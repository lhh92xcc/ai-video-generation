"""Bounded FFmpeg time fitting for narration segments.

The duration fitter is deliberately separate from TTS generation. A provider
still returns its original audio Artifact, while an orchestration layer may
derive a second, auditable Artifact when a narration segment exceeds the
visual time budget assigned to its shot.
"""

from __future__ import annotations

import asyncio
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.providers.errors_audio import AudioDurationFitError


@dataclass(frozen=True, slots=True)
class AudioDurationFitResult:
    """Audio bytes and the traceable operation applied to them."""

    content: bytes
    content_type: str
    changed: bool
    tempo_factor: float
    metadata: dict[str, object]


class FFmpegAudioDurationFitter:
    """Shorten audio with a bounded ``atempo`` operation when necessary.

    The fitter never slows audio down to fill a shot and never crops the input
    waveform. If the required speed-up is larger than ``max_tempo_factor`` it
    fails with a stable error so the caller can shorten the narration or give
    the shot more time.
    """

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 120,
        max_tempo_factor: float = 1.08,
        tolerance_seconds: float = 0.02,
    ) -> None:
        if max_tempo_factor <= 1:
            raise ValueError("max_tempo_factor must be greater than 1")
        if tolerance_seconds < 0:
            raise ValueError("tolerance_seconds must not be negative")
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_tempo_factor = max_tempo_factor
        self.tolerance_seconds = tolerance_seconds

    async def fit(
        self,
        content: bytes,
        content_type: str,
        source_duration_seconds: float,
        target_duration_seconds: float,
    ) -> AudioDurationFitResult:
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if not content or not normalized_type.startswith("audio/"):
            raise AudioDurationFitError(
                "AUDIO_DURATION_FIT_INPUT_INVALID",
                "Narration duration fitting requires non-empty audio content",
            )
        if (
            not math.isfinite(source_duration_seconds)
            or source_duration_seconds <= 0
            or not math.isfinite(target_duration_seconds)
            or target_duration_seconds <= 0
        ):
            raise AudioDurationFitError(
                "AUDIO_DURATION_FIT_INPUT_INVALID",
                "Narration source and target durations must be positive finite values",
            )
        if target_duration_seconds > 3600:
            raise AudioDurationFitError(
                "AUDIO_DURATION_FIT_INPUT_INVALID",
                "Narration target duration must not exceed 3600 seconds",
            )

        tempo_factor = source_duration_seconds / target_duration_seconds
        if tempo_factor <= 1 + self.tolerance_seconds / target_duration_seconds:
            return AudioDurationFitResult(
                content=content,
                content_type=normalized_type,
                changed=False,
                tempo_factor=1.0,
                metadata={
                    "duration_fit": "not_needed",
                    "filter": "none",
                    "source_duration_seconds": round(source_duration_seconds, 6),
                    "target_duration_seconds": round(target_duration_seconds, 6),
                    "tempo_factor": 1.0,
                    "max_tempo_factor": self.max_tempo_factor,
                },
            )

        if tempo_factor > self.max_tempo_factor + 1e-6:
            raise AudioDurationFitError(
                "AUDIO_DURATION_FIT_UNSAFE",
                (
                    f"Narration requires {tempo_factor:.3f}x speed-up to fit "
                    f"{target_duration_seconds:.3f}s, above the safe limit "
                    f"{self.max_tempo_factor:.3f}x"
                ),
            )
        if shutil.which(self.binary) is None:
            raise AudioDurationFitError(
                "AUDIO_DURATION_FIT_UNAVAILABLE",
                f"{self.binary} is required to fit narration duration",
            )

        with TemporaryDirectory(prefix="ai-audio-duration-fit-") as directory:
            root = Path(directory)
            input_path = root / f"input{self._suffix_for_mime(normalized_type)}"
            output_path = root / "fitted.wav"
            await asyncio.to_thread(input_path.write_bytes, content)
            command = [
                self.binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(input_path),
                "-af",
                (
                    "aresample=async=1:first_pts=0,"
                    f"atempo={tempo_factor:.6f},"
                    f"apad=whole_dur={target_duration_seconds:.6f},"
                    f"atrim=duration={target_duration_seconds:.6f},"
                    "asetpts=N/SR/TB"
                ),
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(output_path),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise AudioDurationFitError(
                    "AUDIO_DURATION_FIT_UNAVAILABLE",
                    f"{self.binary} is required to fit narration duration",
                ) from exc

            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise AudioDurationFitError(
                    "AUDIO_DURATION_FIT_TIMEOUT",
                    "FFmpeg timed out while fitting narration duration",
                ) from exc

            if process.returncode != 0 or not output_path.is_file():
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg could not fit narration duration"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise AudioDurationFitError("AUDIO_DURATION_FIT_FAILED", message)
            fitted_content = await asyncio.to_thread(output_path.read_bytes)

        if not fitted_content:
            raise AudioDurationFitError(
                "AUDIO_DURATION_FIT_FAILED",
                "FFmpeg produced an empty fitted narration Artifact",
            )
        return AudioDurationFitResult(
            content=fitted_content,
            content_type="audio/wav",
            changed=True,
            tempo_factor=tempo_factor,
            metadata={
                "duration_fit": "atempo",
                "filter": "atempo",
                "source_duration_seconds": round(source_duration_seconds, 6),
                "target_duration_seconds": round(target_duration_seconds, 6),
                "tempo_factor": round(tempo_factor, 6),
                "max_tempo_factor": self.max_tempo_factor,
                "output_sample_rate": 24000,
                "output_channel_count": 1,
                "output_format": "wav",
            },
        )

    @staticmethod
    def _suffix_for_mime(content_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/webm": ".webm",
        }.get(content_type, ".audio")
