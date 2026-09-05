import pytest

from app.providers.errors_asr import SubtitleASRProviderError
from app.providers.whisper_cpp_asr import WhisperCppASRProvider


def test_whisper_cpp_parses_native_timestamp_json() -> None:
    cues = WhisperCppASRProvider._parse_payload(
        {
            "transcription": [
                {
                    "timestamps": {"from": "00:00:00,120", "to": "00:00:01,800"},
                    "text": "  第一幕开始。 ",
                }
            ]
        }
    )
    assert cues[0].text == "第一幕开始。"
    assert cues[0].start_seconds == pytest.approx(0.12)
    assert cues[0].end_seconds == pytest.approx(1.8)


def test_whisper_cpp_rejects_segments_without_valid_timing() -> None:
    with pytest.raises(SubtitleASRProviderError) as error:
        WhisperCppASRProvider._parse_payload({"segments": [{"text": "没有时间"}]})
    assert error.value.code == "ASR_PROVIDER_INVALID_RESPONSE"
