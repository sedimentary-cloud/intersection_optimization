"""适配层：signal_timing 结果 -> JSON 可序列化结构。"""
from __future__ import annotations

import math
from typing import Any, Dict, List

from signal_timing import IntersectionData, OptimizationResult


def _jsonable(value: Any) -> Any:
    """递归转换为 JSON 可序列化类型。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            return _jsonable(value.item())
        except Exception:  # pragma: no cover - 防御式
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def round_to_dict(trace: Any) -> Dict[str, Any]:
    """RoundTrace -> JSON。"""
    return {
        "name": str(getattr(trace, "name", "")),
        "objective_name": str(getattr(trace, "objective_name", "")),
        "objective_value": _jsonable(getattr(trace, "objective_value", None)),
        "status": str(getattr(trace, "status", "")),
        "message": str(getattr(trace, "message", "")),
        "cycle": _jsonable(getattr(trace, "cycle", None)),
        "waste": _jsonable(getattr(trace, "waste", None)),
        "selected": _jsonable(list(getattr(trace, "selected", []) or [])),
        "locked": _jsonable(dict(getattr(trace, "locked", {}) or {})),
        "mip_gap": _jsonable(getattr(trace, "mip_gap", None)),
    }


def audit_report_to_dict(report: Any) -> Dict[str, Any]:
    """AuditReport -> JSON；status 使用算法包中文 label。"""
    entries = []
    for entry in getattr(report, "subsets", []) or []:
        entries.append(
            {
                "subset": _jsonable(list(getattr(entry, "subset", ()) or ())),
                "status": str(getattr(entry, "label", getattr(entry, "status", ""))),
                "raw_status": str(getattr(entry, "status", "")),
                "active": bool(getattr(entry, "active", False)),
                "trap": bool(getattr(entry, "is_trap", False)),
                "lo": _jsonable(getattr(entry, "lo", None)),
                "hi": _jsonable(getattr(entry, "hi", None)),
            }
        )
    warnings: List[str] = []
    if getattr(report, "note", ""):
        warnings.append(str(report.note))
    if getattr(report, "needs_manual_review", False):
        warnings.append("含非 g 变量，子集结论仅覆盖 g 变量部分，需人工复核")
    trap_subsets = [_jsonable(list(s)) for s in (getattr(report, "trap_subsets", []) or [])]
    return {
        "constraint": str(getattr(report, "spec_name", "")),
        "entries": entries,
        "warnings": warnings,
        "trap_subsets": trap_subsets,
        "needs_manual_review": bool(getattr(report, "needs_manual_review", False)),
    }


def _lane_balance(data: IntersectionData, result: OptimizationResult) -> List[Dict[str, Any]]:
    """由最终配时反算各流向需求/供给/饱和度，纯后处理。"""
    cycle = float(result.cycle)
    selected = set(result.selected)
    rows: List[Dict[str, Any]] = []
    for movement_id, movement in data.movements.items():
        provided = sum(
            float(data.phases[pid].capacity.get(movement_id, 0.0))
            * float(result.greens.get(pid, 0.0))
            for pid in selected
        )
        supply = (provided / cycle) if cycle > 1e-12 else 0.0
        ratio = (movement.demand / supply) if supply > 1e-12 else None
        rows.append(
            {
                "lane": movement_id,
                "demand": float(movement.demand),
                "supply": float(supply),
                "ratio": float(ratio) if ratio is not None else None,
                "provided_service": float(provided),
                "cycle_demand_service": float(movement.demand) * cycle,
                "service_margin": float(provided) - float(movement.demand) * cycle,
            }
        )
    return rows


def _rounds_to_list(result: OptimizationResult) -> List[Dict[str, Any]]:
    return [round_to_dict(trace) for trace in (getattr(result, "rounds", []) or [])]


def result_to_dict(data: IntersectionData, result: OptimizationResult) -> Dict[str, Any]:
    """OptimizationResult -> 前端 OptimizeResult JSON。"""
    order = list(result.order) if result.order is not None else None
    selected = list(result.selected)
    cycle = float(result.cycle)
    clearance = data.order_clearance(order) if order is not None else data.l_bar * len(selected)
    ring_slack = cycle - (sum(float(result.greens.get(p, 0.0)) for p in selected) + clearance)

    order_candidates = []
    if order is not None:
        order_candidates.append(
            {
                "order": order,
                "waste": float(result.waste),
                "lost_total": float(data.order_clearance(order)),
                "note": "算法包公开结果仅暴露最终排序；更多候选排序需后续契约扩展。",
            }
        )

    return {
        "status": str(getattr(result, "status", "optimal")),
        "message": str(getattr(result, "message", "")),
        "cycle": cycle,
        "selected": selected,
        "order": order,
        "greens": {str(k): _jsonable(v) for k, v in (result.greens or {}).items()},
        "waste": float(result.waste),
        "sigmas": {str(k): _jsonable(v) for k, v in (result.sigmas or {}).items()},
        "rounds": _rounds_to_list(result),
        "order_candidates": order_candidates,
        "fallback_cuts": int(result.fallback_cuts),
        "stage1_cycle": _jsonable(getattr(result, "stage1_cycle", None)),
        "stage1_waste": _jsonable(getattr(result, "stage1_waste", None)),
        "audit_reports": [
            audit_report_to_dict(report)
            for report in (getattr(result, "audit_reports", []) or [])
        ],
        "verification": _jsonable(getattr(result, "verification", {}) or {}),
        "lane_balance": _lane_balance(data, result),
        "phase_count": int(getattr(result, "phase_count", len(selected))),
        "cycle_clearance": float(clearance),
        "cycle_slack": float(ring_slack),
    }
