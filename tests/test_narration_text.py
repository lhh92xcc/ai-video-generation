from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.media.narration_text import (
    build_episode_narration_text,
    join_continuous_narration_units,
    join_editorial_narration_units,
    join_narration_units_with_boundaries,
    ensure_narration_unit_boundary,
    join_narration_units,
    normalize_narration_text,
)


def test_normalize_narration_text_removes_layout_breaks_and_invisible_separators() -> None:
    assert normalize_narration_text("第一句\n\n第二句\u2060第三句") == "第一句 第二句第三句"


def test_join_narration_units_uses_natural_boundaries_without_speaker_labels() -> None:
    assert join_narration_units(["雨停了。", "林默：看那边", "她说，"]) == "雨停了 林默：看那边 她说"


def test_join_narration_units_can_join_visual_fragments_without_an_audio_boundary() -> None:
    assert join_narration_units(
        ["钟声。", "今晚三声钟响。"],
        separator="",
    ) == "钟声今晚三声钟响"


def test_join_narration_units_can_preserve_explicit_sentence_boundaries() -> None:
    assert join_narration_units(
        ["雨停了", "林默：看那边"],
        preserve_terminal_punctuation=True,
    ) == "雨停了。 林默：看那边。"


def test_join_continuous_narration_units_softens_editing_boundaries() -> None:
    assert join_continuous_narration_units(
        ["第一段。", "第二段。", "第三段。", "第四段。", "第五段。"]
    ) == "第一段，第二段，第三段，第四段，第五段。"


def test_join_narration_units_with_boundaries_keeps_one_coherent_sentence_plan() -> None:
    assert join_narration_units_with_boundaries(
        ["旧城区里，林默听见钟声", "今晚第三声落下，指针停住", "女孩推门而来"],
        ["。", "，"],
    ) == "旧城区里，林默听见钟声。今晚第三声落下，指针停住，女孩推门而来。"


def test_join_narration_units_with_boundaries_supports_mixed_story_beats() -> None:
    assert join_narration_units_with_boundaries(
        ["第一镜头", "第二镜头", "第三镜头", "第四镜头"],
        ["，", "；", "，"],
    ) == "第一镜头，第二镜头；第三镜头，第四镜头。"


def test_join_narration_units_with_boundaries_can_preserve_editorial_punctuation() -> None:
    assert join_narration_units_with_boundaries(
        ["第一镜头，", "第二镜头。", "第三镜头。"],
        ["", ""],
        final_separator="",
        preserve_unit_terminal_punctuation=True,
    ) == "第一镜头，第二镜头。第三镜头。"


def test_join_narration_units_with_boundaries_rejects_wrong_separator_count() -> None:
    with pytest.raises(ValueError, match="one separator per unit boundary"):
        join_narration_units_with_boundaries(["第一段", "第二段"], [])


def test_ensure_narration_unit_boundary_preserves_closing_quote() -> None:
    assert ensure_narration_unit_boundary("她说：“别回头”") == "她说：“别回头。”"


def test_build_episode_narration_text_does_not_speak_dialogue_speaker_names() -> None:
    script = SimpleNamespace(
        content=SimpleNamespace(
            scenes=[
                SimpleNamespace(
                    narration="门开了。",
                    dialogues=[SimpleNamespace(speaker="林默", text="你是谁？")],
                )
            ]
        )
    )

    assert build_episode_narration_text(script) == "门开了。你是谁？"


def test_join_editorial_narration_units_preserves_script_punctuation() -> None:
    assert join_editorial_narration_units(
        ["雨夜里，钟表店只剩林默一个人。", "第三声落下时，所有指针同时停住。"]
    ) == "雨夜里，钟表店只剩林默一个人。第三声落下时，所有指针同时停住。"


def test_join_editorial_narration_units_only_falls_back_for_missing_punctuation() -> None:
    assert join_editorial_narration_units(["门开了", "林默抬头。"]) == "门开了，林默抬头。"
