"""模型层（设计文档 §4、§10）。

职责：
- 注册变量并内置基础约束；
- 把编译后的稀疏行装配成 scipy.optimize.milp 输入；
- 求解并做基本状态处理。
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix, lil_matrix

from .compiler import CompiledRow, ConstraintCompiler, SlackSpec
from .constraints import ConstraintSpec, Trigger
from .data import IntersectionData
from .exceptions import ConstraintError, InfeasibleError
from .variables import VarKey, VarRegistry


@dataclass
class MILPSolution:
    values: Dict[VarKey, float]
    fun: float
    status: int
    message: str
    mip_gap: Optional[float] = None
    raw: object = None

    def get(self, key: VarKey, default: float = 0.0) -> float:
        return float(self.values.get(key, default))


def _as_row(item, name: str = "cut") -> CompiledRow:
    if isinstance(item, CompiledRow):
        return item
    if isinstance(item, dict) and {"coeffs", "sense", "rhs"} <= set(item):
        return CompiledRow(
            name=str(item.get("name", name)),
            coeffs=dict(item["coeffs"]),
            sense=item["sense"],
            rhs=float(item["rhs"]),
        )
    if isinstance(item, (tuple, list)) and len(item) >= 3:
        coeffs, sense, rhs = item[0], item[1], item[2]
        return CompiledRow(name=str(item[3]) if len(item) > 3 else name, coeffs=dict(coeffs), sense=sense, rhs=float(rhs))
    raise ConstraintError(f"无法识别的额外割约束: {item!r}")


class Stage1Model:
    """第一阶段 MILP 模型。"""

    def __init__(
        self,
        data: IntersectionData,
        specs: Optional[Sequence[ConstraintSpec]] = None,
        *,
        fixed_cycle: Optional[float] = None,
        cycle_upper: Optional[float] = None,
        ring_clearance: Optional[float] = None,
        extra_cuts: Optional[Sequence] = None,
    ) -> None:
        self.data = data
        self.specs: List[ConstraintSpec] = list(specs or [])
        self.fixed_cycle = float(fixed_cycle) if fixed_cycle is not None else None
        self.cycle_upper = float(cycle_upper) if cycle_upper is not None else None
        self.ring_clearance = (
            float(ring_clearance) if ring_clearance is not None else float(data.l_bar)
        )
        if self.fixed_cycle is not None and self.cycle_upper is not None:
            raise ConstraintError("fixed_cycle 与 cycle_upper 不能同时给出")
        if self.fixed_cycle is not None:
            if not (data.c_min - 1e-9 <= self.fixed_cycle <= data.c_max + 1e-9):
                raise ConstraintError(
                    f"fixed_cycle={self.fixed_cycle} 不在 [{data.c_min}, {data.c_max}] 内"
                )
        if self.cycle_upper is not None:
            if not (data.c_min - 1e-9 <= self.cycle_upper <= data.c_max + 1e-9):
                raise ConstraintError(
                    f"cycle_upper={self.cycle_upper} 不在 [{data.c_min}, {data.c_max}] 内"
                )
        self.registry = VarRegistry()
        self.rows: List[CompiledRow] = []
        self.slack_specs: List[SlackSpec] = []
        self.sigma_penalties: Dict[VarKey, float] = {}
        self._build(extra_cuts or [])

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #
    def _build(self, extra_cuts: Sequence) -> None:
        self._register_base_variables()
        compiler = ConstraintCompiler(
            self.registry.get_bound,
            self.data.c_max,
            default_slack_max=self.data.c_max,
        )
        self._add_base_constraints()
        for spec in self.specs:
            rows, slacks = compiler.compile(spec)
            for slack in slacks:
                self._register_slack(slack)
            self.rows.extend(rows)
        for item in extra_cuts:
            self.add_row(_as_row(item))

    def _register_base_variables(self) -> None:
        for pid in self.data.phase_ids:
            self.registry.register(("y", pid), 0.0, 1.0, integrality=1, kind="y")
            self.registry.register(("g", pid), 0.0, self.data.c_max, integrality=0, kind="g")
        if self.fixed_cycle is not None:
            self.registry.register(
                ("C", ""), self.fixed_cycle, self.fixed_cycle, integrality=0, kind="C"
            )
        elif self.cycle_upper is not None:
            self.registry.register(
                ("C", ""), self.data.c_min, self.cycle_upper, integrality=0, kind="C"
            )
        else:
            self.registry.register(
                ("C", ""), self.data.c_min, self.data.c_max, integrality=0, kind="C"
            )

    def _register_slack(self, slack: SlackSpec) -> None:
        self.registry.register(
            slack.key,
            slack.lb,
            slack.ub,
            integrality=0,
            kind="sigma",
        )
        self.sigma_penalties[slack.key] = slack.penalty
        self.slack_specs.append(slack)

    def _add_base_constraints(self) -> None:
        d = self.data
        # 半连续联动：g_p - c_max*y_p <= 0；g_p - g_min*y_p >= 0
        for pid in d.phase_ids:
            self.rows.append(
                CompiledRow(
                    name=f"link_ub[{pid}]",
                    coeffs={("g", pid): 1.0, ("y", pid): -d.c_max},
                    sense="<=",
                    rhs=0.0,
                    note="半连续上界",
                )
            )
            self.rows.append(
                CompiledRow(
                    name=f"link_lb[{pid}]",
                    coeffs={("g", pid): 1.0, ("y", pid): -d.g_min},
                    sense=">=",
                    rhs=0.0,
                    note="半连续下界",
                )
            )
        # 环约束（保守清空时间 l_bar）
        self.rows.append(
            CompiledRow(
                name="ring",
                coeffs={
                    **{("g", pid): 1.0 for pid in d.phase_ids},
                    **{("y", pid): self.ring_clearance for pid in d.phase_ids},
                    ("C", ""): -1.0,
                },
                sense="<=",
                rhs=0.0,
                note=(
                    f"第一阶段环约束：sum g + {self.ring_clearance:.6g} * sum y <= C"
                ),
            )
        )
        # 服务能力约束
        for mid, mov in d.movements.items():
            coeffs: Dict[VarKey, float] = {("C", ""): -mov.demand}
            for pid, ph in d.phases.items():
                a = ph.capacity.get(mid, 0.0)
                if a > 0.0:
                    coeffs[("g", pid)] = a
            self.rows.append(
                CompiledRow(
                    name=f"service[{mid}]",
                    coeffs=coeffs,
                    sense=">=",
                    rhs=0.0,
                    note="服务能力约束",
                )
            )
        # 至少一相
        self.rows.append(
            CompiledRow(
                name="at_least_one_phase",
                coeffs={("y", pid): 1.0 for pid in d.phase_ids},
                sense=">=",
                rhs=1.0,
                note="防空解",
            )
        )

    # ------------------------------------------------------------------ #
    # 动态添加约束
    # ------------------------------------------------------------------ #
    def add_row(self, row: CompiledRow) -> None:
        if not row.coeffs:
            raise ConstraintError(f"额外行 {row.name} 不含变量")
        for key in row.coeffs:
            self.registry.index(key)
        self.rows.append(row)

    def add_no_good_cut(self, phase_set: Iterable[str]) -> CompiledRow:
        S = list(dict.fromkeys(phase_set))
        if not S:
            raise ConstraintError("no-good cut 的相位集合不能为空")
        for pid in S:
            if pid not in self.data.phases:
                raise ConstraintError(f"no-good cut 引用未知相位 {pid}")
        row = CompiledRow(
            name=f"no_good_cut[{','.join(S)}]",
            coeffs={("y", pid): 1.0 for pid in S},
            sense="<=",
            rhs=float(len(S) - 1),
            note="回退 no-good cut：禁止该相位集",
        )
        self.add_row(row)
        return row

    def add_upper_bound_row(self, key: VarKey, rhs: float, name: str) -> CompiledRow:
        self.registry.index(key)
        row = CompiledRow(name=name, coeffs={key: 1.0}, sense="<=", rhs=float(rhs))
        self.add_row(row)
        return row

    # ------------------------------------------------------------------ #
    # 目标函数
    # ------------------------------------------------------------------ #
    def penalty_objective(self) -> Dict[VarKey, float]:
        return {key: pen for key, pen in self.sigma_penalties.items() if abs(pen) > 0}

    def merged_objective(self, main: Dict[VarKey, float]) -> Dict[VarKey, float]:
        obj = {key: float(v) for key, v in main.items()}
        for key, pen in self.penalty_objective().items():
            obj[key] = obj.get(key, 0.0) + pen
        for key in obj:
            self.registry.index(key)
        return obj

    # ------------------------------------------------------------------ #
    # 求解
    # ------------------------------------------------------------------ #
    def _assemble(self, objective: Dict[VarKey, float]):
        n = len(self.registry)
        rows = self.rows
        A = lil_matrix((len(rows), n), dtype=float)
        lb = np.empty(len(rows), dtype=float)
        ub = np.empty(len(rows), dtype=float)
        for r, row in enumerate(rows):
            if not row.coeffs:
                raise ConstraintError(f"行 {row.name} 不含变量，无法装配")
            for key, val in row.coeffs.items():
                j = self.registry.index(key)
                A[r, j] += val
            if row.sense == "<=":
                lb[r] = -np.inf
                ub[r] = row.rhs
            elif row.sense == ">=":
                lb[r] = row.rhs
                ub[r] = np.inf
            elif row.sense == "==":
                lb[r] = row.rhs
                ub[r] = row.rhs
            else:
                raise ConstraintError(f"未知 sense: {row.sense}")
        A = A.tocsr()
        c = np.zeros(n, dtype=float)
        for key, val in objective.items():
            idx = self.registry.index(key)
            c[idx] += float(val)
        bounds_lb, bounds_ub = self.registry.bounds_arrays()
        integrality = np.asarray(self.registry.integrality_array(), dtype=int)
        return A, lb, ub, c, Bounds(bounds_lb, bounds_ub), integrality

    def solve(
        self,
        main_objective: Dict[VarKey, float],
        *,
        time_limit: float = 300.0,
        mip_rel_gap: float = 0.005,
        disp: bool = False,
    ) -> MILPSolution:
        objective = self.merged_objective(main_objective)
        A, lb, ub, c, bounds, integrality = self._assemble(objective)
        options = {
            "disp": bool(disp),
            "presolve": True,
            "time_limit": float(time_limit),
            "mip_rel_gap": float(mip_rel_gap),
        }
        res = milp(
            c,
            integrality=integrality,
            bounds=bounds,
            constraints=LinearConstraint(A, lb, ub),
            options=options,
        )
        if res.status == 2:
            raise InfeasibleError(
                f"Stage1 模型不可行: {res.message}",
                locked_constraints={"rows": str(len(self.rows))},
            )
        if res.status == 3:
            raise InfeasibleError(f"Stage1 模型无界: {res.message}")
        if res.x is None:
            raise InfeasibleError(f"Stage1 未返回可行解 (status={res.status}): {res.message}")
        if res.status == 1:
            warnings.warn(
                f"Stage1 求解达到限制但已有可行解: {res.message}", RuntimeWarning, stacklevel=2
            )
        values = {key: float(res.x[i]) for i, key in enumerate(self.registry.keys)}
        return MILPSolution(
            values=values,
            fun=float(res.fun),
            status=int(res.status),
            message=str(res.message),
            mip_gap=float(getattr(res, "mip_gap", 0.0) or 0.0),
            raw=res,
        )

    # ------------------------------------------------------------------ #
    # 结果便捷访问
    # ------------------------------------------------------------------ #
    @staticmethod
    def selected_phases(values: Dict[VarKey, float], threshold: float = 0.5) -> List[str]:
        return sorted(pid for (kind, pid), v in values.items() if kind == "y" and v > threshold)

    def selected_greens(self, values: Dict[VarKey, float], threshold: float = 0.5) -> Dict[str, float]:
        return {
            pid: float(values.get(("g", pid), 0.0))
            for pid in self.data.phase_ids
            if values.get(("y", pid), 0.0) > threshold
        }

    def cycle_value(self, values: Dict[VarKey, float]) -> float:
        return float(values.get(("C", ""), 0.0))

    def waste_value(self, values: Dict[VarKey, float]) -> float:
        service = sum(
            ph.capacity.get(mid, 0.0) * values.get(("g", pid), 0.0)
            for pid, ph in self.data.phases.items()
            for mid in ph.capacity
        )
        demand = sum(mov.demand for mov in self.data.movements.values()) * self.cycle_value(values)
        return float(service - demand)
