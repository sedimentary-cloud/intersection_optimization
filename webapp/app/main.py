"""FastAPI 应用入口。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import __version__
from webapp.adapter.audit import audit_request
from webapp.adapter.errors import ValidationIssue
from webapp.adapter.runner import validate_request
from webapp.adapter.schemas import AuditRequest, OptimizeRequest
from webapp.app.tasks import task_manager

BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="信号配时优化工具",
    version=__version__,
    description="非侵入式 Web 可视化层：只调用 signal_timing 公开接口。",
)


def _validation_error_payload(exc: RequestValidationError) -> dict:
    errors = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        errors.append(
            {
                "path": location,
                "message": item.get("msg", "参数校验失败"),
                "code": item.get("type", "VALIDATION_FAILED"),
            }
        )
    return {"errors": errors}


def _error_response(status_code: int, code: str, message: str, detail: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "detail": detail or {},
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    # /api/validate 的语义是"返回校验结果"，即使请求结构错误也用 200 返回 ok=False。
    if request.url.path == "/api/validate":
        payload = _validation_error_payload(exc)
        return JSONResponse(
            status_code=200,
            content={"ok": False, "errors": payload["errors"], "warnings": []},
        )
    payload = _validation_error_payload(exc)
    return _error_response(
        422,
        "VALIDATION_FAILED",
        "请求体结构或字段类型不合法",
        {"errors": payload["errors"]},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return _error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "signal-timing-web",
        "version": __version__,
        "task_count": len(task_manager.list()),
    }


@app.post("/api/validate")
def validate_endpoint(payload: OptimizeRequest) -> dict:
    """仅校验路口数据 + 约束声明，不求解。"""
    outcome = validate_request(payload)
    data_summary = None
    if outcome.data is not None:
        data_summary = {
            "movement_count": len(outcome.data.movements),
            "phase_count": len(outcome.data.phases),
            "constraint_count": len(outcome.specs),
            "l_bar": outcome.data.l_bar,
            "l_min": outcome.data.l_min,
        }
    return {
        "ok": outcome.ok,
        "errors": outcome.error_dicts(),
        "warnings": outcome.warnings,
        "summary": data_summary,
    }


@app.post("/api/audit")
def audit_endpoint(payload: AuditRequest) -> dict:
    """对单条约束做子集语义审计。"""
    try:
        report = audit_request(payload)
    except Exception as exc:  # noqa: BLE001 - 审计也属于用户输入校验
        return {
            "ok": False,
            "report": None,
            "errors": [ValidationIssue(path="constraint", message=str(exc)).as_dict()],
        }
    return {"ok": True, "report": report, "errors": []}


@app.post("/api/optimize", status_code=202)
def optimize_endpoint(payload: OptimizeRequest):
    """先校验，再创建异步求解任务。"""
    outcome = validate_request(payload)
    if not outcome.ok:
        return _error_response(
            422,
            "VALIDATION_FAILED",
            "请求校验失败，未创建任务",
            {"errors": outcome.error_dicts(), "warnings": outcome.warnings},
        )
    record = task_manager.create(payload)
    return {
        "task_id": record.task_id,
        "status": record.status,
        "stage": record.stage,
        "message": record.message,
    }


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    record = task_manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在或已过期")
    return record.to_status_dict()


@app.get("/api/tasks/{task_id}/result")
def get_task_result(task_id: str):
    record = task_manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在或已过期")
    if record.status == "succeeded":
        return record.to_result_dict()
    if record.status in {"failed", "infeasible", "cancelled"}:
        return _error_response(
            409,
            record.error.get("code", "TASK_NOT_SUCCEEDED") if record.error else "TASK_NOT_SUCCEEDED",
            record.error.get("message", record.message) if record.error else record.message,
            record.error.get("detail") if record.error else {},
        )
    return _error_response(
        409,
        "TASK_NOT_READY",
        f"任务状态为 {record.status}，结果尚不可用",
        {"status": record.status, "stage": record.stage},
    )


@app.delete("/api/tasks/{task_id}")
def cancel_task(task_id: str):
    record = task_manager.cancel(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在或已过期")
    return record.to_status_dict()


# 静态前端由 FastAPI 同源托管；必须放在 API 路由之后。
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
