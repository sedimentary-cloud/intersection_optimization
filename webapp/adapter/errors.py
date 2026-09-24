"""适配层异常与校验结果封装。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ValidationIssue:
    path: str
    message: str
    code: str = "VALIDATION_FAILED"

    def as_dict(self) -> Dict[str, str]:
        return {"path": self.path, "message": self.message, "code": self.code}


@dataclass
class ValidationOutcome:
    ok: bool
    data: Any = None
    specs: List[Any] = field(default_factory=list)
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def error_dicts(self) -> List[Dict[str, str]]:
        return [e.as_dict() for e in self.errors]


class TaskCancelled(Exception):
    """任务取消协作信号。"""
