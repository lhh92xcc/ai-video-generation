"""Provider for already-licensed local BGM files.

The provider only reads files below the configured source root. It does not
download music, scrape catalogs, or infer copyright ownership.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from time import perf_counter

from app.domain.models import BGMGenerationRequest, BGMGenerationResult
from app.providers.errors_audio import BGMProviderError


class LocalFileBGMProvider:
    def __init__(self, source_root: str | Path, default_source_path: str | None = None) -> None:
        self.source_root = Path(source_root).expanduser().resolve()
        self.default_source_path = default_source_path or None

    async def generate_bgm(self, request: BGMGenerationRequest) -> BGMGenerationResult:
        source_path = self._resolve_source_path(request.source_path)
        started = perf_counter()
        try:
            content = await _read_bytes(source_path)
        except FileNotFoundError as exc:
            raise BGMProviderError(
                "BGM_PROVIDER_SOURCE_NOT_FOUND",
                f"BGM source file was not found: {source_path.name}",
            ) from exc
        except OSError as exc:
            raise BGMProviderError(
                "BGM_PROVIDER_READ_FAILED",
                f"BGM source file could not be read: {source_path.name}",
            ) from exc
        if not content:
            raise BGMProviderError(
                "BGM_PROVIDER_INVALID_SOURCE",
                "BGM source file is empty",
            )

        mime_type = _mime_type_for_path(source_path)
        return BGMGenerationResult(
            audio_base64=base64.b64encode(content).decode("ascii"),
            mime_type=mime_type,
            provider="local_file",
            model="licensed-local-file-v1",
            duration_seconds=0,
            duration_ms=round((perf_counter() - started) * 1000),
            metadata={
                "label": request.label,
                "source_type": "licensed_local_file",
                "source_name": source_path.name,
                "source_root": str(self.source_root),
                "source_size_bytes": len(content),
                "source_sha256": hashlib.sha256(content).hexdigest(),
                "rights_status": request.rights_status.value,
                "rights_holder": request.rights_holder,
                "rights_reference": request.rights_reference,
                "rights_review_required": request.rights_status.value != "confirmed",
                "loop_recommended": True,
            },
        )

    def _resolve_source_path(self, requested_path: str | None) -> Path:
        relative_path = requested_path or self.default_source_path
        if not relative_path:
            raise BGMProviderError(
                "BGM_PROVIDER_SOURCE_REQUIRED",
                "Configure a BGM source_path or AI_VIDEO_BGM_SOURCE_PATH",
            )
        candidate = Path(relative_path).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.source_root / candidate).resolve()
        try:
            resolved.relative_to(self.source_root)
        except ValueError as exc:
            raise BGMProviderError(
                "BGM_PROVIDER_SOURCE_FORBIDDEN",
                "BGM source_path must stay inside AI_VIDEO_BGM_SOURCE_ROOT",
            ) from exc
        if resolved.suffix.lower() not in _MIME_TYPES:
            raise BGMProviderError(
                "BGM_PROVIDER_UNSUPPORTED_FORMAT",
                "BGM source must use wav, mp3, ogg, m4a, aac or webm format",
            )
        return resolved


async def _read_bytes(path: Path) -> bytes:
    import asyncio

    return await asyncio.to_thread(path.read_bytes)


_MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}


def _mime_type_for_path(path: Path) -> str:
    return _MIME_TYPES[path.suffix.lower()]
