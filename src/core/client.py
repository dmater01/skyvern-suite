import inspect
import logging
from typing import Any, Optional

try:
    from skyvern import Skyvern
except ImportError:  # pragma: no cover
    class Skyvern:  # type: ignore[no-redef]
        @classmethod
        def local(cls, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("skyvern package is not installed in the current environment.")

logger = logging.getLogger(__name__)

_shared_skyvern: Optional[Any] = None


async def safe_aclose(client: Any, *, preserve_external_browser: bool = False) -> None:
    """Safely invokes aclose() on a scoped Skyvern client instance if not shared or attached to external CDP."""
    if client is None or client is _shared_skyvern or preserve_external_browser:
        return
    aclose_fn = getattr(client, "aclose", None)
    if callable(aclose_fn):
        try:
            res = aclose_fn()
            if inspect.isawaitable(res):
                await res
        except Exception as exc:
            logger.error("Failed to close Skyvern client cleanly: %s", exc)


async def release_cdp_session_handles(
    run_id: Optional[str] = None,
    cdp_url: Optional[str] = None,
) -> None:
    """Explicitly releases Skyvern browser state handles and stale CDP blank targets without closing host Chrome."""
    try:
        from skyvern.forge import app as skyvern_app

        browser_manager = getattr(skyvern_app, "BROWSER_MANAGER", None)
        if browser_manager is not None and hasattr(browser_manager, "pages"):
            target_ids = [run_id] if run_id and run_id in browser_manager.pages else list(browser_manager.pages.keys())
            for tid in target_ids:
                try:
                    await browser_manager.cleanup_for_task(
                        tid,
                        close_browser_on_completion=False,
                    )
                except Exception as cleanup_exc:
                    logger.debug("Non-fatal cleanup_for_task on %s: %s", tid, cleanup_exc)
    except Exception as exc:
        logger.debug("Optional Skyvern BROWSER_MANAGER cleanup skipped: %s", exc)

    if cdp_url:
        try:
            import httpx

            base = cdp_url.rstrip("/")
            async with httpx.AsyncClient(timeout=2.0) as http_client:
                resp = await http_client.get(f"{base}/json/list")
                if resp.status_code == 200:
                    targets = resp.json()
                    page_targets = [
                        t for t in targets if isinstance(t, dict) and t.get("type") == "page"
                    ]
                    # Keep at least one active page tab so Chrome stays open and ready for immediate reuse;
                    # close surplus about:blank tabs accumulated across runs.
                    if len(page_targets) > 1:
                        blank_pages = [
                            t for t in page_targets if t.get("url") in ("about:blank", "chrome://newtab/")
                        ]
                        for extra in blank_pages[:-1] if len(blank_pages) == len(page_targets) else blank_pages:
                            tid = extra.get("id")
                            if tid:
                                await http_client.get(f"{base}/json/close/{tid}")
        except Exception as exc:
            logger.debug("Optional CDP tab cleanup skipped: %s", exc)


def _register_dynamic_openai_model(model_str: str) -> str:
    """Registers an OpenAI model in Skyvern's LLMConfigRegistry and returns its llm_key."""
    from skyvern.config import settings as skyvern_settings
    from skyvern.forge.sdk.api.llm.config_registry import LLMConfigRegistry
    from skyvern.forge.sdk.settings_manager import SettingsManager
    from skyvern.schemas.llm import LLMConfig

    raw_name = model_str.strip()
    if raw_name in ("gpt-6.1-terra", "openai/gpt-6.1-terra", "OPENAI_GPT6_1_TERRA"):
        llm_key = "OPENAI_GPT6_1_TERRA"
        if LLMConfigRegistry.is_registered(llm_key):
            for alias in ("gpt-6.1-terra", "openai/gpt-6.1-terra"):
                if not LLMConfigRegistry.is_registered(alias):
                    LLMConfigRegistry.register_config_alias(alias, llm_key)
    elif raw_name == "OLLAMA" or raw_name.startswith("ollama/"):
        import os
        from skyvern.schemas.llm import LiteLLMParams

        ollama_model = raw_name if raw_name.startswith("ollama/") else f"ollama/{os.getenv('OLLAMA_MODEL', 'gemma4:latest')}"
        ollama_url = os.getenv("OLLAMA_SERVER_URL", "http://localhost:11434")
        llm_key = raw_name
        if not LLMConfigRegistry.is_registered(llm_key):
            LLMConfigRegistry.register_config(
                llm_key,
                LLMConfig(
                    model_name=ollama_model,
                    required_env_vars=[],
                    supports_vision=True,
                    add_assistant_prefix=False,
                    litellm_params=LiteLLMParams(api_base=ollama_url),
                ),
            )
    else:
        litellm_model = raw_name if "/" in raw_name else f"openai/{raw_name}"
        llm_key = raw_name
        if not LLMConfigRegistry.is_registered(llm_key):
            LLMConfigRegistry.register_config(
                llm_key,
                LLMConfig(
                    model_name=litellm_model,
                    required_env_vars=["OPENAI_API_KEY"],
                    supports_vision=True,
                    add_assistant_prefix=False,
                ),
            )

    for target in (skyvern_settings, SettingsManager.get_settings()):
        target.LLM_KEY = llm_key
    return llm_key


def _configure_real_skyvern_client(client: Any) -> Any:
    """Ensures real Skyvern embedded instances resolve OpenAI models, extraction goals, and CDP flags cleanly."""
    if not isinstance(client, Skyvern):
        return client

    try:
        import os
        from skyvern.library import skyvern as skyvern_lib_mod
        from skyvern.services import task_v1_service

        # Reduce internal completion polling interval from 10s to 1s so completed tasks unblock the queue immediately
        skyvern_lib_mod.DEFAULT_AGENT_HEARTBEAT_INTERVAL = 1.0

        default_model = os.getenv("LLM_KEY", "gpt-6.1-terra")
        _register_dynamic_openai_model(default_model)

        orig_service_run_task = task_v1_service.run_task
        if not getattr(orig_service_run_task, "_extraction_goal_patched", False):
            async def _patched_service_run_task(task: Any, *args: Any, **kwargs: Any) -> Any:
                if getattr(task, "extracted_information_schema", None) is not None:
                    nav_goal = getattr(task, "navigation_goal", None) or "Extract the requested information according to the schema."
                    if not getattr(task, "data_extraction_goal", None):
                        task.data_extraction_goal = nav_goal
                    clarification = (
                        " Complete navigation once the requested navigation steps and target page state are reached; "
                        "structured JSON schema formatting happens automatically during the post-completion extraction phase."
                    )
                    if getattr(task, "navigation_goal", None) and clarification not in task.navigation_goal:
                        task.navigation_goal = f"{task.navigation_goal.rstrip()}{clarification}"
                if getattr(task, "browser_address", None) and hasattr(task, "start_fresh_browser"):
                    task.start_fresh_browser = False
                return await orig_service_run_task(task, *args, **kwargs)

            _patched_service_run_task._extraction_goal_patched = True  # type: ignore[attr-defined]
            task_v1_service.run_task = _patched_service_run_task
    except Exception as exc:
        logger.debug("Optional Skyvern LLM alias setup skipped: %s", exc)

    orig_run_task = client.run_task

    async def _wrapped_run_task(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("start_fresh_browser", None)
        run_with_val = kwargs.pop("run_with", None)
        if run_with_val is not None:
            req_opts = dict(kwargs.get("request_options") or {})
            extra_body = dict(req_opts.get("additional_body_parameters") or {})
            extra_body["run_with"] = run_with_val
            req_opts["additional_body_parameters"] = extra_body
            kwargs["request_options"] = req_opts
        model_arg = kwargs.get("model")
        if isinstance(model_arg, str):
            try:
                _register_dynamic_openai_model(model_arg)
            except Exception as exc:
                logger.debug("Dynamic model registration skipped for %s: %s", model_arg, exc)
            kwargs["model"] = {"model_name": model_arg}
        return await orig_run_task(*args, **kwargs)

    client.run_task = _wrapped_run_task
    return client


def init_skyvern(skyvern_cls: Any = Skyvern) -> Any:
    """Initializes and returns the process-wide shared Skyvern local instance."""
    global _shared_skyvern
    if _shared_skyvern is None:
        _shared_skyvern = _configure_real_skyvern_client(
            skyvern_cls.local(use_in_memory_db=True)
        )
    return _shared_skyvern


def get_skyvern(skyvern_cls: Any = Skyvern) -> Any:
    """Returns the shared Skyvern instance if initialized, or creates a new scoped instance."""
    if _shared_skyvern is not None:
        return _shared_skyvern
    return _configure_real_skyvern_client(skyvern_cls.local(use_in_memory_db=True))


async def close_skyvern() -> None:
    """Closes and clears the shared Skyvern instance on application shutdown."""
    global _shared_skyvern
    if _shared_skyvern is not None:
        client = _shared_skyvern
        _shared_skyvern = None
        await safe_aclose(client)
