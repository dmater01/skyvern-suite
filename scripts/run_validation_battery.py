import asyncio
import json
import time
from typing import Any, Dict, List, Tuple

import httpx

BASE_URL = "http://localhost:8000"
POLL_INTERVAL_SECONDS = 3.0
POLL_TIMEOUT_SECONDS = 360.0


async def submit_and_poll_task(
    client: httpx.AsyncClient,
    label: str,
    concurrency_level: int,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Submits a task to POST /api/execute and polls GET /api/tasks/{task_id} until terminal."""
    start_time = time.monotonic()
    resp = await client.post(f"{BASE_URL}/api/execute", json=payload)
    resp.raise_for_status()
    enqueue_data = resp.json()
    task_id = enqueue_data["task_id"]
    initial_status = enqueue_data["status"]
    print(f"[{label}] Enqueued task_id={task_id} (initial_status={initial_status})")

    deadline = start_time + POLL_TIMEOUT_SECONDS
    final_data = enqueue_data
    while time.monotonic() < deadline:
        status_resp = await client.get(f"{BASE_URL}/api/tasks/{task_id}")
        status_resp.raise_for_status()
        final_data = status_resp.json()
        status = final_data.get("status")
        if status in ("completed", "failed"):
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    duration = round(time.monotonic() - start_time, 2)
    print(f"[{label}] Finished task_id={task_id} in {duration}s with status={final_data.get('status')}")
    return {
        "label": label,
        "task_id": task_id,
        "initial_status": initial_status,
        "concurrency_level": concurrency_level,
        "duration_s": duration,
        "final_payload": final_data,
    }


def validate_scenario_a(record: Dict[str, Any]) -> Tuple[str, str]:
    payload = record["final_payload"]
    queue_status = payload.get("status", "unknown")
    result = payload.get("result") or {}
    run_status = result.get("status", queue_status)
    output = result.get("output")

    if (
        queue_status == "completed"
        and run_status == "completed"
        and isinstance(output, list)
        and len(output) >= 1
        and all(isinstance(item, dict) and isinstance(item.get("title"), str) for item in output)
    ):
        return f"{queue_status} (run: {run_status})", f"PASS ({len(output)} items extracted)"
    return f"{queue_status} (run: {run_status})", f"FAIL (output={output})"


def validate_scenario_b(record: Dict[str, Any], health_ok: bool) -> Tuple[str, str]:
    payload = record["final_payload"]
    queue_status = payload.get("status", "unknown")
    result = payload.get("result") or {}
    run_status = result.get("status", queue_status)
    output = result.get("output")
    errors = result.get("errors") or []

    # Acceptable graceful degradation: either completed with empty tickers list/null output
    # or terminated/failed cleanly with mapped errors while FastAPI remains healthy.
    tickers = output.get("tickers", []) if isinstance(output, dict) else (output if isinstance(output, list) else [])
    if health_ok and queue_status in ("completed", "failed"):
        if isinstance(tickers, list) and len(tickers) == 0:
            return (
                f"{queue_status} (run: {run_status})",
                f"PASS (Graceful empty result; server healthy={health_ok})",
            )
        if errors or result.get("failure_reason") or payload.get("error"):
            return (
                f"{queue_status} (run: {run_status})",
                f"PASS (Graceful error mapping; server healthy={health_ok})",
            )
    return f"{queue_status} (run: {run_status})", f"FAIL (output={output}, health={health_ok})"


def validate_scenario_c(record: Dict[str, Any]) -> Tuple[str, str]:
    payload = record["final_payload"]
    queue_status = payload.get("status", "unknown")
    result = payload.get("result") or {}
    run_status = result.get("status", queue_status)
    output = result.get("output")

    stories = output.get("stories") if isinstance(output, dict) else (output if isinstance(output, list) else None)
    if not isinstance(stories, list) or len(stories) == 0:
        return f"{queue_status} (run: {run_status})", f"FAIL (stories={output})"

    valid_items = 0
    for item in stories:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else item
        title_ok = isinstance(item.get("title"), str) and bool(item.get("title").strip())
        author_ok = isinstance(item.get("author") or metrics.get("author"), str)
        rank_ok = isinstance(item.get("rank"), int)
        points_ok = isinstance(metrics.get("points"), int)
        comments_ok = isinstance(metrics.get("comments_count"), int)
        if title_ok and author_ok and rank_ok and points_ok and comments_ok:
            valid_items += 1

    if queue_status == "completed" and valid_items == len(stories):
        return (
            f"{queue_status} (run: {run_status})",
            f"PASS ({valid_items}/{len(stories)} nested items with strict ints)",
        )
    return (
        f"{queue_status} (run: {run_status})",
        f"PARTIAL/FAIL ({valid_items}/{len(stories)} valid; output={json.dumps(output)[:120]})",
    )


async def run_battery() -> None:
    timeout = httpx.Timeout(30.0)
    summary_rows: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        print("=== Starting Skyvern Validation Battery ===\n")

        # ------------------------------------------------------------------
        # Scenario A: Concurrency & Task Queue (2 simultaneous tasks)
        # ------------------------------------------------------------------
        print("--- Scenario A: Concurrency & Task Queue (2 simultaneous tasks) ---")
        schema_a = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["title"],
            },
        }
        payload_a1 = {
            "url": "https://news.ycombinator.com/news",
            "prompt": "Extract the first 2 story titles and their URLs from the front page without clicking any links.",
            "extraction_schema": schema_a,
        }
        payload_a2 = {
            "url": "https://news.ycombinator.com/newest",
            "prompt": "Extract the first 2 newest story titles and their URLs from the page without clicking any links.",
            "extraction_schema": schema_a,
        }

        res_a1, res_a2 = await asyncio.gather(
            submit_and_poll_task(client, "Scenario A1 (Concurrent Frontpage)", 2, payload_a1),
            submit_and_poll_task(client, "Scenario A2 (Concurrent Newest)", 2, payload_a2),
        )

        for rec in (res_a1, res_a2):
            status_str, adherence_str = validate_scenario_a(rec)
            summary_rows.append(
                {
                    "task_type": rec["label"],
                    "concurrency": rec["concurrency_level"],
                    "duration": rec["duration_s"],
                    "status": status_str,
                    "adherence": adherence_str,
                    "raw": rec["final_payload"],
                }
            )

        # ------------------------------------------------------------------
        # Scenario B: Negative / Graceful Degradation (Impossible Task)
        # ------------------------------------------------------------------
        print("\n--- Scenario B: Negative / Graceful Degradation ---")
        schema_b = {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "price": {"type": "number"},
                        },
                        "required": ["symbol", "price"],
                    },
                }
            },
            "required": ["tickers"],
        }
        payload_b = {
            "url": "https://news.ycombinator.com/jobs",
            "prompt": (
                "Extract live real-time NYSE stock ticker prices (symbol and current numerical trading price) "
                "from a stock ticker table on this page. Do not navigate away from this page. "
                "If no live stock ticker prices exist on this page, complete immediately and return an empty tickers list."
            ),
            "extraction_schema": schema_b,
        }

        res_b = await submit_and_poll_task(
            client, "Scenario B (Negative / Impossible Extraction)", 1, payload_b
        )
        health_resp = await client.get(f"{BASE_URL}/health")
        health_ok = health_resp.status_code == 200 and health_resp.json().get("status") == "healthy"
        status_b, adherence_b = validate_scenario_b(res_b, health_ok)
        summary_rows.append(
            {
                "task_type": res_b["label"],
                "concurrency": res_b["concurrency_level"],
                "duration": res_b["duration_s"],
                "status": status_b,
                "adherence": adherence_b,
                "raw": res_b["final_payload"],
            }
        )

        # ------------------------------------------------------------------
        # Scenario C: Complex Nested Schema (/best)
        # ------------------------------------------------------------------
        print("\n--- Scenario C: Complex Nested Schema (https://news.ycombinator.com/best) ---")
        schema_c = {
            "type": "object",
            "properties": {
                "stories": {
                    "type": "array",
                    "description": "Top 3 stories listed on the Hacker News Best page",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer", "description": "Numeric rank of the story on the page (e.g. 1, 2, 3)"},
                            "title": {"type": "string", "description": "Title of the story"},
                            "author": {"type": "string", "description": "Username of the story submitter"},
                            "metrics": {
                                "type": "object",
                                "properties": {
                                    "points": {"type": "integer", "description": "Integer score/points of the story"},
                                    "comments_count": {"type": "integer", "description": "Integer number of comments"},
                                },
                                "required": ["points", "comments_count"],
                            },
                        },
                        "required": ["rank", "title", "author", "metrics"],
                    },
                }
            },
            "required": ["stories"],
        }
        payload_c = {
            "url": "https://news.ycombinator.com/best",
            "prompt": "Extract the first 3 stories listed on this page.",
            "extraction_schema": schema_c,
        }

        res_c = await submit_and_poll_task(
            client, "Scenario C (Complex Nested Schema /best)", 1, payload_c
        )
        status_c, adherence_c = validate_scenario_c(res_c)
        summary_rows.append(
            {
                "task_type": res_c["label"],
                "concurrency": res_c["concurrency_level"],
                "duration": res_c["duration_s"],
                "status": status_c,
                "adherence": adherence_c,
                "raw": res_c["final_payload"],
            }
        )

    print("\n## Validation Summary Table\n")
    print("| Task Type | Concurrency Level | Duration (seconds) | Result Status | Schema Adherence |")
    print("| :--- | :---: | :---: | :--- | :--- |")
    for row in summary_rows:
        print(
            f"| {row['task_type']} | {row['concurrency']} | {row['duration']:.2f}s | {row['status']} | {row['adherence']} |"
        )

    print("\n## Extracted Outputs Preview\n")
    for row in summary_rows:
        result_obj = (row["raw"].get("result") or {}).get("output")
        print(f"### {row['task_type']}")
        print(json.dumps(result_obj, indent=2))


if __name__ == "__main__":
    asyncio.run(run_battery())
