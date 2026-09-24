"""内存任务表与后台求解线程（v1.0）。

设计文档 §6：进程内后台线程 + 内存任务表，单 worker 部署；
全局并发限制 ``MAX_CONCURRENT_SOLVES=1``；任务结果 TTL 30 分钟。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from webapp.adapter.runner import (
    WebValidationError,
    classify_solve_exception,
    result_payload,
    run_optimize_request,
)
from webapp.adapter.schemas import OptimizeRequest

MAX_CONCURRENT_SOLVES = 1
TASK_TTL_SECONDS = 30 * 60


@dataclass
class TaskRecord:
    request: OptimizeRequest
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    stage: str = "queued"
    message: str = "等待执行"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    logs: List[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False
    thread: Optional[threading.Thread] = None

    @property
    def elapsed(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "infeasible", "cancelled"}

    def add_log(self, message: str) -> None:
        self.logs.append(f"{time.strftime('%H:%M:%S')} {message}")
        self.logs = self.logs[-80:]

    def to_status_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": self.elapsed,
            "cancel_requested": self.cancel_requested,
            "error": self.error,
            "logs": list(self.logs),
        }

    def to_result_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "elapsed": self.elapsed,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    """线程安全的内存任务管理器。"""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SOLVES) -> None:
        self._lock = threading.RLock()
        self._tasks: Dict[str, TaskRecord] = {}
        self._semaphore = threading.Semaphore(max(1, int(max_concurrent)))

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def create(self, request: OptimizeRequest) -> TaskRecord:
        self._purge_expired()
        record = TaskRecord(request=request)
        with self._lock:
            self._tasks[record.task_id] = record
        record.add_log("任务已入队")
        record.thread = threading.Thread(
            target=self._run,
            args=(record,),
            name=f"signal-solve-{record.task_id[:8]}",
            daemon=True,
        )
        record.thread.start()
        return record

    def get(self, task_id: str) -> Optional[TaskRecord]:
        self._purge_expired()
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> List[TaskRecord]:
        self._purge_expired()
        with self._lock:
            return list(self._tasks.values())

    def cancel(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            record.cancel_requested = True
            record.cancel_event.set()
            if record.status == "queued":
                record.message = "取消请求已记录，排队任务将不再执行"
                record.add_log("取消请求已记录（排队中）")
            else:
                record.message = "取消请求已记录；单轮求解结束后协作退出"
                record.add_log("取消请求已记录（运行中，轮内不可中断）")
            return record

    # ------------------------------------------------------------------ #
    # 后台执行
    # ------------------------------------------------------------------ #
    def _run(self, record: TaskRecord) -> None:
        acquired = False
        try:
            with self._lock:
                if record.cancel_event.is_set():
                    record.status = "cancelled"
                    record.stage = "cancelled"
                    record.message = "排队期间已取消"
                    record.finished_at = time.time()
                    return
                record.stage = "queued"
                record.message = "等待并发求解槽位"

            self._semaphore.acquire()
            acquired = True

            with self._lock:
                if record.cancel_event.is_set():
                    record.status = "cancelled"
                    record.stage = "cancelled"
                    record.message = "排队期间已取消"
                    record.finished_at = time.time()
                    return
                record.status = "running"
                record.stage = "preparing"
                record.message = "准备求解"
                record.started_at = time.time()
                record.add_log("获得求解槽位，开始执行")

            def on_progress(stage: str, message: str) -> None:
                with self._lock:
                    record.stage = stage
                    record.message = message
                    record.add_log(f"[{stage}] {message}")

            try:
                data, result = run_optimize_request(
                    record.request,
                    progress_callback=on_progress,
                    cancel_event=record.cancel_event,
                )
            except WebValidationError as exc:
                with self._lock:
                    record.status = "failed"
                    record.stage = "validate"
                    record.error = {
                        "code": "VALIDATION_FAILED",
                        "message": str(exc),
                        "detail": {"errors": exc.outcome.error_dicts()},
                    }
                    record.message = "请求校验失败"
                    record.add_log("请求校验失败")
                    record.finished_at = time.time()
                return

            if record.cancel_event.is_set():
                with self._lock:
                    record.status = "cancelled"
                    record.stage = "cancelled"
                    record.message = "任务在求解完成后被取消，结果已丢弃"
                    record.add_log("任务取消，丢弃结果")
                    record.finished_at = time.time()
                return

            payload = result_payload(data, result)
            with self._lock:
                record.status = "succeeded"
                record.stage = "done"
                record.message = f"求解完成：{payload.get('status', 'ok')}"
                record.result = payload
                record.add_log("求解结果已写入任务表")
                record.finished_at = time.time()
        except BaseException as exc:  # noqa: BLE001 - 保证任务线程不静默死亡
            status, error = classify_solve_exception(exc)
            with self._lock:
                record.status = status
                record.stage = "done" if status == "infeasible" else "error"
                record.error = error
                record.message = error.get("message", str(exc))
                record.add_log(f"求解异常：{error.get('code')} {error.get('message')}")
                record.finished_at = time.time()
        finally:
            if acquired:
                self._semaphore.release()

    # ------------------------------------------------------------------ #
    # 清理
    # ------------------------------------------------------------------ #
    def _purge_expired(self) -> None:
        now = time.time()
        with self._lock:
            stale = [
                task_id
                for task_id, record in self._tasks.items()
                if record.terminal
                and record.finished_at is not None
                and now - record.finished_at > TASK_TTL_SECONDS
            ]
            for task_id in stale:
                self._tasks.pop(task_id, None)


task_manager = TaskManager()
