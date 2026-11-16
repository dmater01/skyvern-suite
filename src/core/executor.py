import asyncio
import os
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from src.core.artifacts import ArtifactManager, current_task_id_var
from src.core.cdp import check_cdp_available, resolve_effective_cdp_url
from src.core.client import (
    Skyvern,
    get_skyvern,
    release_cdp_session_handles,
    safe_aclose,
)
from src.core.webhook import deliver_webhook


async def run_autonomous_action(
    url: str,
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    task_record: Optional[Any] = None,
    task_id: Optional[str] = None,
    cdp_url: Optional[str] = None,
    start_fresh_browser: Optional[bool] = None,
    max_steps: Optional[int] = None,
    run_with: Optional[str] = None,
) -> dict:
    """Dispatches an end-to-end autonomous action to Skyvern and waits for completion."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    model = os.getenv("LLM_KEY", "gpt-6.1-terra")
    # If model does not contain a slash or provider prefix, ensure LiteLLM routes it to OpenAI:
    if (
        "/" not in model
        and not model.startswith("openai/")
        and not model.startswith("OPENAI_")
        and not model.startswith("OLLAMA")
        and not model.startswith("ANTHROPIC_")
        and not model.startswith("GEMINI_")
    ):
        model = f"openai/{model}"

    effective_cdp_url = resolve_effective_cdp_url(cdp_url)
    run_kwargs: Dict[str, Any] = {
        "url": url,
        "prompt": prompt,
        "data_extraction_schema": schema,
        "model": model,
        "wait_for_completion": True,
    }
    if effective_cdp_url:
        cdp_ok, _ = await check_cdp_available(effective_cdp_url)
        if not cdp_ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"CDP endpoint '{effective_cdp_url}' is unreachable. "
                    "Start Chrome with remote debugging enabled (e.g. ./scripts/launch_debug_chrome.sh)."
                ),
            )
        run_kwargs["browser_address"] = effective_cdp_url
    if max_steps is not None:
        run_kwargs["max_steps"] = max_steps
    if run_with is not None:
        run_kwargs["run_with"] = run_with

    skyvern = get_skyvern(Skyvern)
    result_payload: Optional[Dict[str, Any]] = None
    error_msg: Optional[str] = None
    try:
        task = await skyvern.run_task(**run_kwargs)
        if hasattr(task, "model_dump"):
            result_payload = task.model_dump()
        elif hasattr(task, "dict"):
            result_payload = task.dict()
        else:
            result_payload = task
        return result_payload
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        effective_task_id = (
            task_id
            or current_task_id_var.get()
            or (result_payload.get("run_id") if isinstance(result_payload, dict) else None)
        )
        if effective_task_id:
            ArtifactManager.persist_task_artifacts(
                effective_task_id,
                result_payload
                if isinstance(result_payload, dict)
                else {"status": "failed", "failure_reason": error_msg, "output": None},
            )
        run_id_val = (
            result_payload.get("run_id")
            if isinstance(result_payload, dict)
            else None
        )
        if effective_cdp_url or run_id_val:
            await release_cdp_session_handles(
                run_id=run_id_val,
                cdp_url=effective_cdp_url,
            )
        await safe_aclose(
            skyvern,
            preserve_external_browser=bool(effective_cdp_url),
        )
        if webhook_url:
            callback_payload: Dict[str, Any] = (
                task_record.model_dump()
                if task_record is not None and hasattr(task_record, "model_dump")
                else {
                    "status": "failed" if error_msg else "completed",
                    "result": result_payload,
                    "error": error_msg,
                    "webhook_url": webhook_url,
                }
            )
            asyncio.create_task(
                deliver_webhook(
                    webhook_url=webhook_url,
                    payload=callback_payload,
                    secret=webhook_secret,
                    task_record=task_record,
                )
            )
