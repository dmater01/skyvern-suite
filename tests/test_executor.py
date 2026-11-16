import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.client import close_skyvern, init_skyvern
from src.core.executor import run_autonomous_action


@pytest.mark.asyncio
async def test_run_autonomous_action_waits_for_completion():
    await close_skyvern()
    expected_output = {
        "task_id": "tsk_123",
        "status": "completed",
        "extracted_information": {"items": ["item1", "item2"]},
    }
    mock_skyvern = MagicMock()
    mock_skyvern.run_task = AsyncMock(return_value=expected_output)
    mock_skyvern.aclose = AsyncMock()

    schema = {"type": "object", "properties": {"items": {"type": "array"}}}
    with patch("src.core.executor.Skyvern.local", return_value=mock_skyvern) as mock_local:
        result = await run_autonomous_action(
            url="https://example.com",
            prompt="Extract items",
            schema=schema,
        )

    assert result == expected_output
    mock_local.assert_called_once_with(use_in_memory_db=True)
    mock_skyvern.run_task.assert_awaited_once_with(
        url="https://example.com",
        prompt="Extract items",
        data_extraction_schema=schema,
        model="openai/gpt-6.1-terra",
        wait_for_completion=True,
    )
    mock_skyvern.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_skyvern_singleton_reused_across_tasks_and_closed_on_shutdown():
    await close_skyvern()
    mock_skyvern = MagicMock()
    mock_skyvern.run_task = AsyncMock(return_value={"status": "completed"})
    mock_skyvern.aclose = AsyncMock()

    mock_cls = MagicMock()
    mock_cls.local.return_value = mock_skyvern

    init_skyvern(mock_cls)
    try:
        await run_autonomous_action("https://example.com/1", "Task 1")
        await run_autonomous_action("https://example.com/2", "Task 2")
        assert mock_cls.local.call_count == 1
        mock_skyvern.aclose.assert_not_awaited()
    finally:
        await close_skyvern()

    mock_skyvern.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_autonomous_action_forwards_cdp_url_and_preserves_browser():
    await close_skyvern()
    mock_skyvern = MagicMock()
    mock_skyvern.run_task = AsyncMock(return_value={"status": "completed", "output": []})
    mock_skyvern.aclose = AsyncMock()

    with (
        patch("src.core.executor.Skyvern.local", return_value=mock_skyvern),
        patch(
            "src.core.executor.check_cdp_available",
            new=AsyncMock(return_value=(True, {"Browser": "Chrome/131.0"})),
        ),
    ):
        result = await run_autonomous_action(
            url="https://example.com",
            prompt="Extract items",
            cdp_url="http://localhost:9222",
        )

    assert result["status"] == "completed"
    mock_skyvern.run_task.assert_awaited_once_with(
        url="https://example.com",
        prompt="Extract items",
        data_extraction_schema=None,
        model="openai/gpt-6.1-terra",
        wait_for_completion=True,
        browser_address="http://localhost:9222",
    )
    # External browser must NOT be closed when task finishes
    mock_skyvern.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_autonomous_action_forwards_run_with_code():
    await close_skyvern()
    mock_skyvern = MagicMock()
    mock_skyvern.run_task = AsyncMock(return_value={"status": "completed", "output": {}})
    mock_skyvern.aclose = AsyncMock()

    with patch("src.core.executor.Skyvern.local", return_value=mock_skyvern):
        result = await run_autonomous_action(
            url="https://www.royalcaribbean.com/cruises",
            prompt="Locate Adventure of the Seas",
            max_steps=15,
            run_with="code",
        )

    assert result["status"] == "completed"
    mock_skyvern.run_task.assert_awaited_once_with(
        url="https://www.royalcaribbean.com/cruises",
        prompt="Locate Adventure of the Seas",
        data_extraction_schema=None,
        model="openai/gpt-6.1-terra",
        wait_for_completion=True,
        max_steps=15,
        run_with="code",
    )
