"""FFmpeg renderer used by the multi-shot assembly stage."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.media.video_validation import (
    FFprobeVideoValidator,
    VideoArtifactValidator,
    VideoProbeResult,
)
from app.providers.errors_video import VideoArtifactValidationError
from app.rendering.subtitles import SubtitleCue, write_srt


class FFmpegRenderError(Exception):
    """Stable error raised by the FFmpeg renderer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AudioTrackInput:
    """Validated-at-boundary audio content and its placement on the timeline."""

    content: bytes
    content_type: str
    track_type: str = "narration"
    start_seconds: float = 0.0
    volume: float = 1.0
    loop: bool = False
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class RenderedVideo:
    content: bytes
    content_type: str
    probe: VideoProbeResult
    input_clip_count: int
    subtitle_count: int = 0
    audio_track_count: int = 0
    render_preset: str = "medium"
    render_crf: int = 18
    render_tune: str = "animation"

    def as_metadata(self) -> dict[str, object]:
        return {
            "renderer": "ffmpeg",
            "input_clip_count": self.input_clip_count,
            "content_type": self.content_type,
            "has_audio": self.probe.audio_stream_count > 0,
            "audio_track_count": self.audio_track_count,
            "subtitle_count": self.subtitle_count,
            "encoding": {
                "video_codec": "libx264",
                "preset": self.render_preset,
                "crf": self.render_crf,
                "tune": self.render_tune or None,
            },
            "ffprobe": self.probe.as_metadata(),
        }


class FFmpegVideoRenderer:
    """Concatenate compatible video clips and mix optional timeline audio/SRT."""

    _ENCODER_PRESETS = frozenset(
        {
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        }
    )
    _ENCODER_TUNES = frozenset(
        {"", "animation", "film", "grain", "stillimage", "fastdecode", "zerolatency"}
    )

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 300,
        video_validator: VideoArtifactValidator | None = None,
        render_preset: str = "medium",
        render_crf: int = 18,
        render_tune: str = "animation",
    ) -> None:
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        normalized_preset = render_preset.strip().lower()
        if normalized_preset not in self._ENCODER_PRESETS:
            raise ValueError(f"Unsupported FFmpeg x264 preset: {render_preset!r}")
        normalized_tune = render_tune.strip().lower()
        if normalized_tune not in self._ENCODER_TUNES:
            raise ValueError(f"Unsupported FFmpeg x264 tune: {render_tune!r}")
        self.render_preset = normalized_preset
        self.render_crf = max(0, min(51, int(render_crf)))
        self.render_tune = normalized_tune
        self._video_validator = video_validator or FFprobeVideoValidator(
            timeout_seconds=self.timeout_seconds
        )

    async def render(
        self,
        clips: Sequence[bytes],
        input_content_types: Sequence[str] | None = None,
        audio_content: bytes | None = None,
        audio_content_type: str | None = None,
        subtitles: Sequence[SubtitleCue] | None = None,
        audio_tracks: Sequence[AudioTrackInput] | None = None,
    ) -> RenderedVideo:
        if not clips or any(not clip for clip in clips):
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "At least one non-empty video clip is required",
            )
        if shutil.which(self.binary) is None:
            raise FFmpegRenderError(
                "FFMPEG_UNAVAILABLE",
                f"{self.binary} is required to render video artifacts",
            )

        content_types = self._normalize_content_types(clips, input_content_types)
        probes = await self._validate_inputs(clips, content_types)
        self._require_compatible_inputs(content_types, probes)
        video_duration = sum(probe.duration_seconds for probe in probes)
        if video_duration <= 0 or video_duration > 3600:
            raise FFmpegRenderError(
                "FFMPEG_TIMELINE_INVALID",
                "Rendered video duration must be greater than 0 and no more than 3600 seconds",
            )
        normalized_tracks = self._normalize_audio_tracks(
            audio_content,
            audio_content_type,
            audio_tracks,
            video_duration,
        )
        subtitle_cues = tuple(subtitles or ())
        self._validate_subtitles(subtitle_cues, video_duration)
        if subtitle_cues and not await asyncio.to_thread(self._supports_filter, "subtitles"):
            raise FFmpegRenderError(
                "FFMPEG_SUBTITLE_UNAVAILABLE",
                "The installed FFmpeg does not provide the subtitles/libass filter",
            )

        with TemporaryDirectory(prefix="ai-video-render-") as directory:
            temp_dir = Path(directory)
            input_paths: list[Path] = []
            for index, content in enumerate(clips, start=1):
                suffix = ".webm" if content_types[index - 1] == "video/webm" else ".mp4"
                input_path = temp_dir / f"clip-{index:04d}{suffix}"
                await asyncio.to_thread(input_path.write_bytes, content)
                input_paths.append(input_path)

            concat_path = temp_dir / "concat.txt"
            concat_content = "".join(
                f"file '{self._escape_concat_path(path)}'\n" for path in input_paths
            )
            await asyncio.to_thread(concat_path.write_text, concat_content, "utf-8")

            audio_paths: list[Path] = []
            for index, track in enumerate(normalized_tracks, start=1):
                audio_path = temp_dir / f"audio-{index:04d}{self._audio_suffix(track.content_type)}"
                await asyncio.to_thread(audio_path.write_bytes, track.content)
                audio_paths.append(audio_path)

            subtitle_path: Path | None = None
            if subtitle_cues:
                subtitle_cues = self._prepare_subtitles(subtitle_cues, probes[0].width)
                subtitle_path = temp_dir / "subtitles.srt"
                await asyncio.to_thread(write_srt, list(subtitle_cues), subtitle_path)

            output_path = temp_dir / "rendered.mp4"
            await self._run_ffmpeg(
                concat_path,
                output_path,
                audio_paths,
                normalized_tracks,
                subtitle_path,
                video_duration,
            )
            try:
                rendered_content = await asyncio.to_thread(output_path.read_bytes)
            except (FileNotFoundError, OSError) as exc:
                raise FFmpegRenderError(
                    "FFMPEG_RENDER_FAILED",
                    "FFmpeg did not produce an output video",
                ) from exc

        try:
            output_probe = await self._video_validator.validate_bytes(
                rendered_content,
                "video/mp4",
            )
        except VideoArtifactValidationError as exc:
            raise FFmpegRenderError(
                "RENDER_ARTIFACT_NOT_PLAYABLE",
                f"Rendered video failed playback validation: {exc.message}",
            ) from exc

        return RenderedVideo(
            content=rendered_content,
            content_type="video/mp4",
            probe=output_probe,
            input_clip_count=len(clips),
            subtitle_count=len(subtitle_cues),
            audio_track_count=len(normalized_tracks),
            render_preset=self.render_preset,
            render_crf=self.render_crf,
            render_tune=self.render_tune,
        )

    @staticmethod
    def _normalize_audio_tracks(
        audio_content: bytes | None,
        audio_content_type: str | None,
        audio_tracks: Sequence[AudioTrackInput] | None,
        video_duration: float,
    ) -> tuple[AudioTrackInput, ...]:
        if audio_content is not None and audio_tracks:
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "Use either legacy audio_content or audio_tracks, not both",
            )
        if audio_content is not None:
            content_type = FFmpegVideoRenderer._normalize_audio_type(
                audio_content,
                audio_content_type,
            )
            assert content_type is not None
            audio_tracks = (
                AudioTrackInput(
                    content=audio_content,
                    content_type=content_type,
                ),
            )
        elif audio_content_type is not None:
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "audio_content_type requires audio_content",
            )

        normalized: list[AudioTrackInput] = []
        for track in audio_tracks or ():
            content_type = FFmpegVideoRenderer._normalize_audio_type(
                track.content,
                track.content_type,
            )
            assert content_type is not None
            if track.track_type not in {"narration", "bgm"}:
                raise FFmpegRenderError(
                    "FFMPEG_AUDIO_TIMELINE_INVALID",
                    "Audio track type must be narration or bgm",
                )
            if not 0 <= track.start_seconds < video_duration:
                raise FFmpegRenderError(
                    "FFMPEG_AUDIO_TIMELINE_INVALID",
                    "Audio track start must be within the rendered video duration",
                )
            if not 0 <= track.volume <= 2:
                raise FFmpegRenderError(
                    "FFMPEG_AUDIO_TIMELINE_INVALID",
                    "Audio track volume must be between 0 and 2",
                )
            if track.fade_in_seconds < 0 or track.fade_out_seconds < 0:
                raise FFmpegRenderError(
                    "FFMPEG_AUDIO_TIMELINE_INVALID",
                    "Audio fade durations must not be negative",
                )
            if track.fade_in_seconds > 120 or track.fade_out_seconds > 120:
                raise FFmpegRenderError(
                    "FFMPEG_AUDIO_TIMELINE_INVALID",
                    "Audio fade durations must not exceed 120 seconds",
                )
            normalized.append(
                AudioTrackInput(
                    content=track.content,
                    content_type=content_type,
                    track_type=track.track_type,
                    start_seconds=track.start_seconds,
                    volume=track.volume,
                    loop=track.loop,
                    fade_in_seconds=track.fade_in_seconds,
                    fade_out_seconds=track.fade_out_seconds,
                )
            )
        if len(normalized) > 32:
            raise FFmpegRenderError(
                "FFMPEG_AUDIO_TIMELINE_INVALID",
                "No more than 32 audio tracks are supported",
            )
        return tuple(normalized)

    @staticmethod
    def _normalize_audio_type(
        audio_content: bytes | None,
        audio_content_type: str | None,
    ) -> str | None:
        if audio_content is None:
            if audio_content_type is not None:
                raise FFmpegRenderError(
                    "FFMPEG_INPUT_INVALID",
                    "audio_content_type requires audio_content",
                )
            return None
        if not audio_content:
            raise FFmpegRenderError("FFMPEG_INPUT_INVALID", "Audio content must not be empty")
        if not audio_content_type or not audio_content_type.startswith("audio/"):
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "Audio content must use an audio MIME type",
            )
        return audio_content_type.split(";", 1)[0].strip().lower()

    @staticmethod
    def _audio_suffix(content_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
        }.get(content_type, ".audio")

    @staticmethod
    def _validate_subtitles(subtitles: Sequence[SubtitleCue], video_duration: float) -> None:
        previous_end = 0.0
        for cue in subtitles:
            if cue.start_seconds < previous_end:
                raise FFmpegRenderError(
                    "FFMPEG_SUBTITLE_INVALID",
                    "Subtitle cues must be ordered and must not overlap",
                )
            if cue.end_seconds > video_duration:
                raise FFmpegRenderError(
                    "FFMPEG_SUBTITLE_INVALID",
                    "Subtitle cues must be within the rendered video duration",
                )
            previous_end = cue.end_seconds

    @staticmethod
    def _normalize_content_types(
        clips: Sequence[bytes],
        input_content_types: Sequence[str] | None,
    ) -> list[str]:
        if input_content_types is None:
            return ["video/mp4"] * len(clips)
        if len(input_content_types) != len(clips):
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "input_content_types must have one MIME type per clip",
            )
        normalized = [content_type.strip().lower() for content_type in input_content_types]
        if any(not content_type.startswith("video/") for content_type in normalized):
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "All input clips must use a video MIME type",
            )
        return normalized

    async def _validate_inputs(
        self,
        clips: Sequence[bytes],
        content_types: Sequence[str],
    ) -> list[VideoProbeResult]:
        probes: list[VideoProbeResult] = []
        for content, content_type in zip(clips, content_types, strict=True):
            try:
                probes.append(await self._video_validator.validate_bytes(content, content_type))
            except VideoArtifactValidationError as exc:
                raise FFmpegRenderError(
                    "FFMPEG_INPUT_INVALID",
                    f"Input video clip failed validation: {exc.message}",
                ) from exc
        return probes

    @staticmethod
    def _require_compatible_inputs(
        content_types: Sequence[str],
        probes: Sequence[VideoProbeResult],
    ) -> None:
        if len(set(content_types)) != 1:
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "Initial renderer requires all clips to use the same video container",
            )
        first = probes[0]
        if any(probe.width != first.width or probe.height != first.height for probe in probes[1:]):
            raise FFmpegRenderError(
                "FFMPEG_INPUT_INVALID",
                "Initial renderer requires all clips to use the same dimensions",
            )

    async def _run_ffmpeg(
        self,
        concat_path: Path,
        output_path: Path,
        audio_paths: Sequence[Path] = (),
        audio_tracks: Sequence[AudioTrackInput] = (),
        subtitle_path: Path | None = None,
        video_duration: float = 0,
    ) -> None:
        command = [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
        ]
        for audio_path, track in zip(audio_paths, audio_tracks, strict=True):
            if track.loop:
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(audio_path)])
        command.extend(["-map", "0:v:0"])
        if audio_paths:
            filter_complex = self._audio_filter(audio_tracks, video_duration)
            command.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
        else:
            command.append("-an")
        if subtitle_path is not None:
            command.extend(["-vf", self._subtitle_filter(subtitle_path)])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                self.render_preset,
                "-crf",
                str(self.render_crf),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )
        if self.render_tune:
            command.extend(["-tune", self.render_tune])
        if audio_paths:
            command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        if video_duration > 0:
            command.extend(["-t", f"{video_duration:.3f}"])
        command.append(str(output_path))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise FFmpegRenderError(
                "FFMPEG_UNAVAILABLE",
                f"{self.binary} is required to render video artifacts",
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise FFmpegRenderError(
                "FFMPEG_TIMEOUT",
                "FFmpeg timed out while rendering the video artifact",
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "FFmpeg failed to render the video artifact"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise FFmpegRenderError("FFMPEG_RENDER_FAILED", message)

    @staticmethod
    def _audio_filter(
        audio_tracks: Sequence[AudioTrackInput],
        video_duration: float,
    ) -> str:
        chains: list[str] = []
        # A portfolio narration is one already-mixed continuous waveform. Do
        # not send that waveform through amix: amix is useful when narration
        # and BGM coexist, but it adds an unnecessary mixing stage to a single
        # voice track and can obscure the exact source timeline.
        resample_async = 1 if len(audio_tracks) > 1 else 0
        for index, track in enumerate(audio_tracks, start=1):
            parts = [f"[{index}:a]aresample=async={resample_async}:first_pts=0"]
            if track.volume != 1:
                parts.append(f"volume={track.volume:.4f}")
            if track.start_seconds > 0:
                parts.append(f"adelay={round(track.start_seconds * 1000)}:all=1")
            if track.fade_in_seconds > 0:
                parts.append(
                    f"afade=t=in:st={track.start_seconds:.3f}:d={track.fade_in_seconds:.3f}"
                )
            if track.fade_out_seconds > 0:
                fade_start = max(0.0, video_duration - track.fade_out_seconds)
                parts.append(
                    f"afade=t=out:st={fade_start:.3f}:d={track.fade_out_seconds:.3f}"
                )
            parts.append(f"apad=whole_dur={video_duration:.3f}")
            chains.append(",".join(parts) + f"[a{index}]")
        if len(audio_tracks) == 1:
            chains.append(
                "[a1]atrim="
                f"duration={video_duration:.3f},asetpts=N/SR/TB[aout]"
            )
            return ";".join(chains)
        inputs = "".join(f"[a{index}]" for index in range(1, len(audio_tracks) + 1))
        chains.append(
            f"{inputs}amix=inputs={len(audio_tracks)}:duration=longest:"
            f"dropout_transition=0,atrim=duration={video_duration:.3f},"
            "asetpts=N/SR/TB[aout]"
        )
        return ";".join(chains)

    @staticmethod
    def _escape_concat_path(path: Path) -> str:
        return str(path).replace("'", "'\\''")

    @staticmethod
    def _subtitle_filter(path: Path) -> str:
        escaped = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        style = (
            "FontName=Noto Sans CJK SC,FontSize=14,Outline=1,Shadow=0,"
            "Alignment=2,MarginV=36,MarginL=24,MarginR=24"
        )
        return f"subtitles=filename='{escaped}':force_style='{style}'"

    @staticmethod
    def _prepare_subtitles(
        subtitles: Sequence[SubtitleCue],
        width: int,
    ) -> tuple[SubtitleCue, ...]:
        """Wrap long CJK cues for portrait output without changing cue timing."""

        max_characters = 12 if width <= 720 else 20
        prepared: list[SubtitleCue] = []
        for cue in subtitles:
            wrapped_lines: list[str] = []
            for line in cue.text.splitlines() or [cue.text]:
                wrapped_lines.extend(
                    textwrap.wrap(
                        line,
                        width=max_characters,
                        break_long_words=True,
                        break_on_hyphens=False,
                    )
                    or [line]
                )
            prepared.append(
                SubtitleCue(
                    cue.start_seconds,
                    cue.end_seconds,
                    "\n".join(wrapped_lines),
                )
            )
        return tuple(prepared)

    def _supports_filter(self, filter_name: str) -> bool:
        try:
            result = subprocess.run(
                [self.binary, "-hide_banner", "-filters"],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        return f" {filter_name} ".encode("utf-8") in result.stdout
