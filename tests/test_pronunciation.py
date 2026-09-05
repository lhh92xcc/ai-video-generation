from __future__ import annotations

from app.media.pronunciation import (
    PronunciationDictionary,
    map_word_boundaries_to_source,
)


def test_pronunciation_dictionary_uses_longest_match_first() -> None:
    dictionary = PronunciationDictionary.from_mapping(
        {
            "长": "chang",
            "长安": "常安",
        }
    )

    text, count, applied = dictionary.apply("长安的长")

    assert text == "常安的chang"
    assert count == 2
    assert applied == [
        {"source": "长安", "target": "常安", "count": "1"},
        {"source": "长", "target": "chang", "count": "1"},
    ]


def test_word_boundaries_map_provider_text_back_to_source_text() -> None:
    dictionary = PronunciationDictionary.from_mapping({"重明": "崇明"})
    source_text = "重明到了"
    tts_text, source_spans, _, _ = dictionary.apply_with_source_spans(source_text)

    mapped = map_word_boundaries_to_source(
        [
            {"start_seconds": 0.0, "end_seconds": 0.4, "text": "崇明"},
            {"start_seconds": 0.4, "end_seconds": 0.8, "text": "到了"},
        ],
        source_text,
        tts_text,
        source_spans,
    )

    assert tts_text == "崇明到了"
    assert mapped == [
        {"start_seconds": 0.0, "end_seconds": 0.4, "text": "重明"},
        {"start_seconds": 0.4, "end_seconds": 0.8, "text": "到了"},
    ]


def test_word_boundary_mapping_returns_none_when_provider_events_do_not_match() -> None:
    dictionary = PronunciationDictionary.from_mapping({"重明": "崇明"})
    source_text = "重明到了"
    tts_text, source_spans, _, _ = dictionary.apply_with_source_spans(source_text)

    mapped = map_word_boundaries_to_source(
        [{"start_seconds": 0.0, "end_seconds": 0.4, "text": "不存在"}],
        source_text,
        tts_text,
        source_spans,
    )

    assert mapped is None
