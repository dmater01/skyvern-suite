import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.introspect import introspect_webpage, recover_page_state, IntrospectionError


@pytest.fixture
def valid_spec_payload():
    return {
        "app_title": "Job Search Portal",
        "description": "Search and filter open engineering positions",
        "required_inputs": [
            {
                "field_name": "role",
                "label": "Role Title",
                "input_type": "text",
                "options": None,
                "placeholder": "e.g. Backend Engineer",
                "is_required": True,
            }
        ],
        "prompt_template": "Search for {role} positions and list results",
    }


@pytest.mark.asyncio
async def test_introspect_webpage_success(valid_spec_payload):
    mock_page = AsyncMock()
    mock_page.extract.return_value = valid_spec_payload

    mock_browser = AsyncMock()
    mock_browser.get_working_page.return_value = mock_page

    mock_skyvern = MagicMock()
    mock_skyvern.launch_cloud_browser = AsyncMock(return_value=mock_browser)

    with patch("src.core.introspect.Skyvern.local", return_value=mock_skyvern):
        result = await introspect_webpage(
            "https://example.com/jobs", "Find engineering jobs", base_delay=0.01
        )

    assert result["app_title"] == "Job Search Portal"
    assert result["required_inputs"][0]["field_name"] == "role"
    mock_page.goto.assert_awaited_once_with("https://example.com/jobs")
    mock_page.validate.assert_awaited_once()
    mock_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_introspect_webpage_retries_and_recovers(valid_spec_payload):
    mock_page = AsyncMock()
    mock_page.extract.side_effect = [
        RuntimeError("Overlay blocked extraction"),
        valid_spec_payload,
    ]

    mock_browser = AsyncMock()
    mock_browser.get_working_page.return_value = mock_page

    mock_skyvern = MagicMock()
    mock_skyvern.launch_cloud_browser = AsyncMock(return_value=mock_browser)

    with patch("src.core.introspect.Skyvern.local", return_value=mock_skyvern):
        result = await introspect_webpage(
            "https://example.com/jobs",
            "Find engineering jobs",
            max_retries=3,
            base_delay=0.01,
        )

    assert result["app_title"] == "Job Search Portal"
    assert mock_page.extract.await_count == 2
    mock_page.act.assert_awaited_once()
    mock_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_introspect_webpage_exhausts_retries_and_closes_browser():
    mock_page = AsyncMock()
    mock_page.goto.side_effect = TimeoutError("Navigation timed out")

    mock_browser = AsyncMock()
    mock_browser.get_working_page.return_value = mock_page

    mock_skyvern = MagicMock()
    mock_skyvern.launch_cloud_browser = AsyncMock(return_value=mock_browser)

    with patch("src.core.introspect.Skyvern.local", return_value=mock_skyvern):
        with pytest.raises(IntrospectionError, match="Failed to introspect"):
            await introspect_webpage(
                "https://example.com/jobs",
                "Find engineering jobs",
                max_retries=2,
                base_delay=0.01,
            )

    assert mock_page.goto.await_count == 2
    mock_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_introspect_webpage_closes_browser_if_get_working_page_fails():
    mock_browser = AsyncMock()
    mock_browser.get_working_page.side_effect = RuntimeError("Failed to attach page")

    mock_skyvern = MagicMock()
    mock_skyvern.launch_cloud_browser = AsyncMock(return_value=mock_browser)

    with patch("src.core.introspect.Skyvern.local", return_value=mock_skyvern):
        with pytest.raises(RuntimeError, match="Failed to attach page"):
            await introspect_webpage("https://example.com", "Test objective")

    mock_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_introspect_webpage_validates_inputs():
    with pytest.raises(ValueError, match="Target URL must not be empty"):
        await introspect_webpage("   ", "Objective")

    with pytest.raises(ValueError, match="Objective must not be empty"):
        await introspect_webpage("https://example.com", "")


@pytest.mark.asyncio
async def test_recover_page_state_handles_exceptions_gracefully():
    mock_page = AsyncMock()
    mock_page.act.side_effect = RuntimeError("No modal found")
    await recover_page_state(mock_page)
    mock_page.act.assert_awaited_once()
