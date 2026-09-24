"""子集语义审计（设计文档 §7）。

混合系数约束最危险的是静默切掉可行域，而不是直接报 infeasible。
本模块对约束涉及的相位集合枚举全部子集，判断每个子集下表达式区间与
右端项的关系，标出"恒违反"子集。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .constraints import ConstraintSpec, TriggerType
from .exceptions import ConstraintError

_STATUS_SAT = "always_satisfied"
_STATUS_VIOLATED = "always_violated"
_STATUS_POSSIBLE = "possible"
_STATUS_INACTIVE = "inactive"

_STATUS_LABEL = {
    _STATUS_SAT: "恒满足",
    _STATUS_VIOLATED: "恒违反",
    _STATUS_POSSIBLE: "可能生效",
    _STATUS_INACTIVE: "未激活（已松弛/丢弃）",
}

_TOL = 1e-9


@dataclass
class AuditEntry:
    subset: Tuple[str, ...]
    status: str          # raw 表达式状态（不考虑触发）
    active: bool         # 该子集是否满足 Trigger 条件
    is_trap: bool        # active 且恒违反
    lo: float
    hi: float
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = _STATUS_LABEL.get(self.status, self.status)


@dataclass
class AuditReport:
    spec_name: str
    subsets: List[AuditEntry] = field(default_factory=list)
    needs_manual_review: bool = False
    note: str = ""

    @property
    def has_always_violated(self) -> bool:
        return any(e.status == _STATUS_VIOLATED for e in self.subsets)

    @property
    def has_trap(self) -> bool:
        return any(e.is_trap for e in self.subsets)

    @property
    def trap_subsets(self) -> List[Tuple[str, ...]]:
        return [e.subset for e in self.subsets if e.is_trap]

    def as_table(self) -> str:
        lines = [f"审计报告: {self.spec_name}"]
        if self.note:
            lines.append(f"  说明: {self.note}")
        if self.needs_manual_review:
            lines.append("  [需人工复核] 约束含非 g 变量，审计只覆盖 g 变量部分")
        if not self.subsets:
            lines.append("  （无 g 变量，未枚举子集）")
            return "\n".join(lines)
        lines.append("  选中子集 | 状态 | 是否触发 | 陷阱?")
        for e in self.subsets:
            subset = "{}" if not e.subset else "{" + ",".join(e.subset) + "}"
            lines.append(
                f"    {subset:>14} | {e.label:>4} | {str(e.active):>5} | {'是' if e.is_trap else '否'}"
            )
        return "\n".join(lines)


def audit_constraint(
    spec: ConstraintSpec,
    g_min: float,
    c_max: float,
    *,
    max_phases: int = 10,
) -> AuditReport:
    """对单个约束执行子集语义审计。

    只有 g 变量参与区间计算；若约束含 C/y/σ 等非 g 变量，
    报告会标记 ``needs_manual_review=True``。
    """
    g_coeffs: Dict[str, float] = spec.g_coeffs
    report = AuditReport(spec_name=spec.name)

    if not g_coeffs:
        report.needs_manual_review = True
        report.note = "约束不含 g 变量，子集审计跳过"
        return report

    report.needs_manual_review = any(kind != "g" for (kind, _) in spec.coeffs)
    notes = []
    if spec.soft:
        notes.append("软约束：恒违反只会产生惩罚，不视为硬性陷阱")
    if report.needs_manual_review:
        notes.append("含非 g 变量，区间结论仅覆盖 g 变量部分，需人工复核")
    empty_entries = [e for e in report.subsets if e.subset == ()]
    if empty_entries and empty_entries[0].status == _STATUS_VIOLATED:
        notes.append("空集恒违反由模型'至少一相'约束兜底，不计为陷阱")
    report.note = "；".join(notes)

    phases = sorted(g_coeffs.keys())
    if len(phases) > max_phases:
        raise ConstraintError(
            f"约束 {spec.name} 涉及 {len(phases)} 个 g 相位，超过子集审计上限 "
            f"{max_phases}；请拆分约束或手动审计"
        )

    b = float(spec.rhs)
    sense = spec.sense
    for r in range(len(phases) + 1):
        for subset in itertools.combinations(phases, r):
            lo = 0.0
            hi = 0.0
            for pid in subset:
                a = g_coeffs[pid]
                if a > 0:
                    lo += a * g_min
                    hi += a * c_max
                else:
                    lo += a * c_max
                    hi += a * g_min
            if sense == "<=":
                if hi <= b + _TOL:
                    status = _STATUS_SAT
                elif lo > b + _TOL:
                    status = _STATUS_VIOLATED
                else:
                    status = _STATUS_POSSIBLE
            elif sense == ">=":
                if lo >= b - _TOL:
                    status = _STATUS_SAT
                elif hi < b - _TOL:
                    status = _STATUS_VIOLATED
                else:
                    status = _STATUS_POSSIBLE
            elif sense == "==":
                if abs(lo - b) <= _TOL and abs(hi - b) <= _TOL:
                    status = _STATUS_SAT
                elif b < lo - _TOL or b > hi + _TOL:
                    status = _STATUS_VIOLATED
                else:
                    status = _STATUS_POSSIBLE
            else:  # pragma: no cover - ConstraintSpec 已校验
                raise ConstraintError(f"未知 sense: {sense}")

            active = spec.trigger.is_active(set(subset))
            entry = AuditEntry(
                subset=tuple(subset),
                status=status,
                active=active,
                # 软约束的"恒违反"只意味着必然付惩罚，不是隐式排除可行域；
                # 空集恒违反由模型内置的"至少一相"约束兜底，通常表示
                # "该约束隐含要求至少选一个相位"，不是静默排除，也不计为陷阱。
                is_trap=bool(
                    active
                    and status == _STATUS_VIOLATED
                    and not spec.soft
                    and len(subset) > 0
                ),
                lo=lo,
                hi=hi,
            )
            report.subsets.append(entry)
    return report
