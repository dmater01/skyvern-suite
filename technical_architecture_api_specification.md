# Technical Architecture & API Specification

The Skyvern Automation Suite provides an asynchronous, hardened HTTP execution wrapper around the embedded Skyvern autonomous browser engine on macOS. It converts unstructured natural language browser commands and JSON Schema targets into deterministic structured data extraction, media capture, and event-driven notifications.

---

## 1. System Architecture

The service consists of a FastAPI interface, an asynchronous task queue with singleton execution semantics, an embedded Skyvern agent, a persistent artifact manager, an HMAC-secured webhook dispatcher, and Chrome DevTools Protocol (CDP) session attachment.

```
                      ┌────────────────────────────────────────┐
                      │             HTTP Client                │
                      │  (curl / httpx / external integration) │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │          FastAPI Application           │
                      │         (Uvicorn on Port 8000)         │
                      └───────────────────┬────────────────────┘
                                          │
                        ┌─────────────────┴─────────────────┐
                        │ Enqueue Task                      │ Poll / Status
                        ▼                                   ▼
         ┌──────────────────────────────┐       ┌──────────────────────────────┐
         │     InMemoryTaskStore        │◀──────┤    GET /api/tasks/{task_id}  │
         │  (Task Status & Metadata)    │       │    GET /api/cdp/status       │
         └──────────────┬───────────────┘       └──────────────────────────────┘
                        │
                        ▼
         ┌──────────────────────────────┐
         │       AsyncTaskQueue         │
         │   (asyncio.Queue Worker)     │
         └──────────────┬───────────────┘
                        │
                        ▼ (Serialized Execution)
         ┌─────────────────────────────────────────────────────────────────────┐
         │                      Execution Pipeline                             │
         │                                                                     │
         │   1. Validate CDP reachability (if cdp_url configured)              │
         │   2. Execute autonomous task via embedded Skyvern singleton         │
         │      • LLM Provider: LiteLLM (openai/responses/gpt-6.1-terra)       │
         │      • Target Browser: Isolated Playwright OR Live Chrome (CDP)     │
         │   3. Persist media artifacts & update download URLs                 │
         │   4. Dispatch HMAC-signed webhook callback (if configured)          │
         └──────────────┬───────────────────┬───────────────────┬──────────────┘
                        │                   │                   │
                        ▼                   ▼                   ▼
         ┌───────────────────────┐ ┌─────────────────┐ ┌───────────────────────┐
         │    ArtifactManager    │ │ WebhookDelivery │ │    Active Browser     │
         │ artifacts/{task_id}/  │ │ HMAC-SHA256     │ │ Isolated Chromium OR  │
         │ • extraction.json     │ │ 3x Exponential  │ │ Google Chrome :9222   │
         │ • task_metadata.json  │ │ Backoff         │ │ (External process     │
         │ • recording.mp4       │ └─────────────────┘ │  preserved on finish) │
         │ • screenshots/*.png   │                     └───────────────────────┘
         └───────────────────────┘
```

---

## 2. Component Design & Lifecycle Management

### FastAPI Lifespan & Concurrency Control
Embedded browser engines and SQLite metadata stores are prone to database lock contention and display server collisions under concurrent load. The suite implements an asynchronous task queue (`AsyncTaskQueue`) initialized within the FastAPI lifespan context:

1. **Queue Serialization:** Client requests submitted to `POST /api/execute` or `POST /api/introspect` receive an immediate HTTP 200 response with a `task_id` and initial status `queued`.
2. **Worker Pool:** A dedicated background worker consumes tasks sequentially from an internal `asyncio.Queue`. This guarantees that only one Skyvern automation task executes on a given browser profile or local SQLite state at any time.
3. **Graceful Teardown:** On application shutdown, the worker drains pending tasks, flushes state, and cleanly releases browser contexts via `safe_aclose()`.

### External Chrome CDP Session Preservation
When executing against an attached Chrome instance via `cdp_url`:
* `Skyvern.run_task(...)` receives `browser_address="http://localhost:9222"`.
* The internal `start_fresh_browser` parameter is set to `False` on the underlying `TaskRunRequest` model.
* During client closure, `preserve_external_browser=True` prevents the server from sending `SIGTERM` or closing DevTools connections to the host Chrome process, preserving user cookies, tabs, and login state across tasks.

---

## 3. Configuration & Environment Variables

All settings load from environment variables or a root `.env` file via `src/core/config.py`:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `HOST` | string | `0.0.0.0` | Bind address for the FastAPI server. |
| `PORT` | integer | `8000` | Bind port for the FastAPI server. |
| `SKYVERN_API_KEY` | string | `""` | Optional internal API key guard for incoming endpoints. |
| `LLM_MODEL` | string | `openai/responses/gpt-6.1-terra` | Model identifier dispatched via LiteLLM. |
| `ARTIFACTS_DIR` | string | `artifacts` | Relative or absolute path for persistent artifact storage. |
| `ARTIFACT_RETENTION_DAYS` | integer | `7` | Retention threshold before artifact bundles are pruned. |
| `AUTO_PRUNE_ARTIFACTS` | boolean | `true` | Scans and cleans stale artifacts on server startup/shutdown. |
| `DEFAULT_CDP_URL` | string | `http://localhost:9222` | Default target when attaching to an existing browser. |
| `ENABLE_CDP` | boolean | `false` | When true, defaults all execution requests to `DEFAULT_CDP_URL`. |

---

## 4. API Specification

### Endpoints Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/execute` | Enqueue an autonomous browser navigation and extraction task. |
| `POST` | `/api/introspect` | Enqueue a single-page introspection and schema extraction task. |
| `GET` | `/api/tasks/{task_id}` | Retrieve status, results, and artifact URLs for a specific task. |
| `GET` | `/api/artifacts/{task_id}/{file_name}` | Download or stream persisted screenshots, videos, and JSON files. |
| `GET` | `/api/cdp/status` | Probe the reachability and version of the local Chrome DevTools port. |
| `GET` | `/healthz` | Service health check. |

---

### `POST /api/execute`

Submits an autonomous workflow task.

#### Request Headers
* `Content-Type: application/json`

#### Request Body Schema
```json
{
  "url": "https://news.ycombinator.com",
  "prompt": "Extract the top 2 stories from the front page.",
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
  },
  "max_steps": 30,
  "cdp_url": "http://localhost:9222",
  "webhook_url": "https://example.com/api/webhooks/skyvern",
  "webhook_secret": "secure_hmac_secret_key"
}
```

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `url` | string | Yes | Fully qualified URL where execution begins. |
| `prompt` | string | Yes | Plaintext instruction guiding browser interactions. |
| `extraction_schema` | object | No | JSON Schema defining the structured output format. |
| `max_steps` | integer | No | Maximum navigation/action loop steps (default: `30`). |
| `cdp_url` | string | No | Debugging URL of a running Chrome instance (e.g. `http://localhost:9222`). |
| `webhook_url` | string | No | Target URL for asynchronous completion callback. |
| `webhook_secret` | string | No | Secret string used to generate HMAC-SHA256 signatures. |

#### Response (`200 OK`)
```json
{
  "task_id": "4b98dd55-6c5a-4321-b5de-7ff6330abf2c",
  "task_type": "execute",
  "status": "queued",
  "result": null,
  "error": null,
  "webhook_url": "https://example.com/api/webhooks/skyvern",
  "webhook_delivered": null,
  "webhook_delivery_attempts": 0,
  "webhook_delivered_at": null,
  "cdp_url": "http://localhost:9222"
}
```

---

### `GET /api/tasks/{task_id}`

Retrieves task progress, structured outputs, and media artifact URLs.

#### Response (`200 OK` - Completed Task)
```json
{
  "task_id": "4b98dd55-6c5a-4321-b5de-7ff6330abf2c",
  "task_type": "execute",
  "status": "completed",
  "result": {
    "run_id": "tsk_597306434432952324",
    "status": "completed",
    "output": [
      {
        "title": "The MacBook Pro's OLED PWM Problem",
        "url": "https://example.com/oled"
      },
      {
        "title": "SpaceX catches the Starship to end Flight 15",
        "url": "https://spacex.com/starship"
      }
    ],
    "downloaded_files": [],
    "recording_url": "/api/artifacts/4b98dd55-6c5a-4321-b5de-7ff6330abf2c/recording.mp4",
    "recording_archived": false,
    "screenshot_urls": [
      "/api/artifacts/4b98dd55-6c5a-4321-b5de-7ff6330abf2c/screenshots/action_0.png"
    ],
    "failure_reason": null,
    "created_at": "2026-11-15T14:54:01.084553",
    "finished_at": "2026-11-15T14:54:18.182699",
    "step_count": 1,
    "errors": []
  },
  "error": null,
  "webhook_url": "https://example.com/api/webhooks/skyvern",
  "webhook_delivered": true,
  "webhook_delivery_attempts": 1,
  "webhook_delivered_at": "2026-11-15T14:54:18.892011+00:00",
  "cdp_url": "http://localhost:9222"
}
```

---

### `GET /api/artifacts/{task_id}/{file_name:path}`

Streams or downloads persistent media generated during a run.

* **Path Security:** Requests containing relative traversal sequences (`..`) or attempting to escape the configured `ARTIFACTS_DIR` return `403 Forbidden`.
* **Content Types Handled:**
  * `.mp4` $\rightarrow$ `video/mp4`
  * `.png` $\rightarrow$ `image/png`
  * `.json` $\rightarrow$ `application/json`

---

### `GET /api/cdp/status`

Probes connectivity against a specified Chrome DevTools Protocol port.

#### Query Parameters
* `cdp_url` (optional, default: `http://localhost:9222`)

#### Response (`200 OK` - Active Endpoint)
```json
{
  "available": true,
  "endpoint": "http://localhost:9222",
  "browser": "Chrome/157.0.8103.51",
  "protocol_version": "1.3"
}
```

#### Response (`200 OK` - Inactive Endpoint)
```json
{
  "available": false,
  "endpoint": "http://localhost:9222",
  "browser": null,
  "protocol_version": null
}
```

---

## 5. Webhook System Architecture

The webhook dispatcher (`src/core/webhook.py`) runs asynchronously outside the request-response cycle upon task completion or failure.

### HMAC-SHA256 Signature Header
If a `webhook_secret` is supplied during task submission:
1. The serialized task JSON body is transformed into raw bytes.
2. An HMAC is calculated using SHA-256 and the shared secret.
3. The resulting hex digest is sent in the `X-Skyvern-Signature` header:

```http
POST /api/webhooks/skyvern HTTP/1.1
Host: example.com
Content-Type: application/json
X-Skyvern-Event: task.completed
X-Skyvern-Signature: sha256=f7933bcbe26bbead8bafeb59f4b0c4f8bce65d89ea7bcc2786fccfe6a8f62b01
```

### Verification Implementation Example (Python / FastAPI)
```python
import hashlib
import hmac
from fastapi import Header, HTTPException, Request

WEBHOOK_SECRET = "secure_hmac_secret_key"


async def verify_signature(
    request: Request, x_skyvern_signature: str = Header(None)
):
    if not x_skyvern_signature:
        raise HTTPException(
            status_code=400, detail="Missing signature header."
        )

    body = await request.body()
    expected_sig = (
        "sha256="
        + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    )

    if not hmac.compare_digest(expected_sig, x_skyvern_signature):
        raise HTTPException(status_code=403, detail="Invalid HMAC signature.")
```

### Delivery Retry Policy
The dispatcher executes using `httpx.AsyncClient(timeout=10.0)`:
* **Max Attempts:** 3
* **Backoff Strategy:** Exponential with factor $2\times$ ($1.0\text{s} \rightarrow 2.0\text{s} \rightarrow 4.0\text{s}$).
* **Retry Triggers:** Network timeouts, connection resets, and HTTP status codes $\ge 500$. Client errors ($4\text{xx}$) terminate delivery immediately.

---

## 6. Artifact Storage & Retention Engine

### Directory Layout
When Skyvern completes an action loop, the `ArtifactManager` intercepts temporary output files from `/var/folders/...` and moves them into a deterministic directory structure:

```text
artifacts/
└── 4b98dd55-6c5a-4321-b5de-7ff6330abf2c/
    ├── extraction.json         # Raw extracted schema payload
    ├── task_metadata.json       # Run IDs, step counts, duration, and status
    ├── recording.mp4           # Video capture (headless runs)
    └── screenshots/
        ├── action_0.png        # Action observation snapshots
        └── action_1.png
```

### TTL & Automatic Pruning
To prevent unbounded disk usage from video recordings and high-resolution screenshots, `ArtifactManager.prune_stale_artifacts(retention_days=7)` executes:
1. Calculates cutoff timestamp: $\text{Cutoff} = \text{Current Time} - (\text{retention\_days} \times 86400\text{s})$.
2. Iterates over subdirectories in `ARTIFACTS_DIR`.
3. Inspects directory modified time (`mtime`).
4. Recursively removes directories exceeding the threshold.

---

## 7. Verification & Automated Test Suite

The suite includes both unit/integration tests (`pytest`) and end-to-end integration scripts.

```bash
# Run complete unit test battery
.venv/bin/pytest tests/ -v

# Run concurrency, degradation, and complex schema verification
.venv/bin/python scripts/run_validation_battery.py

# Run webhook receiver and HMAC verification
.venv/bin/python scripts/test_webhook_delivery.py

# Run artifact persistence and download endpoint tests
.venv/bin/python scripts/test_artifact_pipeline.py

# Run live Chrome CDP attachment verification
.venv/bin/python scripts/test_cdp_session.py
```