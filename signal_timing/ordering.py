"""排序后处理层（设计文档 §9）。

第一阶段选出无序相位集后，本层：
1. 枚举 S* 的合法排列；
2. 环约束改用真实清空时间；
3. 固定 y、触发坍缩后重解 LP；
4. 取浪费最小的顺序与配时。
"""
from __future__ import annotations

import itertools
import math
import warnings
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from .compiler import CompiledRow, ConstraintCompiler, SlackSpec
from .constraints import ConstraintSpec
from .data import IntersectionData
from .exceptions import CollapsedConstraintInfeasible, OrderingError
from .variables import VarKey, VarRegistry

OrderFilter = Callable[[Tuple[str, ...]], bool]


@dataclass
class OrderingResult:
    order: List[str]
    cycle: float
    greens: Dict[str, float]
    waste: float
    objective: float
    sigmas: Dict[str, float] = field(default_factory=dict)
    status: str = "optimal"
    message: str = ""
    #: 内部使用：完整变量取值，用于后验校验
    values: Optional[Dict[VarKey, float]] = None


class OrderingPostProcessor:
    """第二阶段：枚举排序 + LP 重解。"""

    def __init__(
        self,
        data: IntersectionData,
        specs: Optional[Sequence[ConstraintSpec]] = None,
        *,
        time_limit: float = 300.0,
        disp: bool = False,
    ) -> None:
        self.data = data
        self.specs: List[ConstraintSpec] = list(specs or [])
        self.time_limit = float(time_limit)
        self.disp = bool(disp)

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def process(
        self,
        selected: Sequence[str],
        cycle_upper: float,
        *,
        cycle_fixed: bool = False,
        order_filter: Optional[OrderFilter] = None,
    ) -> Optional[OrderingResult]:
        """返回最优排序结果；所有合法排序都不可行时返回 None。"""
        selected = list(dict.fromkeys(selected))
        if not selected:
            raise OrderingError("selected 相位集不能为空")
        missing = [p for p in selected if p not in self.data.phases]
        if missing:
            raise OrderingError(f"selected 含未知相位: {missing}")

        selected_sorted = sorted(selected)
        best: Optional[OrderingResult] = None
        feasible_count = 0
        considered = 0
        for order in itertools.permutations(selected_sorted):
            if order_filter is not None and not order_filter(order):
                continue
            considered += 1
            res = self._solve_order(order, float(cycle_upper), cycle_fixed=cycle_fixed)
            if res is None:
                continue
            feasible_count += 1
            if best is None or res.objective < best.objective - 1e-12:
                best = res
        if best is None:
            return None
        best.message = (
            f"permutations total={math.factorial(len(selected))}, considered={considered}, "
            f"feasible={feasible_count}, selected={best.order}"
        )
        return best

    # ------------------------------------------------------------------ #
    # 单个排序的 LP
    # ------------------------------------------------------------------ #
    def _solve_order(
        self,
        order: Tuple[str, ...],
        cycle_upper: float,
        *,
        cycle_fixed: bool,
    ) -> Optional[OrderingResult]:
        d = self.data
        registry = VarRegistry()
        for pid in order:
            registry.register(("g", pid), d.g_min, d.c_max, integrality=0, kind="g")
        if cycle_fixed:
            registry.register(("C", ""), cycle_upper, cycle_upper, integrality=0, kind="C")
        else:
            registry.register(("C", ""), d.c_min, cycle_upper, integrality=0, kind="C")

        # 固定 y 以及未选中相位的 g=0
        fixed_values: Dict[VarKey, float] = {}
        for pid in d.phase_ids:
            fixed_values[("y", pid)] = 1.0 if pid in order else 0.0
        for pid in d.phase_ids:
            if pid not in order:
                fixed_values[("g", pid)] = 0.0

        compiler = ConstraintCompiler(
            registry.get_bound,
            d.c_max,
            default_slack_max=d.c_max,
        )
        try:
            user_rows, slack_specs = compiler.compile_all(self.specs, fixed_values=fixed_values)
        except CollapsedConstraintInfeasible:
            return None

        for slack in slack_specs:
            registry.register(slack.key, slack.lb, slack.ub, integrality=0, kind="sigma")

        rows: List[CompiledRow] = []
        rows.extend(user_rows)

        # 真实清空时间的环约束：sum g + sum l(order_k, order_{k+1}) <= C
        clearance = d.order_clearance(order)
        ring_coeffs: Dict[VarKey, float] = {("g", pid): 1.0 for pid in order}
        ring_coeffs[("C", "")] = -1.0
        rows.append(
            CompiledRow(
                name="ring_real_clearance",
                coeffs=ring_coeffs,
                sense="<=",
                rhs=-clearance,
                note=f"真实清空时间 {clearance:.4g}s",
            )
        )
        # 服务能力
        for mid, mov in d.movements.items():
            coeffs: Dict[VarKey, float] = {("C", ""): -mov.demand}
            for pid in order:
                a = d.phases[pid].capacity.get(mid, 0.0)
                if a > 0.0:
                    coeffs[("g", pid)] = a
            rows.append(
                CompiledRow(
                    name=f"service[{mid}]",
                    coeffs=coeffs,
                    sense=">=",
                    rhs=0.0,
                )
            )

        # 目标：min 浪费 + 软约束惩罚
        objective: Dict[VarKey, float] = {}
        for pid in order:
            objective[("g", pid)] = sum(d.phases[pid].capacity.values())
        objective[("C", "")] = -sum(mov.demand for mov in d.movements.values())
        for slack in slack_specs:
            objective[slack.key] = objective.get(slack.key, 0.0) + slack.penalty

        return self._solve_lp(
            registry,
            rows,
            objective,
            slack_specs,
            order=list(order),
        )

    # ------------------------------------------------------------------ #
    # LP 装配/求解
    # ------------------------------------------------------------------ #
    def _solve_lp(
        self,
        registry: VarRegistry,
        rows: Sequence[CompiledRow],
        objective: Dict[VarKey, float],
        slack_specs: Sequence[SlackSpec],
        *,
        order: List[str],
    ) -> Optional[OrderingResult]:
        n = len(registry)
        A = lil_matrix((len(rows), n), dtype=float)
        lb = np.empty(len(rows), dtype=float)
        ub = np.empty(len(rows), dtype=float)
        for r, row in enumerate(rows):
            if not row.coeffs:
                raise OrderingError(f"LP 行 {row.name} 不含变量")
            for key, val in row.coeffs.items():
                A[r, registry.index(key)] += val
            if row.sense == "<=":
                lb[r], ub[r] = -np.inf, row.rhs
            elif row.sense == ">=":
                lb[r], ub[r] = row.rhs, np.inf
            elif row.sense == "==":
                lb[r] = ub[r] = row.rhs
            else:
                raise OrderingError(f"未知 sense: {row.sense}")
        A = A.tocsr()
        c = np.zeros(n, dtype=float)
        for key, val in objective.items():
            c[registry.index(key)] += float(val)
        bounds_lb, bounds_ub = registry.bounds_arrays()
        integrality = np.zeros(n, dtype=int)
        res = milp(
            c,
            integrality=integrality,
            bounds=Bounds(bounds_lb, bounds_ub),
            constraints=LinearConstraint(A, lb, ub),
            options={"disp": self.disp, "presolve": True, "time_limit": self.time_limit},
        )
        if res.status == 2 or res.x is None:
            return None
        if res.status == 3:
            raise OrderingError(f"排序 LP 无界: {res.message}")
        if res.status == 1:
            warnings.warn(
                f"排序 LP 达到限制但已有可行解: {res.message}", RuntimeWarning, stacklevel=2
            )
        values = {key: float(res.x[i]) for i, key in enumerate(registry.keys)}
        greens = {pid: values[("g", pid)] for pid in order}
        cycle = values[("C", "")]
        service = sum(
            self.data.phases[pid].capacity.get(mid, 0.0) * greens[pid]
            for pid in order
            for mid in self.data.phases[pid].capacity
        )
        demand = sum(mov.demand for mov in self.data.movements.values()) * cycle
        sigmas: Dict[str, float] = {}
        for slack in slack_specs:
            name = slack.base_name or slack.spec_name
            sigmas[name] = sigmas.get(name, 0.0) + float(values[slack.key])
        return OrderingResult(
            order=order,
            cycle=cycle,
            greens=greens,
            waste=float(service - demand),
            objective=float(res.fun),
            sigmas=sigmas,
            status="optimal" if res.status == 0 else "time_limit",
            message=str(res.message),
            values=values,
        )
