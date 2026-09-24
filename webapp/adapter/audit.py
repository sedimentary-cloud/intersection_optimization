"""单条约束审计适配。"""
from __future__ import annotations

from signal_timing import audit_constraint

from .deserialize import audit_report_to_dict
from .schemas import AuditRequest
from .serialize import build_constraint, build_intersection


def audit_request(request: AuditRequest) -> dict:
    """JSON 审计请求 -> AuditReport JSON。"""
    data = build_intersection(request.intersection)
    spec = build_constraint(request.constraint)
    report = audit_constraint(spec, data.g_min, data.c_max)
    return audit_report_to_dict(report)
