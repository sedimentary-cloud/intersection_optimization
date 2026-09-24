"""结果对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RoundTrace:
    """字典序一轮的轨迹。"""

    name: str
    objective_name: str
    objective_value: Optional[float]
    status: str
    message: str
    cycle: Optional[float] = None
    waste: Optional[float] = None
    selected: List[str] = field(default_factory=list)
    locked: Dict[str, str] = field(default_factory=dict)
    mip_gap: Optional[float] = None


@dataclass
class Stage1Result:
    """第一阶段（无序相位选择 + 配时）结果。"""

    cycle: float
    selected: List[str]
    greens: Dict[str, float]
    waste: float
    sigmas: Dict[str, float]
    rounds: List[RoundTrace]
    values: Dict[Tuple[str, str], float]
    cycle_cap: Optional[float] = None
    model: Any = None


@dataclass
class OptimizationResult:
    """顶层最终结果。"""

    cycle: float
    selected: List[str]
    greens: Dict[str, float]
    order: Optional[List[str]]
    waste: float
    sigmas: Dict[str, float]
    rounds: List[RoundTrace] = field(default_factory=list)
    fallback_cuts: int = 0
    stage1_cycle: Optional[float] = None
    stage1_waste: Optional[float] = None
    audit_reports: List[Any] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    status: str = "optimal"
    message: str = ""

    @property
    def phase_count(self) -> int:
        return len(self.selected)

    @property
    def selected_set(self):
        return set(self.selected)
