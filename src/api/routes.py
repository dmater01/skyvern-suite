import mimetypes
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse

from src.core.artifacts import ArtifactManager
from src.core.cdp import check_cdp_available
from src.core.config import settings

router = APIRouter()

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_json_strings(obj: Any) -> Any:
    """Recursively normalizes control characters (newlines/tabs) in nested strings for CLI/jq safety."""
    if isinstance(obj, str):
        return _CONTROL_CHAR_RE.sub(" ", obj).strip()
    if isinstance(obj, dict):
        return {k: sanitize_json_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json_strings(item) for item in obj]
    return obj


def encode_safe_json_response(
    payload: Any,
    status_code: int = status.HTTP_200_OK,
) -> JSONResponse:
    """Encodes a response model or dict via jsonable_encoder and strips raw control characters."""
    encoded = jsonable_encoder(payload)
    sanitized = sanitize_json_strings(encoded)
    return JSONResponse(content=sanitized, status_code=status_code)


@router.get("/api/cdp/status")
async def get_cdp_status(
    cdp_url: Optional[str] = Query(default=None, description="CDP endpoint URL to probe"),
) -> JSONResponse:
    """Probes Chrome DevTools Protocol reachability and returns browser version metadata."""
    target_url = (
        cdp_url
        or os.getenv("DEFAULT_CDP_URL", settings.default_cdp_url)
        or "http://localhost:9222"
    ).strip()
    available, version_data = await check_cdp_available(target_url)
    browser = version_data.get("Browser") if isinstance(version_data, dict) else None
    protocol_version = (
        version_data.get("Protocol-Version") if isinstance(version_data, dict) else None
    )
    return encode_safe_json_response(
        {
            "available": available,
            "endpoint": target_url,
            "browser": browser,
            "protocol_version": protocol_version,
        }
    )


@router.get("/api/artifacts/{task_id}/{file_name:path}")
async def get_task_artifact(task_id: str, file_name: str) -> FileResponse:
    """Serves persisted task artifacts (.mp4 recordings, .png screenshots, and JSON metadata)."""
    base_dir = ArtifactManager.get_base_dir().resolve()
    task_dir = (base_dir / task_id).resolve()

    if not task_dir.is_relative_to(base_dir) or not task_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact bundle for task '{task_id}' not found",
        )

    candidate = (task_dir / file_name).resolve()
    if not candidate.is_relative_to(task_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid artifact file path",
        )

    if not candidate.exists() or not candidate.is_file():
        # Allow shorthand lookup inside screenshots/ subdirectory
        screenshot_candidate = (task_dir / "screenshots" / Path(file_name).name).resolve()
        if screenshot_candidate.is_relative_to(task_dir) and screenshot_candidate.is_file():
            candidate = screenshot_candidate
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact '{file_name}' not found for task '{task_id}'",
            )

    suffix = candidate.suffix.lower()
    if suffix == ".mp4":
        media_type = "video/mp4"
    elif suffix == ".png":
        media_type = "image/png"
    elif suffix == ".json":
        media_type = "application/json"
    else:
        media_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"

    return FileResponse(path=candidate, media_type=media_type, filename=candidate.name)
