#!/usr/bin/env python3
"""
Books-A-Million Retail Canary Verification & Timing Benchmark (`scripts/test_bookstore_canary.py`).

Executes a non-destructive end-to-end product discovery and structured extraction journey
on Books-A-Million (`https://www.booksamillion.com/search?query=Project+Hail+Mary`) via
the local Skyvern FastAPI server (`http://localhost:8000`) attached to Chrome CDP (`:9222`).

Validates:
  1. CDP session availability (`/api/cdp/status`).
  2. Queue dwell, active execution, and total wall-clock latency (`run_with: "code"`).
  3. Telemetry persistence in `artifacts/benchmark_timings.csv`.
  4. Persisted `artifacts/<task_id>/extraction.json` non-null product fields.
  5. Screenshot artifact generation and HTTP accessibility (`action_0.png`, `action_1.png`, ...).
"""

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

API_BASE = os.getenv("SKYVERN_SUITE_API", "http://localhost:8000")
CDP_URL = os.getenv("CDP_URL", "http://localhost:9222")

BAM_SEARCH_URL = "https://www.booksamillion.com/search?query=Project+Hail+Mary"

BAM_PROMPT = (
    "1. Dismiss any email newsletter modal, promotional pop-up, or cookie overlay if present. "
    "2. Locate and select the primary Hardcover or Paperback edition of 'Project Hail Mary' by Andy Weir "
    "from the search results grid and navigate to its canonical product details page. "
    "3. Inspect the available formats and pricing tiers (Hardcover, Paperback, and/or eBook) on the product details page. "
    "4. STOP immediately on the product details page. DO NOT click 'Add to Cart', 'Quick Shop', "
    "or proceed into the checkout funnel."
)

BAM_EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Book title on the product page"},
        "author": {"type": "string", "description": "Primary book author"},
        "format": {
            "type": "string",
            "description": "Selected book format (e.g., Hardcover, Paperback, eBook)",
        },
        "price_usd": {
            "type": "number",
            "description": "Numeric price in USD for the selected format",
        },
        "in_stock": {
            "type": "boolean",
            "description": "Whether the selected edition is currently available / in stock",
        },
        "reached_product_page": {
            "type": "boolean",
            "description": "True if the canonical product details page was reached",
        },
    },
    "required": [
        "title",
        "author",
        "format",
        "price_usd",
        "in_stock",
        "reached_product_page",
    ],
}

CSV_PATH = REPO_ROOT / "artifacts" / "benchmark_timings.csv"
CSV_COLUMNS = [
    "timestamp",
    "task_id",
    "run_id",
    "status",
    "run_with",
    "queue_dwell_seconds",
    "active_execution_seconds",
    "total_latency_seconds",
    "step_count",
    "interior_price_usd",
    "balcony_price_usd",
    "ship_name",
    "departure_date",
]


def http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw, strict=False)


def http_status_and_size(url: str, timeout: float = 15.0) -> tuple[int, int]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, len(body)


def build_bookstore_task_payload(
    cdp_url: str = CDP_URL,
    max_steps: int = 12,
    run_with: str = "code",
) -> Dict[str, Any]:
    return {
        "url": BAM_SEARCH_URL,
        "prompt": BAM_PROMPT,
        "cdp_url": cdp_url,
        "max_steps": max_steps,
        "run_with": run_with,
        "extraction_schema": BAM_EXTRACTION_SCHEMA,
    }


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _resolve_screenshots(task_id: str) -> List[Path]:
    screenshots_dir = REPO_ROOT / "artifacts" / task_id / "screenshots"
    if not screenshots_dir.is_dir():
        return []
    return sorted(screenshots_dir.glob("action_*.png"))


def _resolve_step_count(task_id: str, result: Dict[str, Any], screenshots: List[Path]) -> int:
    step_count = result.get("step_count")
    if isinstance(step_count, int) and step_count > 0:
        return step_count
    urls = result.get("screenshot_urls")
    if isinstance(urls, list) and len(urls) > 0:
        return len(urls)
    return len(screenshots)


def _append_csv_row(row: Dict[str, Any]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_PATH.is_file() and CSV_PATH.stat().st_size > 0
    with CSV_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})


def _validate_extraction_file(task_id: str) -> tuple[bool, Dict[str, Any], str]:
    extraction_path = REPO_ROOT / "artifacts" / task_id / "extraction.json"
    if not extraction_path.is_file():
        return False, {}, f"Missing extraction file at {extraction_path}"
    try:
        raw = json.loads(extraction_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, {}, f"Invalid JSON in {extraction_path}: {exc}"

    if not isinstance(raw, dict) or not raw:
        return False, {}, f"Empty or non-object JSON in {extraction_path}: {raw}"

    output = raw.get("output") if isinstance(raw.get("output"), dict) else raw
    if not isinstance(output, dict) or not output:
        return False, {}, f"Empty or null extraction payload in {extraction_path}: {raw}"

    missing = [
        key
        for key in ("title", "author", "price_usd", "reached_product_page")
        if output.get(key) is None or output.get(key) == ""
    ]
    if missing:
        return False, output, f"Missing required non-null product fields: {missing}"
    if not output.get("reached_product_page"):
        return False, output, f"reached_product_page is False in {extraction_path}"
    return True, output, str(extraction_path)


def _verify_screenshots_accessible(
    api_base: str, task_id: str, screenshots: List[Path]
) -> tuple[bool, List[str]]:
    if not screenshots:
        return False, ["No screenshot PNGs found in artifacts/<task_id>/screenshots/"]
    verified_details: List[str] = []
    for png in screenshots:
        url = f"{api_base}/api/artifacts/{task_id}/screenshots/{png.name}"
        try:
            code, size = http_status_and_size(url)
            if code != 200 or size <= 0:
                return False, [f"{png.name} returned HTTP {code} ({size} bytes)"]
            verified_details.append(f"{png.name} ({size:,} bytes)")
        except Exception as exc:
            return False, [f"Failed to fetch {url}: {exc}"]
    return True, verified_details


def print_summary_table(
    task_id: str,
    run_id: str,
    status: str,
    queue_dwell_s: float,
    active_exec_s: float,
    total_latency_s: float,
    step_count: int,
    output: Dict[str, Any],
    extraction_path: str,
    screenshot_Paths: List[Path],
    failure_reason: Optional[str] = None,
) -> None:
    print("\n" + "=" * 82)
    print("BOOKS-A-MILLION E-COMMERCE CANARY VERIFICATION SUMMARY")
    print("=" * 82)
    print(f"{'Field':<28} | {'Value'}")
    print("-" * 82)
    print(f"{'Working Directory':<28} | {Path.cwd()}")
    print(f"{'Task ID':<28} | {task_id}")
    print(f"{'Skyvern Run ID':<28} | {run_id or 'N/A'}")
    print(f"{'Final Status':<28} | {status}")
    print(f"{'Queue Latency (Dwell)':<28} | {queue_dwell_s:.2f}s")
    print(f"{'Active Execution Time':<28} | {active_exec_s:.2f}s")
    print(f"{'Total End-to-End Latency':<28} | {total_latency_s:.2f}s")
    print(f"{'Engine Step Count':<28} | {step_count} steps")
    print("-" * 82)
    print(f"{'Extracted Title':<28} | {output.get('title')}")
    print(f"{'Extracted Author':<28} | {output.get('author')}")
    print(f"{'Extracted Format':<28} | {output.get('format')}")
    print(f"{'Extracted Price (USD)':<28} | ${output.get('price_usd')}")
    print(f"{'In Stock':<28} | {output.get('in_stock')}")
    print(f"{'Reached Product Page':<28} | {output.get('reached_product_page')}")
    print("-" * 82)
    print(f"{'Extraction JSON Path':<28} | {extraction_path}")
    print(f"{'CSV Benchmark Log':<28} | {CSV_PATH}")
    if screenshot_Paths:
        print(f"{'Captured Screenshots':<28} | {len(screenshot_Paths)} file(s) in {screenshot_Paths[0].parent}")
        for p in screenshot_Paths:
            print(f"{'':<28} | - {p}")
    else:
        print(f"{'Captured Screenshots':<28} | None")
    if failure_reason:
        print("-" * 82)
        print(f"{'Failure Diagnosis':<28} | {failure_reason}")
        if screenshot_Paths:
            print(f"{'Terminal Screenshot':<28} | {screenshot_Paths[-1]}")
    print("=" * 82 + "\n")


def run_bookstore_canary() -> int:
    os.chdir(REPO_ROOT)
    print(f"[0/5] Verified working directory: {Path.cwd()}")

    print(f"[1/5] Checking CDP availability via {API_BASE}/api/cdp/status ...")
    cdp_status = http_json("GET", f"{API_BASE}/api/cdp/status")
    print(f"      CDP Status: {json.dumps(cdp_status)}")
    if not cdp_status.get("available"):
        print("ERROR: CDP endpoint on port 9222 is not available.", file=sys.stderr)
        return 1

    run_with_opt = os.getenv("RUN_WITH", "code")
    max_steps = int(os.getenv("MAX_STEPS", "12"))
    payload = build_bookstore_task_payload(
        cdp_url=CDP_URL,
        max_steps=max_steps,
        run_with=run_with_opt,
    )

    print(f"[2/5] Dispatching Books-A-Million canary task to {API_BASE}/api/execute ...")
    print(f"      URL      : {payload['url']}")
    print(f"      run_with : {payload['run_with']}")
    t_submit = time.perf_counter()
    submit_wall_iso = datetime.now(timezone.utc).isoformat()
    queued_resp = http_json("POST", f"{API_BASE}/api/execute", payload=payload)
    task_id = queued_resp.get("task_id")
    if not task_id:
        print(f"ERROR: Missing task_id in execute response: {queued_resp}", file=sys.stderr)
        return 1
    print(f"      Task ID  : {task_id}")

    print("[3/5] Polling task status and recording telemetry ...")
    t_running: Optional[float] = None
    last_status: Optional[str] = None
    final_record: Dict[str, Any] = {}

    while True:
        record = http_json("GET", f"{API_BASE}/api/tasks/{task_id}")
        now = time.perf_counter()
        status = record.get("status", "unknown")

        if status == "running" and t_running is None:
            t_running = now
            print(f"      [+{t_running - t_submit:6.2f}s] Task transitioned queued -> running")

        if status != last_status:
            print(f"      [+{now - t_submit:6.2f}s] status={status}")
            last_status = status

        if status in ("completed", "failed"):
            final_record = record
            t_done = now
            break

        time.sleep(0.25 if t_running is None else 2.0)

    q_ts = _parse_iso(final_record.get("queued_at"))
    s_ts = _parse_iso(final_record.get("started_at"))
    c_ts = _parse_iso(final_record.get("completed_at"))

    if q_ts and s_ts:
        queue_dwell_seconds = max(0.0, (s_ts - q_ts).total_seconds())
    elif t_running is not None:
        queue_dwell_seconds = max(0.0, t_running - t_submit)
    else:
        queue_dwell_seconds = 0.0

    if s_ts and c_ts:
        active_execution_seconds = max(0.0, (c_ts - s_ts).total_seconds())
    elif t_running is not None:
        active_execution_seconds = max(0.0, t_done - t_running)
    else:
        active_execution_seconds = max(0.0, t_done - t_submit)

    total_latency_seconds = max(
        queue_dwell_seconds + active_execution_seconds,
        t_done - t_submit,
    )

    result = final_record.get("result") or {}
    run_id = result.get("run_id", "")
    screenshots = _resolve_screenshots(task_id)
    step_count = _resolve_step_count(task_id, result, screenshots)

    print("[4/5] Validating artifacts/<task_id>/extraction.json and screenshot accessibility ...")
    ext_ok, parsed_output, ext_info = _validate_extraction_file(task_id)
    if not parsed_output:
        fallback_out = result.get("output") or result.get("extracted_information") or {}
        if isinstance(fallback_out, dict):
            parsed_output = fallback_out

    shots_ok, shots_info = _verify_screenshots_accessible(API_BASE, task_id, screenshots)
    print(f"      Extraction validation : {'PASS' if ext_ok else 'FAIL'} ({ext_info})")
    print(f"      Screenshots verified  : {'PASS' if shots_ok else 'FAIL'} ({', '.join(shots_info)})")

    print("[5/5] Appending telemetry to artifacts/benchmark_timings.csv ...")
    csv_row = {
        "timestamp": submit_wall_iso,
        "task_id": task_id,
        "run_id": run_id,
        "status": final_record.get("status", "unknown"),
        "run_with": run_with_opt,
        "queue_dwell_seconds": round(queue_dwell_seconds, 3),
        "active_execution_seconds": round(active_execution_seconds, 3),
        "total_latency_seconds": round(total_latency_seconds, 3),
        "step_count": step_count,
        "interior_price_usd": parsed_output.get("price_usd", ""),
        "balcony_price_usd": "",
        "ship_name": parsed_output.get("title", "Books-A-Million: Project Hail Mary"),
        "departure_date": parsed_output.get("format", ""),
    }
    _append_csv_row(csv_row)

    failure_reason = (
        final_record.get("error")
        or result.get("failure_reason")
        or (None if (ext_ok and shots_ok) else f"ext_ok={ext_ok} ({ext_info}), shots_ok={shots_ok}")
    )
    extraction_file_str = str(REPO_ROOT / "artifacts" / task_id / "extraction.json")
    print_summary_table(
        task_id=task_id,
        run_id=run_id,
        status=final_record.get("status", "unknown"),
        queue_dwell_s=queue_dwell_seconds,
        active_exec_s=active_execution_seconds,
        total_latency_s=total_latency_seconds,
        step_count=step_count,
        output=parsed_output,
        extraction_path=extraction_file_str,
        screenshot_Paths=screenshots,
        failure_reason=failure_reason,
    )

    if final_record.get("status") != "completed" or not ext_ok or not shots_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_bookstore_canary())
