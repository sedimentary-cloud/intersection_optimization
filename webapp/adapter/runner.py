"""求解执行器：校验、调用公开求解接口、异常诊断映射。

本模块不修改算法包；所有进度回调都发生在适配层调度点。
受算法包公开 API 限制，当前高成本求解段无法在轮内回调。前端会先显示
``solving`` 阶段，求解完成后再展示 rounds 细节；这是 §5.4 的降级实现。
"""
from __future__ import annotations

import threading
import warnings
from typing import Any, Callable, Optional, Sequence

from signal_timing import (
    ConstraintAuditError,
    ConstraintCompiler,
    ConstraintError,
    DataValidationError,
    InfeasibleError,
    IntersectionData,
    OrderingError,
    PostCheckError,
    SignalTimingError,
    VarRegistry,
)

from .deserialize import result_to_dict
from .errors import TaskCancelled, ValidationIssue, ValidationOutcome
from .schemas import OptimizeRequest, SolverConfig
from .serialize import (
    build_constraint,
    build_constraints,
    build_intersection,
    build_optimizer,
    compile_order_rules,
    reference_mode_to_ordering_mode,
)

ProgressCallback = Callable[[str, str], None]


class WebValidationError(Exception):
    """适配层校验失败，携带结构化 errors。"""

    def __init__(self, outcome: ValidationOutcome) -> None:
        self.outcome = outcome
        message = "; ".join(e.message for e in outcome.errors) or "请求校验失败"
        super().__init__(message)


def _spec_path(constraints: Sequence[Any], spec_or_name: Any) -> str:
    name = getattr(spec_or_name, "name", spec_or_name)
    for index, payload in enumerate(constraints):
        if getattr(payload, "name", None) == name:
            return f"constraints[{index}]"
    return "constraints"


def _validate_compilable(
    data: IntersectionData,
    specs: Sequence[Any],
) -> list[ValidationIssue]:
    """用算法包公开编译器检查变量引用是否已注册。

    ``ConstraintSpec.__post_init__`` 只做结构校验，不检查 ('g', 'P1') 之类
    变量是否真实存在；编译器会给出 "约束引用了未注册变量" 错误。这里复用该
    公开编译器，保证 /api/validate 能在创建任务前发现此类输入错误。
    """
    registry = VarRegistry()
    for phase_id in data.phase_ids:
        registry.register(("y", phase_id), 0.0, 1.0, kind="y")
        registry.register(("g", phase_id), 0.0, data.c_max, kind="g")
    registry.register(("C", ""), 0.0, data.c_max, kind="C")

    compiler = ConstraintCompiler(
        registry.get_bound,
        data.c_max,
        default_slack_max=data.c_max,
    )
    issues: list[ValidationIssue] = []
    for index, spec in enumerate(specs):
        try:
            compiler.compile(spec)
        except Exception as exc:  # noqa: BLE001 - 统一转成结构化字段错误
            issues.append(
                ValidationIssue(
                    path=f"constraints[{index}]",
                    message=str(exc),
                    code="CONSTRAINT_INVALID",
                )
            )
    return issues


def validate_request(request: OptimizeRequest) -> ValidationOutcome:
    """构造算法包对象并复用其加载审计，返回结构化校验结果。"""
    try:
        data = build_intersection(request.intersection)
    except Exception as exc:  # noqa: BLE001 - 需要把所有加载错误转成响应
        return ValidationOutcome(
            ok=False,
            errors=[ValidationIssue(path="intersection", message=str(exc))],
        )

    specs = []
    errors = []
    for index, payload in enumerate(request.constraints):
        try:
            specs.append(build_constraint(payload))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                ValidationIssue(
                    path=f"constraints[{index}]",
                    message=str(exc),
                    code="CONSTRAINT_INVALID",
                )
            )
    if errors:
        return ValidationOutcome(ok=False, data=data, specs=specs, errors=errors)

    compile_errors = _validate_compilable(data, specs)
    if compile_errors:
        return ValidationOutcome(
            ok=False,
            data=data,
            specs=specs,
            errors=compile_errors,
        )

    captured = []
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            build_optimizer(data, specs, request.solver)
            captured = [str(item.message) for item in caught]
    except ConstraintAuditError as exc:
        report = getattr(exc, "report", None)
        name = getattr(report, "spec_name", None)
        errors.append(
            ValidationIssue(
                path=_spec_path(request.constraints, name),
                message=str(exc),
                code="CONSTRAINT_AUDIT_TRAP",
            )
        )
    except Exception as exc:  # noqa: BLE001 - 算法包所有校验异常
        errors.append(
            ValidationIssue(
                path="constraints" if request.constraints else "solver",
                message=str(exc),
                code="VALIDATION_FAILED",
            )
        )

    return ValidationOutcome(
        ok=not errors,
        data=data,
        specs=specs,
        errors=errors,
        warnings=captured,
    )


def _progress(callback: Optional[ProgressCallback], stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)


def solve_with_data(
    data: IntersectionData,
    specs: Sequence[Any],
    solver_config: SolverConfig,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Any:
    """在已校验数据上调用 LexicographicOptimizer.solve()。"""
    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelled()

    _progress(progress_callback, "solving", "构建 MILP 模型并求解")
    optimizer = build_optimizer(data, specs, solver_config)
    order_filter = compile_order_rules(
        solver_config.order_rules,
        phase_ids=data.phase_ids,
    )
    reference_order = solver_config.reference_order
    reference_mode = reference_mode_to_ordering_mode(solver_config.reference_order_mode)

    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelled()

    result = optimizer.solve(
        fixed_cycle=solver_config.fixed_cycle,
        order_filter=order_filter,
        run_ordering=solver_config.run_ordering,
        allow_cycle_reduction=solver_config.allow_cycle_reduction,
        reference_order=reference_order,
        reference_mode=reference_mode,
        enforce_zero_slack=solver_config.enforce_zero_slack,
        stage2_mode=solver_config.stage2_mode,
    )

    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelled()
    _progress(progress_callback, "done", "求解完成")
    return result


def run_optimize_request(
    request: OptimizeRequest,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[IntersectionData, Any]:
    """完整流程：校验 -> 求解；返回 (data, result)。"""
    outcome = validate_request(request)
    if not outcome.ok:
        raise WebValidationError(outcome)
    _progress(progress_callback, "preparing", "已通过校验，准备求解")
    result = solve_with_data(
        outcome.data,
        outcome.specs,
        request.solver,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )
    return outcome.data, result


def classify_solve_exception(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """算法包异常 -> 任务状态 + 统一错误对象。"""
    if isinstance(exc, TaskCancelled):
        return "cancelled", {
            "code": "CANCELLED",
            "message": "任务已取消",
            "detail": {},
        }
    if isinstance(exc, ConstraintAuditError):
        report = getattr(exc, "report", None)
        return "failed", {
            "code": "CONSTRAINT_AUDIT_TRAP",
            "message": str(exc),
            "detail": {
                "audit": report.as_table() if hasattr(report, "as_table") else str(report),
            },
        }
    if isinstance(exc, (DataValidationError, ConstraintError)):
        return "failed", {
            "code": "VALIDATION_FAILED",
            "message": str(exc),
            "detail": {},
        }
    if isinstance(exc, InfeasibleError):
        return "infeasible", {
            "code": "INFEASIBLE",
            "message": str(exc),
            "detail": {
                "round": getattr(exc, "round_name", None),
                "locked_constraints": getattr(exc, "locked_constraints", {}) or {},
            },
        }
    if isinstance(exc, PostCheckError):
        return "failed", {
            "code": "POST_CHECK_FAILED",
            "message": str(exc),
            "detail": {},
        }
    if isinstance(exc, OrderingError):
        return "failed", {
            "code": "ORDERING_ERROR",
            "message": str(exc),
            "detail": {},
        }
    if isinstance(exc, SignalTimingError):
        return "failed", {
            "code": "SOLVER_ERROR",
            "message": str(exc),
            "detail": {},
        }
    return "failed", {
        "code": "SOLVER_ERROR",
        "message": str(exc),
        "detail": {},
    }


def result_payload(data: IntersectionData, result: Any) -> dict[str, Any]:
    """便捷函数：结果对象 -> JSON。"""
    return result_to_dict(data, result)
