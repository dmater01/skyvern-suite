#!/usr/bin/env python3
"""
Royal Caribbean Cruise Rate Polling Runner (Deep-Link Optimized).

Bypasses homepage modal dismissal, port selection, and destination search by
navigating directly to the pre-filtered Royal Caribbean search results grid.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

API_BASE = os.getenv("SKYVERN_SUITE_API", "http://localhost:8000")
CDP_URL = os.getenv("CDP_URL", "http://localhost:9222")

ROYAL_CARIBBEAN_DEEP_LINK_URL = (
    "https://www.royalcaribbean.com/cruises"
    "?departureCode_FLL=true"
    "&destinationCode_CARIB=true"
    "&dates_minDate=04%2F01%2F2027"
    "&dates_maxDate=04%2F30%2F2027"
)

ROYAL_CARIBBEAN_PROMPT = (
    "Locate the 6-night Western Caribbean sailing on Adventure of the Seas departing April 18, 2027. "
    "Click 'View Dates' or 'Explore This Cruise' and navigate to stateroom pricing. "
    "STOP immediately on the stateroom pricing page showing Interior, Ocean View, Balcony, and Suite rates. "
    "DO NOT proceed to checkout or enter traveler details."
)

ROYAL_CARIBBEAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "ship_name": {"type": "string"},
        "itinerary_title": {"type": "string"},
        "departure_date": {"type": "string"},
        "nights": {"type": "integer"},
        "ports_of_call": {
            "type": "array",
            "items": {"type": "string"},
        },
        "interior_price_usd": {"type": "number"},
        "balcony_price_usd": {"type": "number"},
        "reached_pricing_screen": {"type": "boolean"},
    },
    "required": [
        "ship_name",
        "departure_date",
        "interior_price_usd",
        "balcony_price_usd",
        "reached_pricing_screen",
    ],
}


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


def build_cruise_task_payload(
    cdp_url: str = CDP_URL,
    max_steps: int = 15,
    run_with: Optional[str] = "code",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "url": ROYAL_CARIBBEAN_DEEP_LINK_URL,
        "prompt": ROYAL_CARIBBEAN_PROMPT,
        "cdp_url": cdp_url,
        "max_steps": max_steps,
        "extraction_schema": ROYAL_CARIBBEAN_SCHEMA,
    }
    if run_with:
        payload["run_with"] = run_with
    return payload


def poll_cruise_rates(
    api_base: str = API_BASE,
    cdp_url: str = CDP_URL,
    max_steps: int = 15,
    run_with: Optional[str] = "code",
    poll_interval: float = 2.0,
) -> Dict[str, Any]:
    cdp_status = http_json("GET", f"{api_base}/api/cdp/status")
    if not cdp_status.get("available"):
        raise RuntimeError(f"CDP is not available at {cdp_url}: {cdp_status}")

    payload = build_cruise_task_payload(
        cdp_url=cdp_url,
        max_steps=max_steps,
        run_with=run_with,
    )
    queued = http_json("POST", f"{api_base}/api/execute", payload=payload)
    task_id = queued["task_id"]
    print(f"[poll_cruise_rates] Dispatched deep-link task: {task_id}")

    while True:
        record = http_json("GET", f"{api_base}/api/tasks/{task_id}")
        status = record.get("status")
        if status in ("completed", "failed"):
            return record
        time.sleep(poll_interval)


def main() -> int:
    try:
        record = poll_cruise_rates()
    except Exception as exc:
        print(f"[poll_cruise_rates] ERROR: {exc}", file=sys.stderr)
        return 1

    result = record.get("result") or {}
    output = result.get("output") or result.get("extracted_information") or {}
    print(json.dumps(output, indent=2))
    return 0 if record.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
