"""Local whisper.cpp ASR adapter for macOS host execution."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from app.domain.models import (
    SubtitleASRGenerationRequest,
    SubtitleASRGenerationResult,
    SubtitleCueRequest,
)
from app.providers.errors_asr import SubtitleASRProviderError


class WhisperCppASRProvider:
    def __init__(
        self,
        binary: str = "whisper-cli",
        model_path: str = "",
        timeout_seconds: int = 180,
        threads: int = 4,
    ) -> None:
        self._binary = binary
        self._model_path = model_path
        self._timeout_seconds = max(1, timeout_seconds)
        self._threads = max(1, threads)

    async def transcribe(
        self,
        request: SubtitleASRGenerationRequest,
    ) -> SubtitleASRGenerationResult:
        if shutil.which(self._binary) is None:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_UNAVAILABLE",
                "whisper.cpp is unavailable; install whisper-cpp on the Mac host",
            )
        if not self._model_path:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_NOT_CONFIGURED",
                "AI_VIDEO_ASR_MODEL_PATH must point to a whisper.cpp GGML model",
            )
        model_path = Path(self._model_path).expanduser()
        if not model_path.is_file():
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_NOT_CONFIGURED",
                f"whisper.cpp model was not found: {model_path}",
            )

        started = perf_counter()
        with TemporaryDirectory(prefix="ai-video-whisper-") as directory:
            root = Path(directory)
            audio_path = root / self._audio_suffix(request.mime_type)
            output_prefix = root / "transcription"
            await asyncio.to_thread(audio_path.write_bytes, request.audio_bytes)
            command = [
                self._binary,
                "-m",
                str(model_path),
                "-f",
                str(audio_path),
                "-l",
                self._language_code(request.language),
                "-t",
                str(self._threads),
                "-oj",
                "-of",
                str(output_prefix),
                "-np",
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_UNAVAILABLE",
                    "whisper.cpp executable could not be started",
                ) from exc
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self._timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_TIMEOUT",
                    "whisper.cpp timed out while transcribing audio",
                ) from exc
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "whisper.cpp failed while transcribing audio"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise SubtitleASRProviderError("ASR_PROVIDER_FAILED", message)

            output_path = output_prefix.with_suffix(".json")
            try:
                payload = json.loads(await asyncio.to_thread(output_path.read_text, encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_INVALID_RESPONSE",
                    "whisper.cpp did not produce a readable JSON transcription",
                ) from exc

        cues = self._parse_payload(payload)
        return SubtitleASRGenerationResult(
            cues=cues,
            provider="whisper_cpp",
            model=model_path.name,
            precision="segment_asr",
            duration_ms=max(1, round((perf_counter() - started) * 1000)),
            metadata={
                "offline": True,
                "audio_inspected": True,
                "word_boundary": False,
                "binary": self._binary,
                "language": request.language,
            },
        )

    @classmethod
    def _parse_payload(cls, payload: Any) -> list[SubtitleCueRequest]:
        if not isinstance(payload, dict):
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "whisper.cpp JSON output must be an object",
            )
        raw_segments = payload.get("transcription") or payload.get("segments")
        if not isinstance(raw_segments, list):
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "whisper.cpp JSON output has no transcription segments",
            )
        cues: list[SubtitleCueRequest] = []
        for segment in raw_segments:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            timestamps = segment.get("timestamps")
            if isinstance(timestamps, dict):
                start = cls._timestamp_seconds(timestamps.get("from"))
                end = cls._timestamp_seconds(timestamps.get("to"))
            else:
                start = cls._number(segment.get("start"))
                end = cls._number(segment.get("end"))
            if end <= start:
                continue
            cues.append(
                SubtitleCueRequest(start_seconds=start, end_seconds=end, text=text)
            )
        if not cues:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "whisper.cpp returned no valid timed subtitle segments",
            )
        return cues

    @staticmethod
    def _timestamp_seconds(value: Any) -> float:
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        if not isinstance(value, str):
            return 0.0
        normalized = value.strip().replace(",", ".")
        parts = normalized.split(":")
        try:
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return max(0.0, int(hours) * 3600 + int(minutes) * 60 + float(seconds))
            return max(0.0, float(normalized))
        except ValueError:
            return 0.0

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _language_code(language: str) -> str:
        return language.split("-", 1)[0].lower()

    @staticmethod
    def _audio_suffix(mime_type: str) -> str:
        return {
            "audio/wav": "audio.wav",
            "audio/x-wav": "audio.wav",
            "audio/mpeg": "audio.mp3",
            "audio/mp3": "audio.mp3",
            "audio/mp4": "audio.m4a",
            "audio/webm": "audio.webm",
        }.get(mime_type.split(";", 1)[0].lower(), "audio.bin")
