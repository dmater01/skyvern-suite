import asyncio
import json
from pathlib import Path
import time
from typing import Any, Dict

import httpx

API_BASE_URL = "http://localhost:8000"
ARTIFACTS_ROOT = Path("artifacts")


async def run_artifact_pipeline_verification() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {
            "url": "https://news.ycombinator.com",
            "prompt": "Extract the first 2 story titles from the front page.",
            "extraction_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        }

        print(f"[1/4] Dispatching scraping task to {API_BASE_URL}/api/execute ...")
        start_time = time.monotonic()
        resp = await client.post(f"{API_BASE_URL}/api/execute", json=payload)
        resp.raise_for_status()
        enqueue_data = resp.json()
        task_id = enqueue_data["task_id"]
        print(f"      Enqueued task_id={task_id} (status={enqueue_data['status']})")

        print(f"[2/4] Polling GET {API_BASE_URL}/api/tasks/{task_id} until completed...")
        final_data: Dict[str, Any] = enqueue_data
        deadline = start_time + 240.0
        while time.monotonic() < deadline:
            status_resp = await client.get(f"{API_BASE_URL}/api/tasks/{task_id}")
            status_resp.raise_for_status()
            final_data = status_resp.json()
            if final_data.get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(3.0)

        duration = round(time.monotonic() - start_time, 2)
        print(f"      Task finished in {duration}s with status={final_data.get('status')}")
        assert final_data.get("status") == "completed", f"Task did not complete cleanly: {final_data}"

        print(f"[3/4] Verifying on-disk artifact structure under artifacts/{task_id}/ ...")
        task_dir = ARTIFACTS_ROOT / task_id
        recording_path = task_dir / "recording.mp4"
        extraction_path = task_dir / "extraction.json"
        metadata_path = task_dir / "task_metadata.json"
        screenshots_dir = task_dir / "screenshots"

        assert task_dir.is_dir(), f"Missing artifact directory: {task_dir}"
        assert recording_path.is_file() and recording_path.stat().st_size > 0, (
            f"Missing or empty recording.mp4 at {recording_path}"
        )
        assert extraction_path.is_file(), f"Missing extraction.json at {extraction_path}"
        assert metadata_path.is_file(), f"Missing task_metadata.json at {metadata_path}"

        screenshots = sorted(screenshots_dir.glob("*.png")) if screenshots_dir.is_dir() else []
        assert len(screenshots) >= 1, f"Expected at least 1 screenshot .png in {screenshots_dir}"

        print(f"[4/4] Verifying HTTP download endpoint GET /api/artifacts/{task_id}/recording.mp4 ...")
        video_resp = await client.get(f"{API_BASE_URL}/api/artifacts/{task_id}/recording.mp4")
        assert video_resp.status_code == 200, (
            f"Expected HTTP 200 for recording.mp4, got {video_resp.status_code}"
        )
        content_type = video_resp.headers.get("content-type", "")
        assert "video/mp4" in content_type, f"Expected video/mp4 content-type, got {content_type}"
        assert len(video_resp.content) == recording_path.stat().st_size, (
            "Downloaded video byte length does not match on-disk recording.mp4"
        )

        first_screenshot_name = screenshots[0].name
        img_resp = await client.get(
            f"{API_BASE_URL}/api/artifacts/{task_id}/screenshots/{first_screenshot_name}"
        )
        assert img_resp.status_code == 200 and "image/png" in img_resp.headers.get("content-type", "")

        result_obj = final_data.get("result") or {}
        print("\n=== Artifact Pipeline E2E Verification Results ===")
        print(f"- Task ID:                  {task_id}")
        print(f"- Duration:                 {duration}s")
        print(f"- Bundle Directory:         {task_dir}")
        print(f"- recording.mp4 Size:       {recording_path.stat().st_size} bytes")
        print(f"- Screenshots Count:        {len(screenshots)} ({[p.name for p in screenshots]})")
        print(f"- extraction.json:          {extraction_path.read_text(encoding='utf-8').strip()}")
        print(f"- Updated recording_url:    {result_obj.get('recording_url')}")
        print(f"- Updated screenshot_urls:  {result_obj.get('screenshot_urls')}")
        print(f"- HTTP Download Status:     {video_resp.status_code} ({content_type}, {len(video_resp.content)} bytes)")
        print("\nALL ARTIFACT PIPELINE VERIFICATION ASSERTIONS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_artifact_pipeline_verification())
