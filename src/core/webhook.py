import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 1.0
BACKOFF_FACTOR = 2.0


def _json_default(obj: Any) -> str:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def compute_hmac_sha256(body_bytes: bytes, secret: str) -> str:
    """Computes the hex-encoded HMAC-SHA256 digest for a raw request body."""
    digest = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


async def deliver_webhook(
    webhook_url: str,
    payload: Dict[str, Any],
    secret: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_INITIAL_BACKOFF,
    task_record: Optional[Any] = None,
) -> bool:
    """Delivers task completion/failure webhook with HMAC-SHA256 signing and exponential backoff."""
    status_val = str(payload.get("status", "")).lower()
    event_name = "task.failed" if status_val == "failed" else "task.completed"

    body_bytes = json.dumps(payload, default=_json_default).encode("utf-8")
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-Skyvern-Event": event_name,
    }
    if secret:
        headers["X-Skyvern-Signature"] = compute_hmac_sha256(body_bytes, secret)

    attempts = 0
    async with httpx.AsyncClient(timeout=DEFAULT_WEBHOOK_TIMEOUT) as client:
        for attempt in range(1, max_retries + 1):
            attempts = attempt
            if task_record is not None:
                task_record.webhook_delivery_attempts = attempts
            try:
                response = await client.post(
                    webhook_url,
                    content=body_bytes,
                    headers=headers,
                )
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()

                if task_record is not None:
                    task_record.webhook_delivered = True
                    task_record.webhook_delivery_attempts = attempts
                    task_record.webhook_delivered_at = datetime.now(timezone.utc).isoformat()
                return True
            except Exception as exc:
                logger.warning(
                    "Webhook delivery attempt %d/%d to %s failed: %s",
                    attempt,
                    max_retries,
                    webhook_url,
                    exc,
                )
                if attempt < max_retries:
                    backoff_seconds = base_delay * (BACKOFF_FACTOR ** (attempt - 1))
                    await asyncio.sleep(backoff_seconds)

    if task_record is not None:
        task_record.webhook_delivered = False
        task_record.webhook_delivery_attempts = attempts
        task_record.webhook_delivered_at = datetime.now(timezone.utc).isoformat()
    return False
