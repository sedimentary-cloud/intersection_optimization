"""前后端 JSON 契约（Pydantic v2）。

字段命名尽量与算法包数据类一致；为兼容设计文档中的 ``lanes`` 叫法，
``IntersectionPayload.movements`` 同时接受 ``movements`` 和 ``lanes``。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MovementPayload(BaseModel):
    """一个流向。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., min_length=1, description="流向唯一 id")
    demand: float = Field(..., gt=0.0, description="需求流量，veh/h")


class PhasePayload(BaseModel):
    """一个候选相位。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., min_length=1, description="相位唯一 id")
    capacity: dict[str, float] = Field(
        default_factory=dict,
        description="流向 -> 饱和流率 a_pm（veh/h），只列正值",
    )


class IntersectionPayload(BaseModel):
    """路口输入数据。"""

    model_config = ConfigDict(populate_by_name=True)

    movements: list[MovementPayload] = Field(
        default_factory=list,
        alias="lanes",  # 设计文档旧版用 lanes，新版算法包用 movements
        description="流向列表；也可用 ``lanes`` 字段名提交",
    )
    phases: list[PhasePayload] = Field(default_factory=list)
    lost_time: list[tuple[str, str, float]] = Field(
        default_factory=list,
        description="相邻相位清空时间 [from, to, seconds]",
    )
    g_min: float = 15.0
    c_min: float = 30.0
    c_max: float = 180.0
    strict_clearance: bool = True
    static_precheck: bool = True
    reference_order: Optional[list[Any]] = Field(
        default=None,
        description="分层参考顺序；扁平字符串或嵌套相位组列表",
    )


class TriggerPayload(BaseModel):
    """约束触发逻辑。"""

    kind: Literal["always", "or", "and"] = "always"
    phases: list[str] = Field(default_factory=list)


class ConstraintPayload(BaseModel):
    """用户约束声明。"""

    name: str = Field(..., min_length=1)
    #: 每项形如 [kind, name, coefficient]，kind ∈ {"g", "y", "C"}。
    coeffs: list[tuple[str, str, float]] = Field(..., min_length=1)
    sense: Literal["<=", ">=", "=="] = ">="
    rhs: float = 0.0
    trigger: TriggerPayload = Field(default_factory=TriggerPayload)
    soft: bool = False
    penalty: float = 0.0
    slack_max: Optional[float] = None
    confirm_mixed_trigger: bool = False
    confirm_audit: bool = False
    or_existential: bool = False


class SolverConfig(BaseModel):
    """求解参数，对应 LexicographicOptimizer + solve() 的公开参数。"""

    fixed_cycle: Optional[float] = Field(None, gt=0.0)
    eps_cycle: float = Field(0.01, ge=0.0)
    eps_waste: float = Field(0.01, ge=0.0)
    waste_abs_tol: Optional[float] = Field(None, ge=0.0, description="映射到 delta_abs")
    time_limit: float = Field(300.0, gt=0.0)
    mip_rel_gap: float = Field(0.005, ge=0.0)
    reference_order: Optional[list[Any]] = None
    reference_order_mode: Literal["off", "hard", "soft", "prefer"] = "off"
    order_rules: list[dict[str, Any]] = Field(default_factory=list)
    run_ordering: bool = True
    allow_cycle_reduction: bool = True
    enforce_zero_slack: bool = False
    disp: bool = False
    max_fallback: int = Field(20, ge=0)


class OptimizeRequest(BaseModel):
    """提交求解 / 仅校验的统一请求体。"""

    intersection: IntersectionPayload
    constraints: list[ConstraintPayload] = Field(default_factory=list)
    solver: SolverConfig = Field(default_factory=SolverConfig)


class AuditRequest(BaseModel):
    """单条约束审计请求。"""

    intersection: IntersectionPayload
    constraint: ConstraintPayload
