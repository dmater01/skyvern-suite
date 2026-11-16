import asyncio
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
import httpx
import uvicorn

API_BASE_URL = "http://localhost:8000"
MOCK_RECEIVER_HOST = "127.0.0.1"
MOCK_RECEIVER_PORT = 8088
WEBHOOK_URL = f"http://{MOCK_RECEIVER_HOST}:{MOCK_RECEIVER_PORT}/webhook"
WEBHOOK_SECRET = "test_secret_key"


async def run_webhook_verification() -> None:
    webhook_received_event = asyncio.Event()
    captured_webhook: Dict[str, Any] = {}

    receiver_app = FastAPI()

    @receiver_app.post("/webhook")
    async def receive_webhook(request: Request) -> Dict[str, str]:
        raw_body = await request.body()
        captured_webhook["raw_body"] = raw_body
        captured_webhook["headers"] = dict(request.headers)
        captured_webhook["json"] = json.loads(raw_body.decode("utf-8"))
        webhook_received_event.set()
        return {"status": "received"}

    config = uvicorn.Config(
        receiver_app,
        host=MOCK_RECEIVER_HOST,
        port=MOCK_RECEIVER_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # Wait briefly for the mock webhook server to bind port 8088
    for _ in range(30):
        if server.started:
            break
        await asyncio.sleep(0.1)

    try:
        print(f"[1/4] Mock webhook receiver listening at {WEBHOOK_URL}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            execute_payload = {
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
                "webhook_url": WEBHOOK_URL,
                "webhook_secret": WEBHOOK_SECRET,
            }

            print(f"[2/4] Dispatching scraping task to {API_BASE_URL}/api/execute ...")
            resp = await client.post(f"{API_BASE_URL}/api/execute", json=execute_payload)
            resp.raise_for_status()
            enqueue_data = resp.json()
            task_id = enqueue_data["task_id"]
            print(f"      Enqueued task_id={task_id} (status={enqueue_data['status']})")

            print("[3/4] Waiting for asynchronous webhook callback at mock receiver...")
            await asyncio.wait_for(webhook_received_event.wait(), timeout=240.0)
            print("      Webhook callback received!")

            # Poll GET /api/tasks/{task_id} until webhook_delivered flag is finalized
            task_status_data: Optional[Dict[str, Any]] = None
            for _ in range(20):
                status_resp = await client.get(f"{API_BASE_URL}/api/tasks/{task_id}")
                status_resp.raise_for_status()
                task_status_data = status_resp.json()
                if task_status_data.get("webhook_delivered") is not None:
                    break
                await asyncio.sleep(0.1)

            assert task_status_data is not None, "Failed to retrieve task status"

            # Verify HMAC-SHA256 signature
            raw_body: bytes = captured_webhook["raw_body"]
            headers: Dict[str, str] = captured_webhook["headers"]
            received_payload: Dict[str, Any] = captured_webhook["json"]

            signature_header = headers.get("x-skyvern-signature", "")
            event_header = headers.get("x-skyvern-event", "")
            content_type = headers.get("content-type", "")

            expected_digest = hmac.new(
                WEBHOOK_SECRET.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            expected_signature = f"sha256={expected_digest}"

            print("[4/4] Running verification assertions...")
            assert signature_header == expected_signature, (
                f"HMAC mismatch: expected {expected_signature}, got {signature_header}"
            )
            assert event_header == "task.completed", (
                f"Unexpected X-Skyvern-Event header: {event_header}"
            )
            assert "application/json" in content_type, (
                f"Unexpected Content-Type header: {content_type}"
            )
            assert received_payload["task_id"] == task_id
            assert received_payload["status"] == "completed"
            assert received_payload["result"] == task_status_data["result"]
            assert task_status_data["webhook_delivered"] is True, (
                f"Expected webhook_delivered=True, got {task_status_data}"
            )
            assert task_status_data["webhook_delivery_attempts"] >= 1, (
                f"Expected webhook_delivery_attempts >= 1, got {task_status_data}"
            )

            print("\n=== Webhook Delivery E2E Verification Results ===")
            print(f"- Task ID:                   {task_id}")
            print(f"- Task Final Status:         {task_status_data['status']}")
            print(f"- Webhook URL:               {task_status_data['webhook_url']}")
            print(f"- X-Skyvern-Event:           {event_header}")
            print(f"- X-Skyvern-Signature:       {signature_header}")
            print(f"- Expected HMAC-SHA256:      {expected_signature}")
            print(f"- Signature Verified:        True")
            print(f"- webhook_delivered:         {task_status_data['webhook_delivered']}")
            print(f"- webhook_delivery_attempts: {task_status_data['webhook_delivery_attempts']}")
            print(f"- Extracted Output:          {json.dumps((received_payload.get('result') or {}).get('output'), indent=2)}")
            print("\nALL WEBHOOK VERIFICATION ASSERTIONS PASSED!")
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    asyncio.run(run_webhook_verification())
