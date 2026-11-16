import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional, Set
import uuid

from fastapi import FastAPI, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import httpx

from src.api.routes import (
    encode_safe_json_response,
    router as artifacts_router,
    sanitize_json_strings,
)
from src.api.schemas import ExecuteTaskRequest, IntrospectRequest, TaskStatusResponse
from src.config import settings
from src.core.artifacts import ArtifactManager, current_task_id_var
from src.core.cdp import check_cdp_available, resolve_effective_cdp_url
from src.core.client import close_skyvern, init_skyvern
from src.core.executor import run_autonomous_action
from src.core.introspect import introspect_webpage
from src.core.webhook import (
    DEFAULT_INITIAL_BACKOFF,
    DEFAULT_MAX_RETRIES,
    deliver_webhook,
)

logger = logging.getLogger(__name__)

WEBHOOK_MAX_RETRIES = DEFAULT_MAX_RETRIES
WEBHOOK_BASE_DELAY = DEFAULT_INITIAL_BACKOFF


async def dispatch_webhook(
    webhook_url: str,
    payload: Dict[str, Any],
    secret: Optional[str] = None,
    max_retries: int = WEBHOOK_MAX_RETRIES,
    base_delay: float = WEBHOOK_BASE_DELAY,
    task_record: Optional[TaskStatusResponse] = None,
) -> bool:
    """Delivers task completion/failure payload to the configured webhook URL with retries."""
    return await deliver_webhook(
        webhook_url=webhook_url,
        payload=payload,
        secret=secret,
        max_retries=max_retries,
        base_delay=base_delay,
        task_record=task_record,
    )


class AsyncTaskQueue:
    """Asynchronous background task queue decoupling FastAPI endpoints from browser drivers."""

    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue[tuple[str, Callable[[], Awaitable[Dict[str, Any]]]]]] = None
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.tasks: Dict[str, TaskStatusResponse] = {}
        self._webhook_secrets: Dict[str, Optional[str]] = {}
        self._webhook_tasks: Set[asyncio.Task[Any]] = set()

    def _ensure_worker(self) -> asyncio.Queue[tuple[str, Callable[[], Awaitable[Dict[str, Any]]]]]:
        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop or self._queue is None:
            self._loop = current_loop
            self._queue = asyncio.Queue()
            self._worker_task = current_loop.create_task(self._worker_loop(self._queue))
        elif self._worker_task is None or self._worker_task.done():
            self._worker_task = current_loop.create_task(self._worker_loop(self._queue))
        return self._queue

    async def start(self) -> None:
        self._ensure_worker()

    async def stop(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        if self._webhook_tasks:
            for wh_task in list(self._webhook_tasks):
                wh_task.cancel()
            await asyncio.gather(*self._webhook_tasks, return_exceptions=True)
            self._webhook_tasks.clear()
        self._worker_task = None
        self._queue = None
        self._loop = None

    async def enqueue(
        self,
        task_type: str,
        coro_factory: Callable[[], Awaitable[Dict[str, Any]]],
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        cdp_url: Optional[str] = None,
    ) -> TaskStatusResponse:
        task_id = str(uuid.uuid4())
        task_record = TaskStatusResponse(
            task_id=task_id,
            task_type=task_type,
            status="queued",
            webhook_url=webhook_url,
            cdp_url=cdp_url,
            queued_at=datetime.now(timezone.utc).isoformat(),
        )
        self.tasks[task_id] = task_record
        self._webhook_secrets[task_id] = webhook_secret
        queue = self._ensure_worker()
        await queue.put((task_id, coro_factory))
        return task_record

    async def wait_for_all(self) -> None:
        """Waits until all currently queued tasks and webhook dispatches have been processed."""
        if self._queue is not None:
            await self._queue.join()
        if self._webhook_tasks:
            await asyncio.gather(*list(self._webhook_tasks), return_exceptions=True)

    async def _run_webhook_delivery(
        self,
        record: TaskStatusResponse,
        secret: Optional[str],
    ) -> None:
        if not record.webhook_url:
            return
        payload = sanitize_json_strings(jsonable_encoder(record))
        kwargs: Dict[str, Any] = {}
        if secret is not None:
            kwargs["secret"] = secret
        kwargs["task_record"] = record
        try:
            delivered = await dispatch_webhook(
                record.webhook_url,
                payload,
                **kwargs,
            )
        except TypeError:
            # Fallback if dispatch_webhook is replaced by a mock with simpler signature
            delivered = await dispatch_webhook(record.webhook_url, payload)
        record.webhook_delivered = delivered
        if record.webhook_delivery_attempts == 0:
            record.webhook_delivery_attempts = 1
        if record.webhook_delivered_at is None:
            record.webhook_delivered_at = datetime.now(timezone.utc).isoformat()

    async def _worker_loop(
        self,
        queue: asyncio.Queue[tuple[str, Callable[[], Awaitable[Dict[str, Any]]]]],
    ) -> None:
        while True:
            task_id, coro_factory = await queue.get()
            record = self.tasks[task_id]
            secret = self._webhook_secrets.get(task_id)
            record.status = "running"
            record.started_at = datetime.now(timezone.utc).isoformat()
            token = current_task_id_var.set(task_id)
            try:
                result = await coro_factory()
                record.result = sanitize_json_strings(result)
                record.status = "completed"
            except Exception as exc:
                logger.error("Task %s (%s) failed: %s", task_id, record.task_type, exc)
                record.error = sanitize_json_strings(str(exc))
                record.status = "failed"
            finally:
                record.completed_at = datetime.now(timezone.utc).isoformat()
                current_task_id_var.reset(token)

            if record.webhook_url:
                wh_task = asyncio.create_task(self._run_webhook_delivery(record, secret))
                self._webhook_tasks.add(wh_task)
                wh_task.add_done_callback(self._webhook_tasks.discard)
                await wh_task

            queue.task_done()


task_queue = AsyncTaskQueue()


def _maybe_prune_artifacts() -> None:
    auto_prune = os.getenv(
        "AUTO_PRUNE_ARTIFACTS", str(settings.auto_prune_artifacts)
    ).lower() in ("true", "1", "yes")
    if auto_prune:
        retention_days = int(
            os.getenv("ARTIFACT_RETENTION_DAYS", settings.artifact_retention_days)
        )
        ArtifactManager.prune_stale_artifacts(retention_days=retention_days)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _maybe_prune_artifacts()
    init_skyvern()
    await task_queue.start()
    try:
        yield
    finally:
        await task_queue.stop()
        await close_skyvern()
        _maybe_prune_artifacts()


api = FastAPI(
    title="Skyvern Dynamic App Engine",
    version="1.0.0",
    lifespan=lifespan,
)
api.include_router(artifacts_router)


@api.get("/health")
@api.get("/healthz")
def health():
    return {"status": "healthy"}


async def _validate_cdp_if_configured(requested_cdp_url: Optional[str]) -> Optional[str]:
    effective_cdp_url = resolve_effective_cdp_url(requested_cdp_url)
    if effective_cdp_url:
        available, _ = await check_cdp_available(effective_cdp_url)
        if not available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"CDP endpoint '{effective_cdp_url}' is unreachable. "
                    "Launch Chrome with remote debugging (./scripts/launch_debug_chrome.sh)."
                ),
            )
    return effective_cdp_url


@api.post(
    "/api/introspect",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def introspect(req: IntrospectRequest) -> JSONResponse:
    effective_cdp_url = await _validate_cdp_if_configured(req.cdp_url)
    record = await task_queue.enqueue(
        task_type="introspect",
        coro_factory=lambda: introspect_webpage(req.url, req.objective),
        webhook_url=req.webhook_url,
        webhook_secret=req.webhook_secret,
        cdp_url=effective_cdp_url,
    )
    return encode_safe_json_response(record, status_code=status.HTTP_202_ACCEPTED)


@api.post(
    "/api/execute",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute(req: ExecuteTaskRequest) -> JSONResponse:
    effective_cdp_url = await _validate_cdp_if_configured(req.cdp_url)
    clean_prompt = sanitize_json_strings(req.prompt)
    if (
        effective_cdp_url is not None
        or req.start_fresh_browser is not None
        or req.max_steps is not None
        or req.run_with is not None
    ):
        extra_kwargs: Dict[str, Any] = {}
        if effective_cdp_url is not None:
            extra_kwargs["cdp_url"] = effective_cdp_url
        if req.start_fresh_browser is not None:
            extra_kwargs["start_fresh_browser"] = req.start_fresh_browser
        if req.max_steps is not None:
            extra_kwargs["max_steps"] = req.max_steps
        if req.run_with is not None:
            extra_kwargs["run_with"] = req.run_with
        coro_factory = lambda: run_autonomous_action(
            req.url,
            clean_prompt,
            req.extraction_schema,
            **extra_kwargs,
        )
    else:
        coro_factory = lambda: run_autonomous_action(
            req.url, clean_prompt, req.extraction_schema
        )

    record = await task_queue.enqueue(
        task_type="execute",
        coro_factory=coro_factory,
        webhook_url=req.webhook_url,
        webhook_secret=req.webhook_secret,
        cdp_url=effective_cdp_url,
    )
    return encode_safe_json_response(record, status_code=status.HTTP_202_ACCEPTED)


@api.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str) -> JSONResponse:
    record = task_queue.tasks.get(task_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )
    return encode_safe_json_response(record, status_code=status.HTTP_200_OK)
