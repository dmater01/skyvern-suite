#!/usr/bin/env python3
"""Skyvern Synthetic Canary Monitor - Amazon Golden Path.

Executes a non-destructive e-commerce journey over a live Chrome CDP session,
followed by decoupled post-run cart cleanup in a finally block.
Exits 0 on golden path completion; exits 1 on assertion failure, timeout, or
error.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx

# Operational Configuration
SKYVERN_API_BASE = os.getenv("SKYVERN_API_BASE", "http://localhost:8000")
CDP_URL = os.getenv("CDP_URL", "http://localhost:9222")
POLL_INTERVAL_SECONDS = 3
MAX_TIMEOUT_SECONDS = 300  # 5 minutes maximum for visual loop
CLEANUP_TIMEOUT_SECONDS = 120


def log(msg: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [CANARY] {msg}", flush=True)


def check_cdp_health(client: httpx.Client) -> bool:
    """Validate that the host Chrome instance is reachable via CDP."""
    try:
        res = client.get(
            f"{SKYVERN_API_BASE}/api/cdp/status",
            params={"cdp_url": CDP_URL},
            timeout=5.0,
        )
        if res.status_code != 200:
            log(f"CDP health check HTTP error: {res.status_code}")
            return False
        data = res.json()
        if not data.get("available"):
            log(f"CDP endpoint {CDP_URL} is offline or unresponsive.")
            return False
        log(f"CDP online: {data.get('browser')} on protocol {data.get('protocol_version')}")
        return True
    except Exception as exc:
        log(f"Failed to connect to Skyvern server or CDP: {exc}")
        return False


def dispatch_canary_task(client: httpx.Client) -> str:
    """Dispatch the non-destructive 6-step checkout golden path journey."""
    payload: dict[str, Any] = {
        "url": "https://www.amazon.com",
        "prompt": (
            "1. Locate the main search bar and search for 'Anker USB-C cable'. "
            "2. In the results, click the first organic product result. "
            "3. On the product detail page, verify the item is in stock and click 'Add to Cart'. "
            "4. If any warranty, accessory, or protection plan popup appears, dismiss it or click 'No thanks'. "
            "5. Go to the cart, verify the items, and click 'Proceed to checkout'. "
            "6. STOP immediately once the review/checkout screen is reached. DO NOT click 'Place your order'."
        ),
        "cdp_url": CDP_URL,
        "max_steps": 25,
        "extraction_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "cart_subtotal": {"type": "number"},
                "reached_checkout": {"type": "boolean"},
            },
            "required": ["product_name", "reached_checkout"],
        },
    }

    log("Enqueuing synthetic golden-path task via POST /api/execute...")
    res = client.post(f"{SKYVERN_API_BASE}/api/execute", json=payload, timeout=10.0)
    res.raise_for_status()
    task_id = res.json()["task_id"]
    log(f"Golden-path task enqueued with task_id: {task_id}")
    return task_id


def poll_task(
    client: httpx.Client,
    task_id: str,
    timeout_seconds: int = MAX_TIMEOUT_SECONDS,
    label: str = "Task",
) -> dict[str, Any]:
    """Poll the task endpoint until completion, failure, or timeout."""
    start_time = time.time()

    while True:
        elapsed = int(time.time() - start_time)
        if elapsed > timeout_seconds:
            raise TimeoutError(f"{label} exceeded max timeout of {timeout_seconds}s")

        res = client.get(f"{SKYVERN_API_BASE}/api/tasks/{task_id}", timeout=10.0)
        res.raise_for_status()
        data = res.json()
        status = data.get("status")

        log(f"[{label}] Elapsed: {elapsed}s | Status: {status}")

        if status in ("completed", "failed"):
            return data

        time.sleep(POLL_INTERVAL_SECONDS)


def run_cart_cleanup(client: httpx.Client) -> None:
    """Dispatches a decoupled lightweight 2-step task to empty the Amazon cart."""
    cleanup_payload: dict[str, Any] = {
        "url": "https://www.amazon.com/gp/cart/view.html",
        "prompt": (
            "Look at the cart items. Click Delete on each item until the cart displays "
            "'Your Amazon Cart is empty'. Stop immediately once empty."
        ),
        "cdp_url": CDP_URL,
        "max_steps": 6,
    }
    try:
        log("Starting post-run cart cleanup task at https://www.amazon.com/gp/cart/view.html ...")
        res = client.post(f"{SKYVERN_API_BASE}/api/execute", json=cleanup_payload, timeout=10.0)
        res.raise_for_status()
        cleanup_task_id = res.json()["task_id"]
        log(f"Cart cleanup task enqueued with task_id: {cleanup_task_id}")
        cleanup_data = poll_task(
            client,
            cleanup_task_id,
            timeout_seconds=CLEANUP_TIMEOUT_SECONDS,
            label="CartCleanup",
        )
        cleanup_run_status = (cleanup_data.get("result") or {}).get("status", cleanup_data.get("status"))
        log(f"Post-run cart cleanup finished with status: {cleanup_run_status}")
    except Exception as exc:
        log(f"WARNING: Post-run cart cleanup encountered non-fatal error: {exc}")


def main() -> int:
    exit_code = 1
    with httpx.Client() as client:
        # Step 1: Pre-flight CDP Verification
        if not check_cdp_health(client):
            log("ABORT: Chrome remote debugging session is unavailable.")
            return 1

        try:
            # Step 2: Golden Path Task Dispatch
            try:
                task_id = dispatch_canary_task(client)
            except Exception as exc:
                log(f"ABORT: Failed to dispatch canary payload: {exc}")
                return 1

            # Step 3: Golden Path Execution Polling
            try:
                task_data = poll_task(client, task_id, label="GoldenPath")
            except Exception as exc:
                log(f"FAILURE: Execution polling error: {exc}")
                return 1

            # Step 4: Assertion and Forensic Validation
            status = task_data.get("status")
            result = task_data.get("result") or {}
            output = result.get("output") or {}

            log(f"Golden-path task finished with status: '{status}'")
            log(f"Extraction Output: {json.dumps(output, indent=2)}")

            reached_checkout = output.get("reached_checkout") is True
            has_product = bool(output.get("product_name"))

            if status == "completed" and reached_checkout and has_product:
                step_count = result.get("step_count", "N/A")
                log(f"PASS: Golden path successfully validated in {step_count} steps.")
                exit_code = 0
            else:
                failure_reason = (
                    result.get("failure_reason")
                    or task_data.get("error")
                    or "Assertion criteria not met"
                )
                screenshots = result.get("screenshot_urls", [])
                last_screenshot = screenshots[-1] if screenshots else "None captured"

                log("FAIL: Golden path broken.")
                log(f"  • Reason: {failure_reason}")
                log(f"  • Terminal Screenshot: {last_screenshot}")
                log(f"  • Local Bundle: artifacts/{task_id}/")
                exit_code = 1
        finally:
            # Step 5: Decoupled Post-Run Cart Cleanup (preserves primary exit_code)
            run_cart_cleanup(client)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
