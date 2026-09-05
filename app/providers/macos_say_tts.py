"""Offline macOS ``say`` TTS Provider.

The adapter intentionally produces a normal WAV Artifact through FFmpeg so the
rest of the pipeline does not need to know about AIFF or macOS process details.
"""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from app.domain.models import TTSGenerationRequest, TTSGenerationResult
from app.providers.errors_audio import TTSProviderError


class MacOSSayTTSProvider:
    def __init__(
        self,
        timeout_seconds: int = 90,
        say_binary: str = "say",
        ffmpeg_binary: str = "ffmpeg",
    ) -> None:
        self._timeout_seconds = max(1, timeout_seconds)
        self._say_binary = say_binary
        self._ffmpeg_binary = ffmpeg_binary

    async def generate_speech(self, request: TTSGenerationRequest) -> TTSGenerationResult:
        if shutil.which(self._say_binary) is None:
            raise TTSProviderError(
                "TTS_PROVIDER_UNAVAILABLE",
                "macOS say is unavailable; use this Provider on the Mac host",
            )
        if shutil.which(self._ffmpeg_binary) is None:
            raise TTSProviderError(
                "TTS_PROVIDER_UNAVAILABLE",
                f"{self._ffmpeg_binary} is required to normalize macOS say output",
            )

        started = perf_counter()
        with TemporaryDirectory(prefix="ai-video-say-") as directory:
            root = Path(directory)
            aiff_path = root / "speech.aiff"
            wav_path = root / "speech.wav"
            say_command = [
                self._say_binary,
                "-v",
                request.voice,
                "-r",
                str(self._rate_to_words_per_minute(request.rate)),
                "-o",
                str(aiff_path),
                request.text,
            ]
            await self._run(say_command, "macOS say")
            await self._run(
                [
                    self._ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(aiff_path),
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                "FFmpeg audio normalization",
            )
            if not wav_path.exists() or wav_path.stat().st_size == 0:
                raise TTSProviderError(
                    "TTS_PROVIDER_INVALID_RESPONSE",
                    "macOS say produced an empty audio file",
                )
            content = await asyncio.to_thread(wav_path.read_bytes)

        return TTSGenerationResult(
            audio_base64=base64.b64encode(content).decode("ascii"),
            mime_type="audio/wav",
            provider="macos_say",
            model=request.voice,
            duration_seconds=0,
            duration_ms=max(1, round((perf_counter() - started) * 1000)),
            metadata={
                "voice": request.voice,
                "rate": request.rate,
                "volume": request.volume,
                "offline": True,
                "transport": "macos-say",
            },
        )

    async def _run(self, command: list[str], operation: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise TTSProviderError(
                "TTS_PROVIDER_UNAVAILABLE",
                f"{operation} binary is unavailable",
            ) from exc
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise TTSProviderError(
                "TTS_PROVIDER_TIMEOUT",
                f"{operation} timed out",
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = f"{operation} failed"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise TTSProviderError("TTS_PROVIDER_FAILED", message)

    @staticmethod
    def _rate_to_words_per_minute(rate: str) -> int:
        match = re.fullmatch(r"([+-]?\d+)%", rate.strip())
        if match is None:
            return 180
        percent = int(match.group(1))
        return max(80, min(360, round(180 * (1 + percent / 100))))
