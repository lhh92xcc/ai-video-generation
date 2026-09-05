from __future__ import annotations

import asyncio

from app.domain.models import SubtitleAlignmentCreateRequest
from app.providers.mock_subtitle_alignment import MockSentenceSubtitleAlignmentProvider


def test_mock_sentence_alignment_is_deterministic_and_does_not_read_audio() -> None:
    async def exercise() -> None:
        provider = MockSentenceSubtitleAlignmentProvider()
        request = SubtitleAlignmentCreateRequest(
            text="短句一。较长的第二句！",
            language="zh-CN",
            audio_duration_seconds=6.0,
        )
        result = await provider.align(request)

        assert result.precision == "sentence_estimate"
        assert result.metadata["audio_inspected"] is False
        assert [cue.text for cue in result.cues] == ["短句一。", "较长的第二句！"]
        assert result.cues[0].start_seconds == 0
        assert result.cues[-1].end_seconds == 6
        assert result.cues[0].end_seconds < result.cues[1].start_seconds + 0.000001

    asyncio.run(exercise())
