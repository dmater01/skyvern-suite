import pytest
from src.api.schemas import (
    DynamicAppSpec,
    ExecuteTaskRequest,
    InputControlSpec,
    TaskStatusResponse,
)

def test_dynamic_app_spec_validation():
    input_item = InputControlSpec(
        field_name="search_query",
        label="Search Query",
        input_type="text",
        is_required=True
    )
    spec = DynamicAppSpec(
        app_title="Test App",
        description="Demo application description",
        required_inputs=[input_item],
        prompt_template="Search for {search_query}"
    )
    assert spec.app_title == "Test App"
    assert spec.required_inputs[0].field_name == "search_query"


def test_execute_request_and_task_status_webhook_fields():
    req = ExecuteTaskRequest(
        url="https://news.ycombinator.com",
        prompt="Extract stories",
        webhook_url="http://127.0.0.1:8088/webhook",
        webhook_secret="secret_123",
    )
    assert req.webhook_url == "http://127.0.0.1:8088/webhook"
    assert req.webhook_secret == "secret_123"

    status_resp = TaskStatusResponse(
        task_id="task-1",
        task_type="execute",
        status="queued",
        webhook_url=req.webhook_url,
    )
    assert status_resp.webhook_delivered is None
    assert status_resp.webhook_delivery_attempts == 0


def test_bookstore_canary_payload_and_schema():
    from scripts.test_bookstore_canary import (
        BAM_EXTRACTION_SCHEMA,
        BAM_SEARCH_URL,
        build_bookstore_task_payload,
    )

    payload = build_bookstore_task_payload()
    assert payload["url"] == BAM_SEARCH_URL
    assert payload["run_with"] == "code"
    assert payload["cdp_url"] == "http://localhost:9222"
    assert "reached_product_page" in BAM_EXTRACTION_SCHEMA["required"]
    assert set(BAM_EXTRACTION_SCHEMA["properties"].keys()) == {
        "title",
        "author",
        "format",
        "price_usd",
        "in_stock",
        "reached_product_page",
    }
