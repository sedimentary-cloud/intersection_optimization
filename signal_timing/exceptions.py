"""框架统一异常类型。"""
from __future__ import annotations

from typing import Dict, Optional


class SignalTimingError(Exception):
    """所有框架异常的基类。"""


class DataValidationError(SignalTimingError):
    """数据层校验失败。"""


class ConstraintError(SignalTimingError):
    """约束描述/编译错误。"""


class ConstraintAuditError(ConstraintError):
    """子集语义审计发现恒违反陷阱，且用户未显式确认。"""

    def __init__(self, message: str, report=None):
        super().__init__(message)
        self.report = report


class CollapsedConstraintInfeasible(ConstraintError):
    """触发坍缩后约束退化为恒违反的常数行，表示当前固定选择不可行。"""


class InfeasibleError(SignalTimingError):
    """求解阶段不可行。"""

    def __init__(
        self,
        message: str,
        round_name: Optional[str] = None,
        locked_constraints: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.round_name = round_name
        self.locked_constraints = locked_constraints or {}


class PostCheckError(SignalTimingError):
    """求解完成后后验校验失败。"""


class OrderingError(SignalTimingError):
    """排序后处理发生不可恢复错误。"""
