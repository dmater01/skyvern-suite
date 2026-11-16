# Operator & User Guide

This guide explains how to operate the Skyvern Automation Suite, formulate robust natural-language prompts with extraction schemas, connect to authenticated browser sessions via Chrome DevTools Protocol (CDP), receive automated webhook callbacks, and inspect generated run artifacts.

---

## 1. Quickstart

### Prerequisites
* macOS running Python 3.11+
* Google Chrome installed in `/Applications/Google Chrome.app` (for CDP-authenticated workflows)
* Virtual environment configured under `.venv`

### Starting the Server
From the project root (`~/Downloads/skyvern-suite`), start the FastAPI server with Uvicorn:

```bash
cd ~/Downloads/skyvern-suite
source .venv/bin/activate
uvicorn src.api.server:app --host 0.0.0.0 --port 8000 --reload
```

Verify service availability:

```bash
curl -s http://localhost:8000/healthz | jq .
```

Expected output:
```json
{
  "status": "healthy"
}
```

---

## 2. Dispatching Your First Scraping Task

Tasks are submitted asynchronously via `POST /api/execute`. The server returns immediately with a unique `task_id` while processing happens in the background.

### Step 1: Submit the Task

```bash
curl -X POST "http://localhost:8000/api/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news.ycombinator.com",
    "prompt": "Extract the titles and external URLs of the top 3 stories on the front page",
    "extraction_schema": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "title": {"type": "string"},
          "url": {"type": "string"}
        },
        "required": ["title", "url"]
      }
    }
  }' | jq .
```

The response provides the queued task descriptor:

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "task_type": "execute",
  "status": "queued",
  "result": null,
  "error": null,
  "webhook_url": null,
  "webhook_delivered": null
}
```

### Step 2: Poll for Results (Interactive or Scripted)

```bash
TASK_ID="a1b2c3d4-e5f6-7890-abcd-ef1234567890"

while true; do
  STATUS=$(curl -s "http://localhost:8000/api/tasks/$TASK_ID" | jq -r '.status')
  echo "[$(date +'%T')] Task status: $STATUS"
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    curl -s "http://localhost:8000/api/tasks/$TASK_ID" | jq .
    break
  fi
  sleep 3
done
```

---

## 3. Prompt & Schema Crafting Best Practices

The service pairs LLM visual navigation with strict JSON Schema post-processing. To maximize extraction reliability, follow these guidelines.

### Separation of Concerns: Navigation vs. Extraction
* **Prompt (Navigation Goal):** Tell the agent **where to go** and **what actions to take** (clicking, scrolling, typing, pagination).
* **Extraction Schema:** Define **what fields to capture** and **what primitive data types to enforce**.

> **Operator Tip:** Do not instruct the prompt to output JSON. The agent automatically runs structured schema extraction once the navigation goal finishes.

### Pattern A: Simple Entity Lists

```json
{
  "url": "https://news.ycombinator.com/jobs",
  "prompt": "Extract the first 5 job listings shown on the page",
  "extraction_schema": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "company": {"type": "string"},
        "title": {"type": "string"}
      },
      "required": ["company", "title"]
    }
  }
}
```

### Pattern B: Multi-Step Actions & Pagination

When page transitions are required, specify sequential navigation milestones explicitly:

```json
{
  "url": "https://news.ycombinator.com",
  "prompt": "Scroll to the bottom of the page, click the 'More' link to navigate to page 2, and extract the top 3 items on page 2",
  "extraction_schema": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "title": {"type": "string"},
        "url": {"type": "string"}
      },
      "required": ["title", "url"]
    }
  }
}
```

### Pattern C: Strict Numerical Coercion & Dense Tables

Specify integer or float types in the schema to ensure numerical strings are coerced cleanly:

```json
{
  "url": "https://en.wikipedia.org/wiki/List_of_highest-grossing_films",
  "prompt": "Extract the top 5 highest-grossing films from the primary table",
  "extraction_schema": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "rank": {"type": "integer"},
        "title": {"type": "string"},
        "year": {"type": "integer"},
        "lifetime_gross": {"type": "string"}
      },
      "required": ["rank", "title", "year", "lifetime_gross"]
    }
  }
}
```

### Pattern D: Nested Structures

```json
{
  "url": "https://news.ycombinator.com/best",
  "prompt": "Extract the top 3 stories including their ranking, author, and engagement metrics",
  "extraction_schema": {
    "type": "object",
    "properties": {
      "stories": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "rank": {"type": "integer"},
            "title": {"type": "string"},
            "author": {"type": "string"},
            "metrics": {
              "type": "object",
              "properties": {
                "points": {"type": "integer"},
                "comments_count": {"type": "integer"}
              },
              "required": ["points", "comments_count"]
            }
          },
          "required": ["rank", "title", "author", "metrics"]
        }
      }
    },
    "required": ["stories"]
  }
}
```

---

## 4. Working with Authenticated Sessions (CDP Attachment)

When scraping behind logins, SSO, Multi-Factor Authentication (MFA), or bot mitigation services (Cloudflare, DataDome), attach Skyvern directly to an existing Google Chrome instance.

### Step 1: Start Chrome in Debugging Mode

Execute the launcher script in a separate terminal:

```bash
cd ~/Downloads/skyvern-suite
./scripts/launch_debug_chrome.sh
```

This launches Chrome with `--remote-debugging-port=9222` using an isolated local profile:
`~/Library/Application Support/Google/Chrome/SkyvernProfile`

### Step 2: Log In Manually Once

1. In the newly launched Chrome window, navigate to your target site (e.g., GitHub, internal dashboard, CRM).
2. Complete your login and satisfy any 2FA/MFA prompts manually.
3. Keep the browser window open. Your cookies and local tokens persist inside the `SkyvernProfile` directory across sessions.

### Step 3: Check CDP Connectivity

Confirm that Skyvern detects the browser:

```bash
curl -s "http://localhost:8000/api/cdp/status" | jq .
```

Verify the response shows `"available": true`:
```json
{
  "available": true,
  "endpoint": "http://localhost:9222",
  "browser": "Chrome/157.0.8103.51",
  "protocol_version": "1.3"
}
```

### Step 4: Dispatch Tasks to the Authenticated Profile

Include `"cdp_url": "http://localhost:9222"` in the request:

```bash
curl -X POST "http://localhost:8000/api/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://github.com/settings/profile",
    "prompt": "Extract the logged-in user profile details",
    "cdp_url": "http://localhost:9222",
    "extraction_schema": {
      "type": "object",
      "properties": {
        "username": {"type": "string"},
        "email": {"type": "string"}
      },
      "required": ["username"]
    }
  }' | jq .
```

* Skyvern attaches directly to your active browser window.
* When the task completes, Skyvern detaches without closing your tabs (`preserve_external_browser=True`).

---

## 5. Webhook Integration (Event-Driven Notifications)

Instead of continuous client polling, provide a `webhook_url` and optional `webhook_secret` to receive push notifications when tasks finish.

### Submitting a Task with Webhook Configuration

```bash
curl -X POST "http://localhost:8000/api/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news.ycombinator.com",
    "prompt": "Extract top stories",
    "extraction_schema": {
      "type": "array",
      "items": {"type": "object", "properties": {"title": {"type": "string"}}}
    },
    "webhook_url": "https://api.yourdomain.com/webhooks/skyvern",
    "webhook_secret": "my_production_secret_token"
  }' | jq .
```

### Incoming Webhook Callback Format

When execution finishes, the service sends an HTTP `POST` to your callback URL:

* **HTTP Method:** `POST`
* **Header `Content-Type`:** `application/json`
* **Header `X-Skyvern-Event`:** `task.completed` or `task.failed`
* **Header `X-Skyvern-Signature`:** `sha256=<hex_digest>` (when secret is provided)

#### Example Webhook Payload
```json
{
  "task_id": "4b98dd55-6c5a-4321-b5de-7ff6330abf2c",
  "task_type": "execute",
  "status": "completed",
  "result": {
    "run_id": "tsk_597306434432952324",
    "status": "completed",
    "output": [
      {"title": "The MacBook Pro's OLED PWM Problem"},
      {"title": "SpaceX catches the Starship to end Flight 15"}
    ],
    "downloaded_files": [],
    "recording_url": "/api/artifacts/4b98dd55-6c5a-4321-b5de-7ff6330abf2c/recording.mp4",
    "screenshot_urls": [
      "/api/artifacts/4b98dd55-6c5a-4321-b5de-7ff6330abf2c/screenshots/action_0.png"
    ],
    "created_at": "2026-11-15T14:54:01.084553",
    "finished_at": "2026-11-15T14:54:18.182699",
    "step_count": 1,
    "errors": []
  },
  "error": null,
  "webhook_url": "https://api.yourdomain.com/webhooks/skyvern",
  "webhook_delivered": true,
  "webhook_delivery_attempts": 1,
  "webhook_delivered_at": "2026-11-15T14:54:18.892011+00:00",
  "cdp_url": "http://localhost:9222"
}
```

### Verifying the HMAC Signature (Python / FastAPI)

```python
import hashlib
import hmac
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
WEBHOOK_SECRET = "my_production_secret_token"


@app.post("/webhooks/skyvern")
async def handle_skyvern_webhook(
    request: Request,
    x_skyvern_event: str = Header(None),
    x_skyvern_signature: str = Header(None),
):
    body = await request.body()

    if x_skyvern_signature:
        expected = (
            "sha256="
            + hmac.new(
                WEBHOOK_SECRET.encode(), body, hashlib.sha256
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, x_skyvern_signature):
            raise HTTPException(
                status_code=403, detail="Invalid signature verification."
            )

    payload = await request.json()
    print(f"Received event {x_skyvern_event} for task {payload['task_id']}")
    print(f"Extracted records: {payload['result']['output']}")
    return {"status": "accepted"}
```

---

## 6. Accessing Artifacts & Debug Media

Artifacts are persisted under `artifacts/{task_id}/` on the server and exposed via the HTTP API.

### Listing Artifacts on Disk

```bash
TASK_ID="4b98dd55-6c5a-4321-b5de-7ff6330abf2c"
ls -la ~/Downloads/skyvern-suite/artifacts/$TASK_ID/
```

Layout:
```text
├── extraction.json         # Extracted structured data
├── task_metadata.json      # Step counts, timestamps, and status
├── recording.mp4           # Full session MP4 (headless runs)
└── screenshots/
    └── action_0.png        # Action capture frame
```

### Downloading Media via the API

* **Extracted JSON:**
  ```bash
  curl -s "http://localhost:8000/api/artifacts/$TASK_ID/extraction.json" | jq .
  ```
* **Action Screenshot:**
  ```bash
  curl -O "http://localhost:8000/api/artifacts/$TASK_ID/screenshots/action_0.png"
  open action_0.png
  ```
* **Full Session Recording (Headless Tasks):**
  ```bash
  curl -O "http://localhost:8000/api/artifacts/$TASK_ID/recording.mp4"
  open recording.mp4
  ```

---

## 7. Operational Troubleshooting

| Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| `HTTP 400: CDP endpoint '...' is unreachable` | Task specified `cdp_url`, but Chrome is not running or listening on port 9222. | Run `./scripts/launch_debug_chrome.sh` and verify with `curl http://localhost:8000/api/cdp/status`. |
| Task stays in `queued` status | Prior task is still navigating or interacting with a page. | The internal `AsyncTaskQueue` processes requests sequentially to prevent browser collisions. The task will begin once the active run completes. |
| `recording_url` is `null` on CDP tasks | Attached Chrome sessions operate directly in the desktop browser rather than an isolated Playwright container. | Expected behavior. Screencast video capture is skipped on attached desktop sessions; individual action screenshots remain captured in `screenshot_urls`. |
| Extracted array contains empty output (`[]`) | The prompt directed the agent to a view where schema items were not visible, or the page changed layout. | Inspect the captured `screenshot_urls` at `/api/artifacts/{task_id}/screenshots/action_0.png` to review what the agent observed. |
| Webhook delivery fails (`webhook_delivered: false`) | Target server returned 5xx errors, timed out after 10s, or network was unreachable. | Check `webhook_delivery_attempts` in task details. The dispatcher retries up to 3 times with exponential backoff before marking delivery failed. |