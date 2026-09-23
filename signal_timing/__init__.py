"""单交叉口信号配时优化框架（v1.0）。

按设计文档实现的分层结构：
- data：数据层
- constraints：约束描述层
- compiler：约束编译层
- audit：子集语义审计
- model：模型层（scipy.optimize.milp / HiGHS）
- pipeline：字典序目标管线
- ordering：排序后处理层
- optimizer：顶层 LexicographicOptimizer
"""
from .audit import AuditEntry, AuditReport, audit_constraint
from .compiler import CompiledRow, ConstraintCompiler, SlackSpec
from .constraints import ConstraintSpec, Trigger, TriggerType
from .data import IntersectionData, Movement, Phase
from .exceptions import (
    CollapsedConstraintInfeasible,
    ConstraintAuditError,
    ConstraintError,
    DataValidationError,
    InfeasibleError,
    OrderingError,
    PostCheckError,
    SignalTimingError,
)
from .optimizer import LexicographicOptimizer
from .ordering import OrderingPostProcessor, OrderingResult, make_reference_order_filter
from .result import OptimizationResult, RoundTrace, Stage1Result
from .variables import VarKey, VarRegistry

__all__ = [
    "AuditEntry",
    "AuditReport",
    "audit_constraint",
    "CompiledRow",
    "ConstraintCompiler",
    "SlackSpec",
    "ConstraintSpec",
    "Trigger",
    "TriggerType",
    "IntersectionData",
    "Movement",
    "Phase",
    "CollapsedConstraintInfeasible",
    "ConstraintAuditError",
    "ConstraintError",
    "DataValidationError",
    "InfeasibleError",
    "OrderingError",
    "PostCheckError",
    "SignalTimingError",
    "LexicographicOptimizer",
    "OrderingPostProcessor",
    "OrderingResult",
    "make_reference_order_filter",
    "OptimizationResult",
    "RoundTrace",
    "Stage1Result",
    "VarKey",
    "VarRegistry",
]

__version__ = "1.0.0"
