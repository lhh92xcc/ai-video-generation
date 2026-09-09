"""FFmpeg composition for a continuous multi-speaker narration waveform."""

from __future__ import annotations

import asyncio
import math
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
    segment_durations_seconds: Sequence[float] | None = None,
    transition_fade_ms: int = 24,
    room_tone_enabled: bool = True,
    room_tone_db: float = -52.0,
) -> bytes:
    """Join speaker clips into a stable 24 kHz mono WAV timeline.

    Measured segment durations enable timeline mixing with small edge fades
    and an optional, very quiet room-tone bed. Voice clips are never trimmed
    or time-stretched, so the caller's measured timing remains authoritative.
    Omitting measured durations retains the original concat behavior for
    backwards compatibility with external callers.
    """

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
    if segment_durations_seconds is not None and len(segment_durations_seconds) != len(segments):
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "There must be one measured duration per audio segment",
        )
    if segment_durations_seconds is not None and any(
        not math.isfinite(float(duration)) or float(duration) <= 0
        for duration in segment_durations_seconds
    ):
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "Measured audio durations must be finite and greater than zero",
        )
    if transition_fade_ms < 0 or transition_fade_ms > 120:
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "Transition fade must be between 0 and 120 milliseconds",
        )
    if not math.isfinite(room_tone_db) or room_tone_db < -80 or room_tone_db > -20:
        raise MultiVoiceAudioError(
            "AUDIO_COMPOSITION_INPUT_INVALID",
            "Room tone level must be between -80 and -20 dB",
        )
    if segment_durations_seconds is not None:
        return await _compose_timeline(
            segments,
            content_types,
            pauses_after_seconds,
            segment_durations_seconds,
            binary=binary,
            timeout_seconds=timeout_seconds,
            transition_fade_ms=transition_fade_ms,
            room_tone_enabled=room_tone_enabled,
            room_tone_db=room_tone_db,
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


async def _compose_timeline(
    segments: Sequence[bytes],
    content_types: Sequence[str],
    pauses_after_seconds: Sequence[float],
    segment_durations_seconds: Sequence[float],
    *,
    binary: str,
    timeout_seconds: int,
    transition_fade_ms: int,
    room_tone_enabled: bool,
    room_tone_db: float,
) -> bytes:
    """Mix measured speaker clips on one explicit timeline."""

    durations = [float(duration) for duration in segment_durations_seconds]
    total_duration = sum(durations) + sum(
        float(pause) for pause in pauses_after_seconds[:-1]
    )

    with TemporaryDirectory(prefix="ai-video-multi-voice-") as directory:
        root = Path(directory)
        segment_paths: list[Path] = []
        for index, (content, content_type) in enumerate(
            zip(segments, content_types, strict=True),
            1,
        ):
            path = root / f"segment-{index:03d}{_suffix_for_content_type(content_type)}"
            await asyncio.to_thread(path.write_bytes, content)
            segment_paths.append(path)

        command: list[str] = [binary, "-hide_banner", "-loglevel", "error", "-y"]
        for path in segment_paths:
            command.extend(["-i", str(path)])

        room_tone_input: int | None = None
        if room_tone_enabled:
            room_tone_input = len(segment_paths)
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    f"{total_duration:.6f}",
                    "-i",
                    "anoisesrc=color=pink:amplitude=1:sample_rate=24000",
                ]
            )

        filters: list[str] = []
        labels: list[str] = []
        cursor = 0.0
        fade_limit = float(transition_fade_ms) / 1000.0
        for index, duration in enumerate(durations):
            label = f"[voice{index}]"
            labels.append(label)
            voice_filter = (
                f"[{index}:a]aresample=24000,aformat=sample_fmts=s16:"
                "sample_rates=24000:channel_layouts=mono"
            )
            fade_duration = min(fade_limit, duration / 2.0)
            if fade_duration > 0:
                fade_start = max(0.0, duration - fade_duration)
                voice_filter += (
                    f",afade=t=in:st=0:d={fade_duration:.6f}:curve=qsin"
                    f",afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f}:curve=qsin"
                )
            delay_ms = max(0, round(cursor * 1000))
            filters.append(f"{voice_filter},adelay={delay_ms}:all=1{label}")
            cursor += duration
            if index < len(durations) - 1:
                cursor += float(pauses_after_seconds[index])

        if room_tone_input is not None:
            room_amplitude = 10 ** (room_tone_db / 20.0)
            room_label = "[room]"
            labels.append(room_label)
            filters.append(
                f"[{room_tone_input}:a]aresample=24000,aformat=sample_fmts=s16:"
                f"sample_rates=24000:channel_layouts=mono,volume={room_amplitude:.8f},"
                f"atrim=duration={total_duration:.6f},asetpts=PTS-STARTPTS{room_label}"
            )

        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:"
            "dropout_transition=0:normalize=0,aresample=24000,"
            "aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono[out]"
        )
        output_path = root / "multi-voice.wav"
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
                "-t",
                f"{total_duration:.6f}",
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
