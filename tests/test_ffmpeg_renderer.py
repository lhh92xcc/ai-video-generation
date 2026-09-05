from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.rendering.ffmpeg_renderer import AudioTrackInput, FFmpegRenderError, FFmpegVideoRenderer
from app.rendering.subtitles import SubtitleCue, parse_srt, write_srt


def _playable_mp4(tmp_path: Path, name: str, color: str, duration: str = "1") -> bytes:
    output = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=10",
            "-t",
            duration,
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    return output.read_bytes()


def _playable_wav(tmp_path: Path, name: str = "voice.wav", duration: str = "2") -> bytes:
    output = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            duration,
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )
    return output.read_bytes()


def test_ffmpeg_renderer_concatenates_video_clips_and_validates_output(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for rendering tests")

    async def exercise() -> None:
        first = _playable_mp4(tmp_path, "first.mp4", "blue")
        second = _playable_mp4(tmp_path, "second.mp4", "red")

        rendered = await FFmpegVideoRenderer(timeout_seconds=30).render([first, second])

        assert rendered.content_type == "video/mp4"
        assert rendered.input_clip_count == 2
        assert rendered.probe.width == 160
        assert rendered.probe.height == 90
        assert rendered.probe.codec_name == "h264"
        assert "mp4" in rendered.probe.format_name
        assert 1.7 <= rendered.probe.duration_seconds <= 2.3
        assert rendered.as_metadata()["ffprobe"] == rendered.probe.as_metadata()

    asyncio.run(exercise())


def test_ffmpeg_renderer_rejects_invalid_input(tmp_path: Path) -> None:
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is required for rendering input validation")

    async def exercise() -> None:
        with pytest.raises(FFmpegRenderError) as error:
            await FFmpegVideoRenderer().render([b"not-a-video"])
        assert error.value.code == "FFMPEG_INPUT_INVALID"

    asyncio.run(exercise())


def test_ffmpeg_renderer_adds_audio_and_handles_srt_subtitles(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for audio/subtitle rendering tests")

    async def exercise() -> None:
        first = _playable_mp4(tmp_path, "first-av.mp4", "blue", duration="1")
        second = _playable_mp4(tmp_path, "second-av.mp4", "red", duration="1")
        audio = _playable_wav(tmp_path)
        cues = [
            SubtitleCue(0.0, 0.9, "第一句字幕"),
            SubtitleCue(1.0, 1.9, "第二句字幕"),
        ]

        rendered = await FFmpegVideoRenderer(timeout_seconds=30).render(
            [first, second],
            audio_content=audio,
            audio_content_type="audio/wav",
        )

        assert rendered.content_type == "video/mp4"
        assert rendered.probe.audio_stream_count == 1
        assert rendered.subtitle_count == 0
        assert rendered.as_metadata()["has_audio"] is True
        assert 1.7 <= rendered.probe.duration_seconds <= 2.3

        srt_path = tmp_path / "expected.srt"
        write_srt(cues, srt_path)
        assert "00:00:00,000 --> 00:00:00,900" in srt_path.read_text(encoding="utf-8")
        assert "第一句字幕" in srt_path.read_text(encoding="utf-8")

        if FFmpegVideoRenderer()._supports_filter("subtitles"):
            rendered_with_subtitles = await FFmpegVideoRenderer(timeout_seconds=30).render(
                [first, second],
                audio_content=audio,
                audio_content_type="audio/wav",
                subtitles=cues,
            )
            assert rendered_with_subtitles.subtitle_count == 2
        else:
            with pytest.raises(FFmpegRenderError) as error:
                await FFmpegVideoRenderer(timeout_seconds=30).render(
                    [first, second],
                    audio_content=audio,
                    audio_content_type="audio/wav",
                    subtitles=cues,
                )
            assert error.value.code == "FFMPEG_SUBTITLE_UNAVAILABLE"

    asyncio.run(exercise())


def test_ffmpeg_renderer_mixes_timeline_audio_and_loops_bgm(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for rendering tests")

    async def exercise() -> None:
        first = _playable_mp4(tmp_path, "timeline-first.mp4", "blue", duration="1")
        second = _playable_mp4(tmp_path, "timeline-second.mp4", "red", duration="1")
        narration = _playable_wav(tmp_path, "narration.wav", duration="1")
        bgm = _playable_wav(tmp_path, "bgm.wav", duration="0.4")

        rendered = await FFmpegVideoRenderer(timeout_seconds=30).render(
            [first, second],
            audio_tracks=[
                AudioTrackInput(
                    content=narration,
                    content_type="audio/wav",
                    track_type="narration",
                    volume=1.0,
                ),
                AudioTrackInput(
                    content=bgm,
                    content_type="audio/wav",
                    track_type="bgm",
                    volume=0.12,
                    loop=True,
                    fade_in_seconds=0.1,
                    fade_out_seconds=0.2,
                ),
            ],
        )

        assert rendered.audio_track_count == 2
        assert rendered.probe.audio_stream_count == 1
        assert rendered.as_metadata()["audio_track_count"] == 2
        assert 1.7 <= rendered.probe.duration_seconds <= 2.3

    asyncio.run(exercise())


def test_ffmpeg_renderer_keeps_single_narration_track_out_of_amix() -> None:
    filter_graph = FFmpegVideoRenderer._audio_filter(
        [AudioTrackInput(content=b"audio", content_type="audio/wav")],
        3.0,
    )

    assert "amix" not in filter_graph
    assert "aresample=async=0:first_pts=0" in filter_graph
    assert "[a1]atrim=duration=3.000" in filter_graph


def test_parse_srt_returns_ordered_cues() -> None:
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:00,900\n第一句\n\n"
        "2\n00:00:01,000 --> 00:00:01,900\n第二句\n".encode("utf-8")
    )

    assert len(cues) == 2
    assert cues[0].start_seconds == 0
    assert cues[1].end_seconds == 1.9


def test_ffmpeg_renderer_wraps_long_subtitles_for_portrait_video() -> None:
    cues = (
        SubtitleCue(0.0, 2.0, "这是一段需要在竖屏画面中换行显示的中文字幕"),
    )

    prepared = FFmpegVideoRenderer._prepare_subtitles(cues, 576)

    assert prepared[0].text == "这是一段需要在竖屏画面中\n换行显示的中文字幕"


def test_ffmpeg_renderer_rejects_invalid_audio_input() -> None:
    async def exercise() -> None:
        with pytest.raises(FFmpegRenderError) as error:
            await FFmpegVideoRenderer().render(
                [b"video"],
                audio_content=b"audio",
                audio_content_type="video/mp4",
            )
        assert error.value.code == "FFMPEG_INPUT_INVALID"

    asyncio.run(exercise())


def test_ffmpeg_renderer_reports_missing_binary() -> None:
    async def exercise() -> None:
        with pytest.raises(FFmpegRenderError) as error:
            await FFmpegVideoRenderer(binary="ffmpeg-binary-does-not-exist").render([b"content"])
        assert error.value.code == "FFMPEG_UNAVAILABLE"

    asyncio.run(exercise())
