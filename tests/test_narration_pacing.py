from __future__ import annotations

from app.media.narration_pacing import split_narration_text


def test_split_narration_text_preserves_strong_boundaries_and_pause_policy() -> None:
    segments = split_narration_text("雨停了。林默抬头，发现钟表全部停在十二点。")

    assert [segment.text for segment in segments] == [
        "雨停了。",
        "林默抬头，",
        "发现钟表全部停在十二点。",
    ]
    assert segments[0].pause_after_seconds > segments[1].pause_after_seconds
    assert segments[-1].pause_after_seconds == 0.0


def test_split_narration_text_uses_soft_boundary_before_hard_cut() -> None:
    segments = split_narration_text(
        "他推开门，看到走廊尽头亮着一盏灯，然后听见身后传来脚步声。",
        max_segment_characters=14,
    )

    assert len(segments) >= 3
    assert all(len(segment.text) <= 14 for segment in segments)
    assert any(segment.boundary in {"，", ",", "：", ":"} for segment in segments[:-1])


def test_split_narration_text_rejects_unreasonably_short_limit() -> None:
    try:
        split_narration_text("测试", max_segment_characters=3)
    except ValueError as error:
        assert "at least 4" in str(error)
    else:
        raise AssertionError("expected a validation error")
