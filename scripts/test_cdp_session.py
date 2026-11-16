import asyncio
import json
import time
from typing import Any, Dict

import httpx

API_BASE_URL = "http://localhost:8000"
DEFAULT_CDP_URL = "http://localhost:9222"


async def run_cdp_verification() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"[1/3] Probing CDP status via GET {API_BASE_URL}/api/cdp/status?cdp_url={DEFAULT_CDP_URL} ...")
        status_resp = await client.get(
            f"{API_BASE_URL}/api/cdp/status",
            params={"cdp_url": DEFAULT_CDP_URL},
        )
        status_resp.raise_for_status()
        cdp_status = status_resp.json()
        print("      CDP Status Response:", json.dumps(cdp_status, indent=2))

        assert "available" in cdp_status and "endpoint" in cdp_status

        if not cdp_status.get("available"):
            print(
                "\n[2/3] CDP endpoint is currently INACTIVE on http://localhost:9222."
            )
            print(
                "      Verifying fast-fail 400 guard when dispatching a task with an unreachable cdp_url..."
            )
            bad_resp = await client.post(
                f"{API_BASE_URL}/api/execute",
                json={
                    "url": "https://news.ycombinator.com",
                    "prompt": "Extract top story",
                    "cdp_url": DEFAULT_CDP_URL,
                },
            )
            assert bad_resp.status_code == 400, (
                f"Expected HTTP 400 when CDP is unreachable, got {bad_resp.status_code}: {bad_resp.text}"
            )
            print(f"      Fast-fail guard verified: HTTP {bad_resp.status_code} -> {bad_resp.json()['detail']}")
            print(
                "\n[3/3] To attach Skyvern to a live authenticated Chrome session, run:\n"
                "        ./scripts/launch_debug_chrome.sh\n"
                "      Skipping live CDP browser execution cleanly."
            )
            return

        print(f"\n[2/3] CDP is ACTIVE ({cdp_status.get('browser')}). Submitting task with cdp_url={DEFAULT_CDP_URL} ...")
        start_time = time.monotonic()
        exec_resp = await client.post(
            f"{API_BASE_URL}/api/execute",
            json={
                "url": "https://news.ycombinator.com",
                "prompt": "Extract the first 2 story titles from the front page.",
                "extraction_schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    },
                },
                "cdp_url": DEFAULT_CDP_URL,
                "start_fresh_browser": False,
            },
        )
        exec_resp.raise_for_status()
        enqueue_data = exec_resp.json()
        task_id = enqueue_data["task_id"]
        assert enqueue_data.get("cdp_url") == DEFAULT_CDP_URL
        print(f"      Enqueued task_id={task_id} with cdp_url={enqueue_data.get('cdp_url')}")

        print(f"[3/3] Polling GET {API_BASE_URL}/api/tasks/{task_id} until completed...")
        final_data: Dict[str, Any] = enqueue_data
        deadline = start_time + 240.0
        while time.monotonic() < deadline:
            poll_resp = await client.get(f"{API_BASE_URL}/api/tasks/{task_id}")
            poll_resp.raise_for_status()
            final_data = poll_resp.json()
            if final_data.get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(3.0)

        assert final_data.get("status") == "completed", f"CDP task failed: {final_data}"
        output = (final_data.get("result") or {}).get("output")
        print("\n=== CDP Session Attachment Verification Results ===")
        print(f"- Task ID:          {task_id}")
        print(f"- Attached CDP URL: {final_data.get('cdp_url')}")
        print(f"- Browser Version:  {cdp_status.get('browser')}")
        print(f"- Extracted Output: {json.dumps(output, indent=2)}")
        print("\nALL CDP SESSION VERIFICATION ASSERTIONS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_cdp_verification())
