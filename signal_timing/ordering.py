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


def _normalize_reference_groups(reference_order) -> Tuple[Tuple[str, ...], ...]:
    groups = []
    for item in reference_order:
        if isinstance(item, str):
            group = (item,)
        else:
            group = tuple(str(p) for p in item)
        if not group:
            raise OrderingError("reference_order 中存在空分组")
        groups.append(group)
    return tuple(groups)


def make_reference_order_filter(reference_order) -> OrderFilter:
    """根据分层参考顺序生成 order_filter。

    ``reference_order`` 可以是扁平序列，也可以是嵌套分组：
    ``["P1", ("P5", "P6"), "P3", ("P2", "P4")]``。

    规则：``order`` 中每个相位的层级索引必须非递减；同层相位可互换。
    """
    groups = _normalize_reference_groups(reference_order)
    tier = {p: i for i, group in enumerate(groups) for p in group}

    def order_filter(order):
        last = -1
        for p in order:
            if p not in tier:
                return False
            if tier[p] < last:
                return False
            last = tier[p]
        return True

    return order_filter


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
        reference_order: Optional[Sequence[str]] = None,
        reference_mode: str = "prefer",
        reference_tolerance: float = 1e-3,
    ) -> Optional[OrderingResult]:
        """返回最优排序结果；所有合法排序都不可行时返回 None。

        reference_mode：
        - ``none``：忽略参考顺序；
        - ``prefer``：先尝试与参考顺序一致的顺序，不可行再回退到全枚举；
        - ``hard``：只允许与参考顺序一致的顺序。
        """
        if reference_mode not in ("none", "prefer", "soft", "hard"):
            raise OrderingError(
                "reference_mode 必须是 'none' / 'prefer' / 'soft' / 'hard'"
            )
        selected = list(dict.fromkeys(selected))
        if not selected:
            raise OrderingError("selected 相位集不能为空")
        missing = [p for p in selected if p not in self.data.phases]
        if missing:
            raise OrderingError(f"selected 含未知相位: {missing}")

        selected_sorted = sorted(selected)
        ref = reference_order if reference_order is not None else self.data.reference_order
        if ref is not None and reference_mode != "none":
            ref = self._normalize_reference_order(ref)

        # ---- hard 模式：只允许分层参考顺序一致的顺序 ----
        if ref is not None and reference_mode == "hard":
            best: Optional[OrderingResult] = None
            for order in self._reference_consistent_orders(selected_sorted, ref):
                if order_filter is not None and not order_filter(order):
                    continue
                res = self._solve_order(order, float(cycle_upper), cycle_fixed=cycle_fixed)
                if res is None:
                    continue
                if best is None or res.objective < best.objective - 1e-12:
                    best = res
            if best is None:
                return None
            best.message = f"hard reference order {best.order}; reference={ref}"
            return best

        # ---- none / soft / prefer：全枚举；soft 用参考顺序做次级偏好 ----
        best: Optional[OrderingResult] = None
        best_deviation = 0
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
            deviation = 0
            if ref is not None and reference_mode in ("soft", "prefer"):
                deviation = self._reference_deviation(order, ref)
            if best is None:
                best = res
                best_deviation = deviation
                continue
            # 目标函数显著更优 -> 替换（主目标优先）
            tol = abs(best.objective) * reference_tolerance + 1e-9
            if res.objective < best.objective - tol:
                best = res
                best_deviation = deviation
            # 目标函数在容差内 -> 用参考偏差做次级排序
            elif (
                res.objective <= best.objective + tol
                and deviation < best_deviation
            ):
                best = res
                best_deviation = deviation
        if best is None:
            return None
        best.message = (
            f"permutations total={math.factorial(len(selected))}, considered={considered}, "
            f"feasible={feasible_count}, selected={best.order}, "
            f"reference_mode={reference_mode}" +
            (f", reference_deviation={best_deviation}" if ref is not None else "")
        )
        return best

    # ------------------------------------------------------------------ #
    # 参考顺序辅助
    # ------------------------------------------------------------------ #
    def _normalize_reference_order(self, reference_order) -> Tuple[Tuple[str, ...], ...]:
        groups = _normalize_reference_groups(reference_order)
        flat = [p for group in groups for p in group]
        if len(flat) != len(set(flat)):
            raise OrderingError("reference_order 中存在重复相位")
        unknown = [p for p in flat if p not in self.data.phases]
        if unknown:
            raise OrderingError(f"reference_order 引用了未知相位: {unknown}")
        missing = [p for p in self.data.phase_ids if p not in flat]
        groups = groups + tuple((p,) for p in missing)
        return groups

    @staticmethod
    def _reference_deviation(order: Sequence[str], reference_groups) -> int:
        """相对分层参考顺序的层级逆序对数。"""
        tier = {
            p: i
            for i, group in enumerate(reference_groups)
            for p in group
        }
        seq = [tier[p] for p in order if p in tier]
        inversions = 0
        for i in range(len(seq)):
            for j in range(i + 1, len(seq)):
                if seq[i] > seq[j]:
                    inversions += 1
        return inversions

    @staticmethod
    def _reference_consistent_orders(
        selected_sorted: Sequence[str], reference_groups
    ) -> List[Tuple[str, ...]]:
        """枚举所有与分层参考顺序一致的顺序（层内任意排列）。"""
        selected_set = set(selected_sorted)
        group_lists: List[List[str]] = []
        for group in reference_groups:
            inside = [p for p in group if p in selected_set]
            if inside:
                group_lists.append(inside)
        known = {p for group in reference_groups for p in group}
        for p in selected_sorted:
            if p not in known:
                group_lists.append([p])
        if not group_lists:
            return [tuple(selected_sorted)]
        perms_per_group = [list(itertools.permutations(group)) for group in group_lists]
        orders: List[Tuple[str, ...]] = []
        for combo in itertools.product(*perms_per_group):
            orders.append(tuple(p for perm in combo for p in perm))
        # 去重并保持稳定顺序
        seen = set()
        unique = []
        for order in orders:
            if order not in seen:
                seen.add(order)
                unique.append(order)
        return unique

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
