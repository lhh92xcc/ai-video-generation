"""FFmpeg-backed audio normalization for stable BGM mixing inputs."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.providers.errors_audio import AudioNormalizationError


@dataclass(frozen=True, slots=True)
class NormalizedAudio:
    content: bytes
    content_type: str
    metadata: dict[str, object]


class AudioNormalizer:
    async def normalize(self, content: bytes, content_type: str) -> NormalizedAudio:
        raise NotImplementedError


class FFmpegAudioNormalizer(AudioNormalizer):
    """Convert audio to stereo 48 kHz WAV and apply FFmpeg ``loudnorm``."""

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 120,
        target_i_lufs: float = -16.0,
        target_lra_lu: float = 11.0,
        target_tp_dbtp: float = -1.5,
        sample_rate: int = 48_000,
        channel_count: int = 2,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.target_i_lufs = target_i_lufs
        self.target_lra_lu = target_lra_lu
        self.target_tp_dbtp = target_tp_dbtp
        self.sample_rate = sample_rate
        self.channel_count = channel_count

    async def normalize(self, content: bytes, content_type: str) -> NormalizedAudio:
        if not content:
            raise AudioNormalizationError(
                "AUDIO_NORMALIZATION_FAILED",
                "Cannot normalize empty audio content",
            )
        if not content_type.startswith("audio/"):
            raise AudioNormalizationError(
                "AUDIO_NORMALIZATION_FAILED",
                "Audio normalization requires an audio MIME type",
            )
        if shutil.which(self.binary) is None:
            raise AudioNormalizationError(
                "AUDIO_NORMALIZATION_UNAVAILABLE",
                f"{self.binary} is required to normalize audio artifacts",
            )

        with TemporaryDirectory(prefix="ai-audio-normalize-") as directory:
            input_path = Path(directory) / f"input{self._suffix_for_mime(content_type)}"
            output_path = Path(directory) / "normalized.wav"
            await asyncio.to_thread(input_path.write_bytes, content)
            command = [
                self.binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-map_metadata",
                "-1",
                "-af",
                (
                    f"loudnorm=I={self.target_i_lufs}:LRA={self.target_lra_lu}:"
                    f"TP={self.target_tp_dbtp}"
                ),
                "-ar",
                str(self.sample_rate),
                "-ac",
                str(self.channel_count),
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
                raise AudioNormalizationError(
                    "AUDIO_NORMALIZATION_UNAVAILABLE",
                    f"{self.binary} is required to normalize audio artifacts",
                ) from exc

            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise AudioNormalizationError(
                    "AUDIO_NORMALIZATION_TIMEOUT",
                    "FFmpeg timed out while normalizing the audio artifact",
                ) from exc

            if process.returncode != 0 or not output_path.exists():
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg could not normalize the audio artifact"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise AudioNormalizationError("AUDIO_NORMALIZATION_FAILED", message)

            normalized_content = await asyncio.to_thread(output_path.read_bytes)
            if not normalized_content:
                raise AudioNormalizationError(
                    "AUDIO_NORMALIZATION_FAILED",
                    "FFmpeg produced an empty normalized audio artifact",
                )

        return NormalizedAudio(
            content=normalized_content,
            content_type="audio/wav",
            metadata={
                "enabled": True,
                "filter": "loudnorm",
                "target_i_lufs": self.target_i_lufs,
                "target_lra_lu": self.target_lra_lu,
                "target_tp_dbtp": self.target_tp_dbtp,
                "sample_rate": self.sample_rate,
                "channel_count": self.channel_count,
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
        }.get(content_type.split(";", 1)[0].strip().lower(), ".audio")
