# Skyvern Automation Suite: Operator & User Guide

A practical manual for operating, dispatching, and monitoring autonomous web extraction workflows using the local Skyvern service.

---

## 1. Quickstart

### Starting the Service
From the repository root with the active virtual environment:

```bash
cd ~/Downloads/skyvern-suite
source .venv/bin/activate
uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
```
*(Alternatively: `uvicorn src.api.server:api --host 0.0.0.0 --port 8000 --env-file .env --reload`)*

### Verifying Service Health
```bash
curl -s http://localhost:8000/healthz | jq .
```

---

## 2. Dispatching & Polling Extraction Tasks

### Submitting a Task (`POST /api/execute`)
Submit a target URL, natural language prompt, and strict JSON extraction schema:

```bash
TASK_ID=$(curl -s -X POST "http://localhost:8000/api/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news.ycombinator.com/best",
    "prompt": "Extract the first 3 stories listed on this page.",
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
  }' | jq -r '.task_id')

echo "Enqueued Task ID: $TASK_ID"
```

### Polling Task Status (`GET /api/tasks/{task_id}`)
```bash
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

## 3. Working with Persisted Artifacts

Every completed or failed task automatically archives its run artifacts into `artifacts/{task_id}/`:
- `artifacts/{task_id}/extraction.json` — Extracted structured JSON output
- `artifacts/{task_id}/recording.mp4` — Full browser session video recording
- `artifacts/{task_id}/screenshots/action_0.png` — Action step screenshots
- `artifacts/{task_id}/task_metadata.json` — Run timings, step counts, and artifact paths

### Downloading Artifacts via HTTP
```bash
# Download the MP4 session recording
curl -s -o recording.mp4 "http://localhost:8000/api/artifacts/$TASK_ID/recording.mp4"

# Download the first step screenshot
curl -s -o action_0.png "http://localhost:8000/api/artifacts/$TASK_ID/screenshots/action_0.png"

# View extracted JSON
curl -s "http://localhost:8000/api/artifacts/$TASK_ID/extraction.json" | jq .
```

---

## 4. Authenticated Scraping with Chrome DevTools Protocol (CDP)

To run tasks inside an existing Chrome session (preserving logins, cookies, and MFA state):

### Step 1: Launch Chrome with Remote Debugging
```bash
./scripts/launch_debug_chrome.sh
```
Log into any target sites in the opened Chrome window.

### Step 2: Check CDP Availability
```bash
curl -s "http://localhost:8000/api/cdp/status?cdp_url=http://localhost:9222" | jq .
```

### Step 3: Dispatch a Task Attached to Chrome
```bash
curl -s -X POST "http://localhost:8000/api/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news.ycombinator.com",
    "prompt": "Extract the first 2 story titles from the front page.",
    "cdp_url": "http://localhost:9222",
    "extraction_schema": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"]
      }
    }
  }' | jq .
```
When the task finishes, Skyvern detaches cleanly without closing your Chrome window or tabs.

---

## 5. Receiving Signed Webhook Callbacks

Include `webhook_url` and `webhook_secret` in your `POST /api/execute` payload to receive event-driven notifications:

```json
{
  "url": "https://news.ycombinator.com",
  "prompt": "Extract the first 2 story titles.",
  "webhook_url": "http://127.0.0.1:8088/webhook",
  "webhook_secret": "my_webhook_secret"
}
```

### Verifying `X-Skyvern-Signature` in Python
```python
import hashlib
import hmac

def verify_skyvern_webhook(raw_body: bytes, secret: str, signature_header: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

---

## 6. Running the Automated Verification Suite

```bash
# Unit & integration tests (pytest)
.venv/bin/pytest tests/

# End-to-end validation battery (Concurrency, Negative Degradation, Complex Nested Schema)
.venv/bin/python scripts/run_validation_battery.py

# End-to-end HMAC-SHA256 webhook callback test
.venv/bin/python scripts/test_webhook_delivery.py

# End-to-end artifact persistence & streaming test
.venv/bin/python scripts/test_artifact_pipeline.py

# End-to-end Chrome CDP session attachment test
.venv/bin/python scripts/test_cdp_session.py
```
