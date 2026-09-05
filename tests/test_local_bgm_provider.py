from __future__ import annotations

import asyncio
import io
import wave

import pytest

from app.domain.models import BGMGenerationRequest, RightsStatus
from app.providers.errors_audio import BGMProviderError
from app.providers.local_bgm import LocalFileBGMProvider


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


def test_local_bgm_provider_reads_file_inside_source_root(tmp_path) -> None:
    async def exercise() -> None:
        (tmp_path / "licensed.wav").write_bytes(_wav_bytes())
        provider = LocalFileBGMProvider(tmp_path)

        result = await provider.generate_bgm(
            BGMGenerationRequest(
                source_path="licensed.wav",
                label="授权音乐",
                rights_status=RightsStatus.CONFIRMED,
                rights_holder="测试团队",
                rights_reference="license-001",
            )
        )

        assert result.provider == "local_file"
        assert result.mime_type == "audio/wav"
        assert result.metadata["source_type"] == "licensed_local_file"
        assert result.metadata["source_name"] == "licensed.wav"
        assert result.metadata["rights_status"] == "confirmed"
        assert result.metadata["rights_holder"] == "测试团队"
        assert result.metadata["rights_reference"] == "license-001"
        assert result.metadata["rights_review_required"] is False
        assert len(result.metadata["source_sha256"]) == 64
        assert result.audio_base64

    asyncio.run(exercise())


def test_local_bgm_provider_blocks_directory_traversal(tmp_path) -> None:
    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(_wav_bytes())

    async def exercise() -> None:
        provider = LocalFileBGMProvider(tmp_path)
        with pytest.raises(BGMProviderError, match="inside AI_VIDEO_BGM_SOURCE_ROOT") as error:
            await provider.generate_bgm(BGMGenerationRequest(source_path="../outside.wav"))
        assert error.value.code == "BGM_PROVIDER_SOURCE_FORBIDDEN"

    try:
        asyncio.run(exercise())
    finally:
        outside.unlink()


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("missing.wav", "BGM_PROVIDER_SOURCE_NOT_FOUND"),
        ("empty.wav", "BGM_PROVIDER_INVALID_SOURCE"),
        ("notes.txt", "BGM_PROVIDER_UNSUPPORTED_FORMAT"),
    ],
)
def test_local_bgm_provider_rejects_invalid_sources(tmp_path, filename, expected_code) -> None:
    if filename == "empty.wav":
        (tmp_path / filename).write_bytes(b"")
    elif filename == "notes.txt":
        (tmp_path / filename).write_text("not audio", encoding="utf-8")

    async def exercise() -> None:
        provider = LocalFileBGMProvider(tmp_path)
        with pytest.raises(BGMProviderError) as error:
            await provider.generate_bgm(BGMGenerationRequest(source_path=filename))
        assert error.value.code == expected_code

    asyncio.run(exercise())
