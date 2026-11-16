#!/usr/bin/env python3
"""
Royal Caribbean Cruise Canary Benchmark & Timing Instrumentation.

Executes a timed end-to-end evaluation of the deep-linked Royal Caribbean cruise
rate extraction task, records queue dwell and active execution latencies to
`artifacts/benchmark_timings.csv`, and prints a comparison table against the
baseline homepage funnel (Baseline: 337s queue dwell, 352s execution, 7 steps).
"""

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Optional

# Ensure repository root is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.poll_cruise_rates import (  # noqa: E402
    API_BASE,
    CDP_URL,
    build_cruise_task_payload,
    http_json,
)

BASELINE_METRICS = {
    "queue_dwell_seconds": 337.0,
    "active_execution_seconds": 352.0,
    "total_latency_seconds": 689.0,
    "step_count": 7,
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


def _parse_iso(ts: Optional[str]) -> Optional[ datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _resolve_step_count(task_id: str, result: Dict[str, Any]) -> int:
    step_count = result.get("step_count")
    if isinstance(step_count, int) and step_count > 0:
        return step_count
    screenshots = result.get("screenshot_urls")
    if isinstance(screenshots, list) and len(screenshots) > 0:
        return len(screenshots)
    screenshots_dir = REPO_ROOT / "artifacts" / task_id / "screenshots"
    if screenshots_dir.is_dir():
        pngs = list(screenshots_dir.glob("*.png"))
        if pngs:
            return len(pngs)
    return 0


def _append_csv_row(row: Dict[str, Any]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_PATH.is_file() and CSV_PATH.stat().st_size > 0
    with CSV_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})


def _fmt_delta(new_val: float, base_val: float, unit: str = "s") -> str:
    diff = new_val - base_val
    pct = ((new_val - base_val) / base_val * 100.0) if base_val else 0.0
    sign = "+" if diff > 0 else ""
    return f"{sign}{diff:.1f}{unit} ({sign}{pct:.1f}%)"


def print_comparison_table(metrics: Dict[str, Any]) -> None:
    q_new = float(metrics["queue_dwell_seconds"])
    e_new = float(metrics["active_execution_seconds"])
    t_new = float(metrics["total_latency_seconds"])
    s_new = int(metrics["step_count"])

    q_base = BASELINE_METRICS["queue_dwell_seconds"]
    e_base = BASELINE_METRICS["active_execution_seconds"]
    t_base = BASELINE_METRICS["total_latency_seconds"]
    s_base = BASELINE_METRICS["step_count"]

    print("\n" + "=" * 78)
    print("ROYAL CARIBBEAN CRUISE CANARY BENCHMARK SUMMARY")
    print("=" * 78)
    print(f"Task ID          : {metrics['task_id']}")
    print(f"Skyvern Run ID   : {metrics.get('run_id') or 'N/A'}")
    print(f"Final Status     : {metrics['status']}")
    print(f"Extracted Rates  : Interior=${metrics.get('interior_price_usd')} | Balcony=${metrics.get('balcony_price_usd')}")
    print("-" * 78)
    print(f"{'Metric':<28} | {'Baseline':<14} | {'Optimized Run':<14} | {'Delta':<16}")
    print("-" * 78)
    print(
        f"{'Queue Dwell Time':<28} | {f'{q_base:.1f}s':<14} | {f'{q_new:.2f}s':<14} | {_fmt_delta(q_new, q_base):<16}"
    )
    print(
        f"{'Active Execution Time':<28} | {f'{e_base:.1f}s':<14} | {f'{e_new:.2f}s':<14} | {_fmt_delta(e_new, e_base):<16}"
    )
    print(
        f"{'Total End-to-End Latency':<28} | {f'{t_base:.1f}s':<14} | {f'{t_new:.2f}s':<14} | {_fmt_delta(t_new, t_base):<16}"
    )
    print(
        f"{'Engine Step Count':<28} | {f'{s_base} steps':<14} | {f'{s_new} steps':<14} | {_fmt_delta(float(s_new), float(s_base), ' steps'):<16}"
    )
    print("=" * 78)
    print(f"CSV Timings Log  : {CSV_PATH}")
    print("=" * 78 + "\n")


def run_benchmark() -> int:
    print(f"[1/4] Verifying CDP status via {API_BASE}/api/cdp/status ...")
    cdp_status = http_json("GET", f"{API_BASE}/api/cdp/status")
    print(f"      CDP Status: {json.dumps(cdp_status)}")
    if not cdp_status.get("available"):
        print("ERROR: CDP is not available on port 9222.", file=sys.stderr)
        return 1

    run_with_opt = os.getenv("RUN_WITH", "code")
    max_steps = int(os.getenv("MAX_STEPS", "15"))
    payload = build_cruise_task_payload(
        cdp_url=CDP_URL,
        max_steps=max_steps,
        run_with=run_with_opt,
    )

    print(f"[2/4] Dispatching deep-linked Royal Caribbean canary to {API_BASE}/api/execute ...")
    print(f"      URL      : {payload['url']}")
    print(f"      run_with : {payload.get('run_with')}")
    t_submit = time.perf_counter()
    submit_wall_iso = datetime.now(timezone.utc).isoformat()
    queued_resp = http_json("POST", f"{API_BASE}/api/execute", payload=payload)
    task_id = queued_resp.get("task_id")
    if not task_id:
        print(f"ERROR: Missing task_id in response: {queued_resp}", file=sys.stderr)
        return 1

    print(f"      Task ID  : {task_id}")
    print("[3/4] Measuring queue dwell and active execution latency ...")

    t_running: Optional[float] = None
    last_status = None
    final_record: Dict[str, Any] = {}

    while True:
        record = http_json("GET", f"{API_BASE}/api/tasks/{task_id}")
        now = time.perf_counter()
        status = record.get("status", "unknown")

        if status == "running" and t_running is None:
            t_running = now
            dwell_est = t_running - t_submit
            print(f"      [+{dwell_est:6.2f}s] Task transitioned queued -> running")

        if status != last_status:
            elapsed = now - t_submit
            print(f"      [+{elapsed:6.2f}s] status={status}")
            last_status = status

        if status in ("completed", "failed"):
            final_record = record
            t_done = now
            break

        # Poll faster while waiting for queue transition so dwell measurement is sub-second accurate
        time.sleep(0.25 if t_running is None else 2.0)

    # Prefer server-recorded timestamps for exact queue dwell & execution duration when available
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
    output = result.get("output") or result.get("extracted_information") or {}
    if not isinstance(output, dict):
        output = {}

    step_count = _resolve_step_count(task_id, result)
    interior_price = output.get("interior_price_usd")
    balcony_price = output.get("balcony_price_usd")

    metrics_row = {
        "timestamp": submit_wall_iso,
        "task_id": task_id,
        "run_id": result.get("run_id", ""),
        "status": final_record.get("status", "unknown"),
        "run_with": run_with_opt,
        "queue_dwell_seconds": round(queue_dwell_seconds, 3),
        "active_execution_seconds": round(active_execution_seconds, 3),
        "total_latency_seconds": round(total_latency_seconds, 3),
        "step_count": step_count,
        "interior_price_usd": interior_price if interior_price is not None else "",
        "balcony_price_usd": balcony_price if balcony_price is not None else "",
        "ship_name": output.get("ship_name", ""),
        "departure_date": output.get("departure_date", ""),
    }

    print("[4/4] Persisting benchmark timings and printing comparison report ...")
    _append_csv_row(metrics_row)
    print("\nExtracted Payload:")
    print(json.dumps(output, indent=2))
    print_comparison_table(metrics_row)

    if final_record.get("status") != "completed":
        print(f"ERROR: Benchmark task failed: {final_record.get('error')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_benchmark())
