"""约束描述层（设计文档 §5）。

用户面向的接口只有两个结构：
- Trigger：ALWAYS / OR / AND 三分类；
- ConstraintSpec：通用线性组合 x 触发 x 软硬。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, Optional, Sequence, Set, Tuple

from .exceptions import ConstraintError
from .variables import VarKey

_TOL = 1e-12


class TriggerType(str, Enum):
    ALWAYS = "always"
    OR = "or"
    AND = "and"


def _normalize_phases(phases: Iterable[str]) -> Tuple[str, ...]:
    out = []
    for p in phases:
        if isinstance(p, str):
            out.append(p)
        else:  # pragma: no cover - 防御式
            out.extend(_normalize_phases(p))
    if not out:
        raise ConstraintError("触发相位集合不能为空")
    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return tuple(uniq)


@dataclass(frozen=True)
class Trigger:
    """约束触发逻辑。"""

    kind: TriggerType
    phases: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind in (TriggerType.OR, TriggerType.AND):
            if not self.phases:
                raise ConstraintError(f"{self.kind.value} 触发必须给出非空相位集合")
        elif self.kind == TriggerType.ALWAYS and self.phases:
            raise ConstraintError("ALWAYS 触发不能携带相位集合")

    # ------------------------------------------------------------------ #
    # 构造器
    # ------------------------------------------------------------------ #
    @classmethod
    def always(cls) -> "Trigger":
        return cls(TriggerType.ALWAYS, ())

    @classmethod
    def any_of(cls, *phases: str) -> "Trigger":
        return cls(TriggerType.OR, _normalize_phases(phases))

    @classmethod
    def all_of(cls, *phases: str) -> "Trigger":
        return cls(TriggerType.AND, _normalize_phases(phases))

    # ------------------------------------------------------------------ #
    # 语义
    # ------------------------------------------------------------------ #
    def is_active(self, selected: Set[str]) -> bool:
        if self.kind == TriggerType.ALWAYS:
            return True
        if self.kind == TriggerType.OR:
            return any(p in selected for p in self.phases)
        return all(p in selected for p in self.phases)

    @property
    def phase_set(self) -> Set[str]:
        return set(self.phases)

    def __str__(self) -> str:  # pragma: no cover - 日志友好
        if self.kind == TriggerType.ALWAYS:
            return "always()"
        return f"{self.kind.value}({', '.join(self.phases)})"


@dataclass
class ConstraintSpec:
    """统一约束描述。

    coeffs 以 VarKey=(kind, name) 寻址，例如 ("g", "P1")、("C", "")。
    """

    name: str
    coeffs: Dict[VarKey, float]
    sense: str
    rhs: float
    trigger: Trigger = field(default_factory=Trigger.always)
    soft: bool = False
    penalty: float = 0.0
    slack_max: Optional[float] = None
    #: 混合系数 + 非 AND 触发时，必须显式确认才允许编译（设计文档 §5.2/§7）
    confirm_mixed_trigger: bool = False
    #: 审计发现恒违反子集时，允许用户显式确认后继续注册（通用确认开关）
    confirm_audit: bool = False
    #: OR + >= 的显式"至少选一个"语义（设计文档 §5.3 OR 特例）。
    #: 默认 False，即走 big-M 安全路径。
    or_existential: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ConstraintError("约束 name 必须是非空字符串")
        self.sense = str(self.sense).strip()
        if self.sense not in ("<=", ">=", "=="):
            raise ConstraintError(f"约束 {self.name}: sense 必须是 '<=', '>=' 或 '=='")
        if not isinstance(self.coeffs, dict) or not self.coeffs:
            raise ConstraintError(f"约束 {self.name}: coeffs 不能为空")
        clean: Dict[VarKey, float] = {}
        for key, val in self.coeffs.items():
            if not (isinstance(key, tuple) and len(key) == 2):
                raise ConstraintError(f"约束 {self.name}: 非法变量键 {key!r}，应为 (kind, name)")
            v = float(val)
            if not math.isfinite(v):
                raise ConstraintError(f"约束 {self.name}: 系数必须有限，{key} -> {val}")
            if abs(v) > _TOL:
                clean[key] = v
        if not clean:
            raise ConstraintError(f"约束 {self.name}: 所有系数均为 0")
        self.coeffs = clean
        self.rhs = float(self.rhs)
        if self.soft:
            if not (math.isfinite(self.penalty) and self.penalty > 0.0):
                raise ConstraintError(f"软约束 {self.name}: penalty 必须 > 0")
        elif self.penalty != 0.0:
            # 硬约束忽略 penalty，但给出明确提示比静默忽略好
            self.penalty = 0.0
        if self.slack_max is not None:
            self.slack_max = float(self.slack_max)
            if not (math.isfinite(self.slack_max) and self.slack_max > 0.0):
                raise ConstraintError(f"约束 {self.name}: slack_max 必须 > 0")

    # ------------------------------------------------------------------ #
    # 派生属性
    # ------------------------------------------------------------------ #
    @property
    def positive_coeffs(self) -> Dict[VarKey, float]:
        return {k: v for k, v in self.coeffs.items() if v > _TOL}

    @property
    def negative_coeffs(self) -> Dict[VarKey, float]:
        return {k: v for k, v in self.coeffs.items() if v < -_TOL}

    @property
    def has_mixed_coeffs(self) -> bool:
        return bool(self.positive_coeffs) and bool(self.negative_coeffs)

    @property
    def g_coeffs(self) -> Dict[str, float]:
        """{相位 id: 绿灯系数}，忽略非 g 变量。"""
        out: Dict[str, float] = {}
        for (kind, name), val in self.coeffs.items():
            if kind == "g":
                out[name] = out.get(name, 0.0) + val
        return out

    @property
    def phase_set(self) -> Set[str]:
        """约束涉及的相位集合：g/y 变量键 + 触发集合。"""
        out: Set[str] = set(self.trigger.phases)
        for (kind, name) in self.coeffs:
            if kind in ("g", "y"):
                out.add(name)
        return out

    def describe(self) -> str:  # pragma: no cover - 日志友好
        terms = " + ".join(f"{v:+.4g}*{k[0]}:{k[1]}" for k, v in self.coeffs.items())
        return f"{self.name}: {terms} {self.sense} {self.rhs:+.4g} [{self.trigger}]"
