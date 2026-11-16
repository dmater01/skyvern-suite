import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)


def resolve_effective_cdp_url(requested_cdp_url: Optional[str] = None) -> Optional[str]:
    """Resolves the target CDP endpoint from explicit request argument or ENABLE_CDP environment setting."""
    if requested_cdp_url:
        return requested_cdp_url.strip()
    enable_cdp = os.getenv("ENABLE_CDP", str(settings.enable_cdp)).lower() in ("true", "1", "yes")
    if enable_cdp:
        default_url = os.getenv("DEFAULT_CDP_URL", settings.default_cdp_url).strip()
        if default_url:
            return default_url
    return None


async def check_cdp_available(
    cdp_url: str = "http://localhost:9222",
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Queries {cdp_url}/json/version with a 2.0s timeout to verify Chrome DevTools Protocol reachability."""
    version_url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(version_url)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    return True, data
                return True, {"raw": data}
    except Exception as exc:
        logger.debug("CDP probe to %s failed: %s", version_url, exc)
    return False, None
