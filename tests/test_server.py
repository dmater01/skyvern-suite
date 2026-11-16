import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from src.api.server import api, task_queue, dispatch_webhook


@pytest_asyncio.fixture(autouse=True)
async def reset_task_queue():
    task_queue.tasks.clear()
    await task_queue.start()
    yield
    await task_queue.stop()
    task_queue.tasks.clear()


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_execute_enqueues_task_and_triggers_webhook():
    mock_result = {"extracted_information": {"jobs": ["Backend Engineer"]}}

    with (
        patch(
            "src.api.server.run_autonomous_action",
            new=AsyncMock(return_value=mock_result),
        ) as mock_exec,
        patch(
            "src.api.server.dispatch_webhook",
            new=AsyncMock(return_value=True),
        ) as mock_webhook,
    ):
        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/execute",
                json={
                    "url": "https://example.com/jobs",
                    "prompt": "Extract backend jobs",
                    "extraction_schema": {"type": "object"},
                    "webhook_url": "https://hooks.example.com/callback",
                },
            )
            assert resp.status_code == 202
            queued_data = resp.json()
            task_id = queued_data["task_id"]
            assert queued_data["task_type"] == "execute"

            await task_queue.wait_for_all()

            status_resp = await client.get(f"/api/tasks/{task_id}")
            assert status_resp.status_code == 200
            final_data = status_resp.json()

        assert final_data["status"] == "completed"
        assert final_data["result"] == mock_result
        assert final_data["webhook_delivered"] is True
        mock_exec.assert_awaited_once_with(
            "https://example.com/jobs",
            "Extract backend jobs",
            {"type": "object"},
        )
        mock_webhook.assert_awaited_once()
        webhook_url_arg, payload_arg = mock_webhook.await_args.args
        assert webhook_url_arg == "https://hooks.example.com/callback"
        assert payload_arg["task_id"] == task_id
        assert payload_arg["status"] == "completed"
        assert payload_arg["result"] == mock_result


@pytest.mark.asyncio
async def test_introspect_enqueues_task_and_completes():
    mock_spec = {
        "app_title": "Search App",
        "description": "Search items",
        "required_inputs": [],
        "prompt_template": "Search now",
    }

    with patch(
        "src.api.server.introspect_webpage",
        new=AsyncMock(return_value=mock_spec),
    ) as mock_introspect:
        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/introspect",
                json={
                    "url": "https://example.com",
                    "objective": "Search items",
                },
            )
            assert resp.status_code == 202
            task_id = resp.json()["task_id"]

            await task_queue.wait_for_all()

            status_resp = await client.get(f"/api/tasks/{task_id}")
            assert status_resp.status_code == 200
            final_data = status_resp.json()

        assert final_data["status"] == "completed"
        assert final_data["result"] == mock_spec
        mock_introspect.assert_awaited_once_with("https://example.com", "Search items")


@pytest.mark.asyncio
async def test_failed_task_records_error_and_fires_webhook():
    with (
        patch(
            "src.api.server.run_autonomous_action",
            new=AsyncMock(side_effect=RuntimeError("Browser crashed")),
        ),
        patch(
            "src.api.server.dispatch_webhook",
            new=AsyncMock(return_value=True),
        ) as mock_webhook,
    ):
        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/execute",
                json={
                    "url": "https://example.com",
                    "prompt": "Run task",
                    "webhook_url": "https://hooks.example.com/error",
                },
            )
            task_id = resp.json()["task_id"]
            await task_queue.wait_for_all()

            status_resp = await client.get(f"/api/tasks/{task_id}")
            final_data = status_resp.json()

        assert final_data["status"] == "failed"
        assert "Browser crashed" in final_data["error"]
        assert final_data["webhook_delivered"] is True
        mock_webhook.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_unknown_task_returns_404():
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/tasks/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_webhook_retries_on_failure():
    mock_response_ok = MagicMock()
    mock_response_ok.status_code = 200
    mock_response_ok.raise_for_status.return_value = None

    mock_post = AsyncMock(
        side_effect=[RuntimeError("Connection reset"), mock_response_ok]
    )

    with patch("src.api.server.httpx.AsyncClient.post", new=mock_post):
        delivered = await dispatch_webhook(
            "https://hooks.example.com/callback",
            {"task_id": "123", "status": "completed"},
            max_retries=2,
            base_delay=0.01,
        )

    assert delivered is True
    assert mock_post.await_count == 2


@pytest.mark.asyncio
async def test_deliver_webhook_hmac_signature_and_headers():
    import hashlib
    import hmac
    import json
    from src.api.schemas import TaskStatusResponse
    from src.core.webhook import deliver_webhook

    mock_response_ok = MagicMock()
    mock_response_ok.status_code = 200
    mock_response_ok.raise_for_status.return_value = None
    mock_post = AsyncMock(return_value=mock_response_ok)

    record = TaskStatusResponse(
        task_id="tsk_hmac",
        task_type="execute",
        status="completed",
        result={"output": [{"title": "HN"}]},
        webhook_url="http://127.0.0.1:8088/webhook",
    )
    payload = record.model_dump()
    secret = "test_secret_key"

    with patch("src.core.webhook.httpx.AsyncClient.post", new=mock_post):
        delivered = await deliver_webhook(
            webhook_url="http://127.0.0.1:8088/webhook",
            payload=payload,
            secret=secret,
            max_retries=3,
            base_delay=0.01,
            task_record=record,
        )

    assert delivered is True
    assert record.webhook_delivered is True
    assert record.webhook_delivery_attempts == 1
    assert record.webhook_delivered_at is not None

    call_kwargs = mock_post.await_args.kwargs
    sent_bytes = call_kwargs["content"]
    sent_headers = call_kwargs["headers"]

    expected_sig = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        json.dumps(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert sent_headers["Content-Type"] == "application/json"
    assert sent_headers["X-Skyvern-Event"] == "task.completed"
    assert sent_headers["X-Skyvern-Signature"] == expected_sig
    assert json.loads(sent_bytes.decode("utf-8")) == payload


@pytest.mark.asyncio
async def test_artifact_manager_persist_prune_and_download_endpoint(tmp_path, monkeypatch):
    import os
    import time
    from src.core.artifacts import ArtifactManager

    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts_root))

    # Create mock volatile OS temp files
    volatile_dir = tmp_path / "var_folders_skyvern"
    volatile_dir.mkdir(parents=True, exist_ok=True)
    temp_mp4 = volatile_dir / "temp_recording.mp4"
    temp_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42mockvideo")
    temp_png = volatile_dir / "temp_screenshot.png"
    temp_png.write_bytes(b"\x89PNG\r\n\x1a\nmockimage")

    task_id = "test-artifact-task-1"
    result_payload = {
        "run_id": "tsk_art_1",
        "status": "completed",
        "output": [{"title": "Example Story"}],
        "recording_url": f"file://{temp_mp4}",
        "screenshot_urls": [f"file://{temp_png}"],
    }

    updated = ArtifactManager.persist_task_artifacts(task_id, result_payload)
    assert updated["recording_url"] == f"/api/artifacts/{task_id}/recording.mp4"
    assert updated["screenshot_urls"] == [f"/api/artifacts/{task_id}/screenshots/action_0.png"]

    bundle_dir = artifacts_root / task_id
    assert (bundle_dir / "recording.mp4").is_file()
    assert (bundle_dir / "extraction.json").is_file()
    assert (bundle_dir / "task_metadata.json").is_file()
    assert (bundle_dir / "screenshots" / "action_0.png").is_file()

    # Verify download endpoint
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mp4_resp = await client.get(f"/api/artifacts/{task_id}/recording.mp4")
        assert mp4_resp.status_code == 200
        assert "video/mp4" in mp4_resp.headers["content-type"]
        assert mp4_resp.content == b"\x00\x00\x00\x18ftypmp42mockvideo"

        png_resp = await client.get(f"/api/artifacts/{task_id}/screenshots/action_0.png")
        assert png_resp.status_code == 200
        assert "image/png" in png_resp.headers["content-type"]

    # Verify pruning of stale bundles
    stale_dir = artifacts_root / "stale-task-old"
    stale_dir.mkdir(parents=True, exist_ok=True)
    old_epoch = time.time() - (10 * 86400)
    os.utime(stale_dir, (old_epoch, old_epoch))

    pruned = ArtifactManager.prune_stale_artifacts(retention_days=7)
    assert "stale-task-old" in pruned
    assert not stale_dir.exists()
    assert bundle_dir.exists()


@pytest.mark.asyncio
async def test_cdp_status_endpoint_and_fast_fail_400():
    transport = ASGITransport(app=api)
    with patch(
        "src.api.routes.check_cdp_available",
        new=AsyncMock(
            return_value=(True, {"Browser": "Chrome/131.0.6778.86", "Protocol-Version": "1.3"})
        ),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/cdp/status?cdp_url=http://localhost:9222")
            assert resp.status_code == 200
            data = resp.json()
            assert data == {
                "available": True,
                "endpoint": "http://localhost:9222",
                "browser": "Chrome/131.0.6778.86",
                "protocol_version": "1.3",
            }

        with patch(
            "src.api.server.check_cdp_available",
            new=AsyncMock(return_value=(False, None)),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                bad_resp = await client.post(
                    "/api/execute",
                    json={
                        "url": "https://example.com",
                        "prompt": "Scrape behind login",
                        "cdp_url": "http://localhost:9222",
                    },
                )
                assert bad_resp.status_code == 400
                assert "unreachable" in bad_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_execute_forwards_run_with_and_records_timing_timestamps():
    mock_result = {"status": "completed", "output": {"interior_price_usd": 434}}

    with patch(
        "src.api.server.run_autonomous_action",
        new=AsyncMock(return_value=mock_result),
    ) as mock_exec:
        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/execute",
                json={
                    "url": "https://www.royalcaribbean.com/cruises",
                    "prompt": "Locate sailing",
                    "max_steps": 15,
                    "run_with": "code",
                },
            )
            assert resp.status_code == 202
            task_id = resp.json()["task_id"]
            await task_queue.wait_for_all()

            status_resp = await client.get(f"/api/tasks/{task_id}")
            assert status_resp.status_code == 200
            data = status_resp.json()

    assert data["status"] == "completed"
    assert data["queued_at"] is not None
    assert data["started_at"] is not None
    assert data["completed_at"] is not None
    mock_exec.assert_awaited_once_with(
        "https://www.royalcaribbean.com/cruises",
        "Locate sailing",
        None,
        max_steps=15,
        run_with="code",
    )



