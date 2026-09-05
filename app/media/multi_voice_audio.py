"""FFmpeg composition for a continuous multi-speaker narration waveform."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory


class MultiVoiceAudioError(Exception):
    """Raised when multi-speaker audio cannot be composed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def concatenate_audio_segments(
    segments: Sequence[bytes],
    content_types: Sequence[str],
    pauses_after_seconds: Sequence[float],
    binary: str = "ffmpeg",
    timeout_seconds: int = 120,
) -> bytes:
    """Join speaker clips with explicit silence and return a 24 kHz mono WAV."""

    if not segments or len(segments) != len(content_types):
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "At least one audio segment and one MIME type per segment are required",
        )
    if len(pauses_after_seconds) != len(segments):
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "There must be one pause value per audio segment",
        )
    if shutil.which(binary) is None:
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_FFMPEG_UNAVAILABLE",
            f"{binary} is required to compose multi-speaker audio",
        )
    if any(not content for content in segments):
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "Audio segments must not be empty",
        )
    if any(pause < 0 or pause > 5 for pause in pauses_after_seconds):
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "Speaker pauses must be between 0 and 5 seconds",
        )

    with TemporaryDirectory(prefix="ai-video-multi-voice-") as directory:
        root = Path(directory)
        segment_paths: list[Path] = []
        for index, (content, content_type) in enumerate(zip(segments, content_types, strict=True), 1):
            suffix = _suffix_for_content_type(content_type)
            path = root / f"segment-{index:03d}{suffix}"
            await asyncio.to_thread(path.write_bytes, content)
            segment_paths.append(path)

        command: list[str] = [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        input_count = 0
        labels: list[str] = []
        for index, segment_path in enumerate(segment_paths):
            command.extend(["-i", str(segment_path)])
            labels.append(f"[{input_count}:a]")
            input_count += 1
            if index == len(segment_paths) - 1:
                continue
            pause = float(pauses_after_seconds[index])
            if pause > 0:
                command.extend(
                    [
                        "-f",
                        "lavfi",
                        "-t",
                        f"{pause:.3f}",
                        "-i",
                        "anullsrc=r=24000:cl=mono",
                    ]
                )
                labels.append(f"[{input_count}:a]")
                input_count += 1

        output_path = root / "multi-voice.wav"
        command.extend(
            [
                "-filter_complex",
                "".join(labels)
                + f"concat=n={len(labels)}:v=0:a=1,"
                "aresample=24000,aformat=sample_fmts=s16:sample_rates=24000:"
                "channel_layouts=mono[out]",
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise MultiVoiceAudioError(
                "AUDIO_COMPOSITION_FFMPEG_UNAVAILABLE",
                f"{binary} is required to compose multi-speaker audio",
            ) from exc
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=max(1, int(timeout_seconds)),
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise MultiVoiceAudioError(
                "AUDIO_COMPOSITION_TIMEOUT",
                "FFmpeg timed out while composing multi-speaker audio",
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "FFmpeg failed to compose multi-speaker audio"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise MultiVoiceAudioError("AUDIO_COMPOSITION_FAILED", message)
        try:
            return await asyncio.to_thread(output_path.read_bytes)
        except OSError as exc:
            raise MultiVoiceAudioError(
                "AUDIO_COMPOSITION_FAILED",
                "FFmpeg did not produce a multi-speaker WAV file",
            ) from exc


def _suffix_for_content_type(content_type: str) -> str:
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
