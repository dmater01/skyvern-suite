import asyncio
import logging
from typing import Any, Dict
from pydantic import ValidationError
from src.core.client import Skyvern, get_skyvern, safe_aclose
from src.schemas import DynamicAppSpec

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0


class IntrospectionError(RuntimeError):
    """Raised when webpage introspection fails after retries or returns invalid data."""


async def recover_page_state(page: Any) -> None:
    """Attempts visual state recovery by dismissing overlays and validating page readiness."""
    try:
        await page.act(
            "Close or dismiss any active modal, popup, cookie banner, or alert overlay."
        )
        await page.validate("Verify the main page content is visible and interactive")
    except Exception as exc:
        logger.warning("Visual state recovery failed: %s", exc)


async def introspect_webpage(
    url: str,
    objective: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> Dict[str, Any]:
    """Navigates to the target URL and uses visual perception to generate form specifications."""
    if not url or not url.strip():
        raise ValueError("Target URL must not be empty.")
    if not objective or not objective.strip():
        raise ValueError("Objective must not be empty.")

    meta_schema = {
        "type": "object",
        "properties": {
            "app_title": {"type": "string"},
            "description": {"type": "string"},
            "required_inputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string"},
                        "label": {"type": "string"},
                        "input_type": {
                            "type": "string",
                            "enum": ["text", "number", "email", "dropdown"],
                        },
                        "options": {"type": "array", "items": {"type": "string"}},
                        "placeholder": {"type": "string"},
                        "is_required": {"type": "boolean"},
                    },
                    "required": ["field_name", "label", "input_type", "is_required"],
                },
            },
            "prompt_template": {
                "type": "string",
                "description": "Template using {field_name} tokens instructing Skyvern how to fulfill the task",
            },
        },
        "required": ["app_title", "description", "required_inputs", "prompt_template"],
    }

    skyvern = get_skyvern(Skyvern)
    browser = None

    try:
        browser = await skyvern.launch_cloud_browser()
        page = await browser.get_working_page()

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                await page.goto(url)
                await page.validate("Verify the target page has loaded and is ready for inspection")

                raw_spec = await page.extract(
                    prompt=(
                        f"Analyze this page for a user whose goal is: '{objective}'. "
                        "Inspect all visible forms, buttons, inputs, and interactive components. "
                        "Generate a structured specification representing what inputs a user needs to supply, "
                        "and build an execution prompt template formatted with {field_name} placeholders."
                    ),
                    schema=meta_schema,
                )

                validated_spec = DynamicAppSpec.model_validate(raw_spec)
                return validated_spec.model_dump()

            except (ValidationError, Exception) as exc:
                last_error = exc
                logger.warning(
                    "Introspection attempt %d/%d failed for %s: %s",
                    attempt,
                    max_retries,
                    url,
                    exc,
                )
                if attempt < max_retries:
                    await recover_page_state(page)
                    backoff = base_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(backoff)

        raise IntrospectionError(
            f"Failed to introspect '{url}' after {max_retries} attempts: {last_error}"
        ) from last_error

    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception as close_exc:
                logger.error("Failed to close browser cleanly: %s", close_exc)
        await safe_aclose(skyvern)
