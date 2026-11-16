from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from src.config import settings

logger = logging.getLogger(__name__)

current_task_id_var: ContextVar[Optional[str]] = ContextVar("current_task_id", default=None)


def _resolve_local_file_path(uri_or_path: Optional[str]) -> Optional[Path]:
    """Resolves a file:// URI or local filesystem path to a Path object if it exists."""
    if not uri_or_path or not isinstance(uri_or_path, str):
        return None
    if uri_or_path.startswith("file://"):
        parsed = urlparse(uri_or_path)
        local_path = Path(unquote(parsed.path))
    elif uri_or_path.startswith(("http://", "https://", "/api/artifacts/")):
        return None
    else:
        local_path = Path(uri_or_path)

    if local_path.exists() and local_path.is_file():
        return local_path
    return None


class ArtifactManager:
    """Manages structured persistence, URL rewriting, and retention pruning of Skyvern run artifacts."""

    def __init__(self, artifacts_dir: Optional[str] = None) -> None:
        self.artifacts_dir = Path(
            artifacts_dir or os.getenv("ARTIFACTS_DIR", settings.artifacts_dir)
        )
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_base_dir(cls, artifacts_dir: Optional[str] = None) -> Path:
        base = Path(artifacts_dir or os.getenv("ARTIFACTS_DIR", settings.artifacts_dir))
        base.mkdir(parents=True, exist_ok=True)
        return base

    @classmethod
    def persist_task_artifacts(
        cls,
        task_id: str,
        result: Optional[Dict[str, Any]],
        artifacts_dir: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Relocates temporary Skyvern recordings/screenshots into artifacts/{task_id}/ and updates URLs."""
        if not task_id or not isinstance(result, dict):
            return result

        base_dir = cls.get_base_dir(artifacts_dir)
        task_dir = base_dir / task_id
        screenshots_dir = task_dir / "screenshots"
        task_dir.mkdir(parents=True, exist_ok=True)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # 1. Persist extraction.json
        extraction_payload = (
            result.get("output")
            if "output" in result
            else result.get("extracted_information")
        )
        extraction_file = task_dir / "extraction.json"
        extraction_file.write_text(
            json.dumps(extraction_payload, indent=2, default=str),
            encoding="utf-8",
        )

        # 2. Relocate recording.mp4
        raw_recording_url = result.get("recording_url")
        source_recording = _resolve_local_file_path(raw_recording_url)
        source_step_dir: Optional[Path] = None
        persisted_recording_url: Optional[str] = None

        if source_recording is not None:
            source_step_dir = source_recording.parent
            target_recording = task_dir / "recording.mp4"
            if source_recording.resolve() != target_recording.resolve():
                shutil.copy2(source_recording, target_recording)
            persisted_recording_url = f"/api/artifacts/{task_id}/recording.mp4"
            result["recording_url"] = persisted_recording_url

        # 3. Relocate screenshots into artifacts/{task_id}/screenshots/action_{i}.png
        raw_screenshots = result.get("screenshot_urls") or []
        source_screenshots: List[Path] = []
        if isinstance(raw_screenshots, list):
            for uri in raw_screenshots:
                resolved = _resolve_local_file_path(uri)
                if resolved is not None:
                    source_screenshots.append(resolved)
                    if source_step_dir is None:
                        source_step_dir = resolved.parent

        # Fallback or multi-step expansion: scan task/step directories for action screenshots
        if source_step_dir is not None and source_step_dir.exists():
            task_temp_root = source_step_dir.parent
            if task_temp_root.name.startswith("tsk_"):
                step_action_pngs = sorted(task_temp_root.glob("*_stp_*/*screenshot_action.png"))
                if not step_action_pngs:
                    step_action_pngs = sorted(task_temp_root.glob("*_stp_*/*.png"))
                seen_resolved = {p.resolve() for p in source_screenshots}
                for png_path in step_action_pngs:
                    if png_path.resolve() not in seen_resolved:
                        source_screenshots.append(png_path)
                        seen_resolved.add(png_path.resolve())
            elif not source_screenshots:
                source_screenshots = sorted(source_step_dir.glob("*.png"))

        persisted_screenshot_urls: List[str] = []
        for idx, src_png in enumerate(source_screenshots):
            target_name = f"action_{idx}.png"
            target_png = screenshots_dir / target_name
            if src_png.resolve() != target_png.resolve():
                shutil.copy2(src_png, target_png)
            persisted_screenshot_urls.append(
                f"/api/artifacts/{task_id}/screenshots/{target_name}"
            )

        if persisted_screenshot_urls or "screenshot_urls" in result:
            result["screenshot_urls"] = persisted_screenshot_urls

        # 4. Write task_metadata.json
        metadata = {
            "task_id": task_id,
            "run_id": result.get("run_id"),
            "status": result.get("status", "completed"),
            "step_count": result.get("step_count"),
            "created_at": result.get("created_at"),
            "finished_at": result.get("finished_at"),
            "failure_reason": result.get("failure_reason"),
            "recording_url": result.get("recording_url"),
            "screenshot_urls": result.get("screenshot_urls"),
            "persisted_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata_file = task_dir / "task_metadata.json"
        metadata_file.write_text(
            json.dumps(metadata, indent=2, default=str),
            encoding="utf-8",
        )

        return result

    @classmethod
    def prune_stale_artifacts(
        cls,
        retention_days: Optional[int] = None,
        artifacts_dir: Optional[str] = None,
    ) -> List[str]:
        """Scans ARTIFACTS_DIR and deletes task artifact bundles older than retention_days."""
        days = (
            retention_days
            if retention_days is not None
            else int(os.getenv("ARTIFACT_RETENTION_DAYS", settings.artifact_retention_days))
        )
        base_dir = cls.get_base_dir(artifacts_dir)
        cutoff_epoch = time.time() - (days * 86400)
        pruned_tasks: List[str] = []

        for entry in base_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime < cutoff_epoch:
                    shutil.rmtree(entry)
                    pruned_tasks.append(entry.name)
                    logger.info("Pruned stale artifact directory: %s", entry)
            except Exception as exc:
                logger.warning("Failed to prune artifact directory %s: %s", entry, exc)

        return pruned_tasks
