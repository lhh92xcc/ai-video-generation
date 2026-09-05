"""Optional face-embedding checks for shot-level identity consistency.

The application deliberately does not import InsightFace into its main virtual
environment.  A local ComfyUI installation already has the heavy computer
vision dependencies, so this module invokes a small helper in that runtime and
keeps the result as ordinary Artifact metadata.  A missing runtime is reported
as ``unavailable`` and is never treated as a successful identity check.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol


class IdentityAuditProvider(Protocol):
    async def audit(
        self,
        reference_bytes: bytes,
        reference_mime_type: str,
        video_bytes: bytes,
        video_mime_type: str,
    ) -> dict[str, Any]:
        """Return a JSON-safe identity audit report."""


class IdentityConsistencyAuditor:
    """Run the InsightFace helper in an explicitly configured Python runtime."""

    def __init__(
        self,
        python_path: str,
        script_path: str = "scripts/face_identity_audit.py",
        model_root: str | None = None,
        threshold: float = 0.4,
        frame_count: int = 8,
        timeout_seconds: int = 120,
    ) -> None:
        self.python_path = python_path.strip()
        self.script_path = Path(script_path)
        self.model_root = model_root.strip() if model_root else ""
        self.threshold = max(-1.0, min(1.0, float(threshold)))
        self.frame_count = max(1, min(64, int(frame_count)))
        self.timeout_seconds = max(1, int(timeout_seconds))

    async def audit(
        self,
        reference_bytes: bytes,
        reference_mime_type: str,
        video_bytes: bytes,
        video_mime_type: str,
    ) -> dict[str, Any]:
        if not self.python_path:
            return self._unavailable("identity_audit_python_path_not_configured")
        if not _executable_exists(self.python_path):
            return self._unavailable("identity_audit_python_not_found")
        if not self.script_path.is_file():
            return self._unavailable("identity_audit_script_not_found")
        if self.model_root and not Path(self.model_root).is_dir():
            return self._unavailable("identity_audit_model_root_not_found")
        if not reference_bytes or not video_bytes:
            return {
                "status": "error",
                "reason": "identity_audit_input_empty",
                "human_review_required": True,
            }

        reference_suffix = ".jpg" if reference_mime_type.lower().split(";", 1)[0] == "image/jpeg" else ".png"
        video_suffix = ".webm" if video_mime_type.lower().split(";", 1)[0] == "video/webm" else ".mp4"
        with TemporaryDirectory(prefix="ai-video-identity-audit-") as directory:
            root = Path(directory)
            reference_path = root / f"reference{reference_suffix}"
            video_path = root / f"video{video_suffix}"
            await asyncio.gather(
                asyncio.to_thread(reference_path.write_bytes, reference_bytes),
                asyncio.to_thread(video_path.write_bytes, video_bytes),
            )
            command = [
                self.python_path,
                str(self.script_path),
                "--reference",
                str(reference_path),
                "--video",
                str(video_path),
                "--threshold",
                str(self.threshold),
                "--max-frames",
                str(self.frame_count),
            ]
            if self.model_root:
                command.extend(["--model-root", self.model_root])
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                return self._unavailable("identity_audit_python_not_found")
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                process.kill()
                await process.communicate()
                return {
                    "status": "error",
                    "reason": "identity_audit_timeout",
                    "human_review_required": True,
                }

        payload = _parse_json_object(stdout)
        if payload is None:
            detail = stderr.decode("utf-8", errors="replace").strip()
            return {
                "status": "error",
                "reason": "identity_audit_invalid_helper_output",
                "detail": detail[-500:] if detail else "",
                "return_code": process.returncode,
                "human_review_required": True,
            }
        if process.returncode != 0 and payload.get("status") not in {
            "unavailable",
            "reference_no_face",
            "no_face",
        }:
            payload.setdefault("status", "error")
            payload.setdefault("human_review_required", True)
        return payload

    @staticmethod
    def _unavailable(reason: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "reason": reason,
            "human_review_required": True,
        }


def _executable_exists(path: str) -> bool:
    candidate = Path(path)
    return candidate.is_file() if candidate.is_absolute() else shutil.which(path) is not None


def _parse_json_object(raw: bytes) -> dict[str, Any] | None:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Keep the helper free to emit a diagnostic line before its final JSON
        # object. Parse the last object-shaped line without trusting arbitrary
        # output as application instructions.
        for line in reversed(text.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None
    return payload if isinstance(payload, dict) else None
