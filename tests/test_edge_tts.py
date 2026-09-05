from __future__ import annotations

import asyncio
import base64
import sys
from types import SimpleNamespace

from app.domain.models import TTSGenerationRequest
from app.providers.edge_tts import EdgeTTSProvider


def test_edge_tts_keeps_one_waveform_and_captures_word_boundaries(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs) -> None:
            observed.update({"text": text, "voice": voice, **kwargs})

        async def stream(self):
            yield {"type": "audio", "data": b"audio-part-1"}
            yield {
                "type": "WordBoundary",
                "offset": 1_000_000,
                "duration": 2_500_000,
                "text": "林默",
            }
            yield {"type": "audio", "data": b"audio-part-2"}

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate))

    result = asyncio.run(
        EdgeTTSProvider().generate_speech(
            TTSGenerationRequest(
                text="林默守着钟表店。",
                voice="zh-CN-XiaoxiaoNeural",
                rate="-10%",
                volume="+0%",
            )
        )
    )

    assert base64.b64decode(result.audio_base64) == b"audio-part-1audio-part-2"
    assert observed["boundary"] == "WordBoundary"
    assert result.metadata["synthesis_mode"] == "single_waveform"
    assert result.metadata["timing_source"] == "edge_tts_word_boundary"
    assert result.metadata["word_boundary_count"] == 1
    assert result.metadata["word_boundaries"] == [
        {
            "start_seconds": 0.1,
            "end_seconds": 0.35,
            "text": "林默",
        }
    ]
