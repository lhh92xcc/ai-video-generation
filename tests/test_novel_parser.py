from app.services.novel_parser import split_chapters


def test_split_chapters_supports_chinese_and_markdown_headings() -> None:
    chapters = split_chapters(
        "序言\n\n第一章 初遇\n主角在雨中醒来。\n\n# 第二章 决定\n他决定离开。"
    )

    assert [chapter.chapter_number for chapter in chapters] == [0, 1, 2]
    assert [chapter.title for chapter in chapters] == ["序章", "第一章 初遇", "第二章 决定"]
    assert chapters[1].content == "主角在雨中醒来。"
    assert chapters[2].content == "他决定离开。"


def test_split_chapters_supports_english_heading_and_plain_text_fallback() -> None:
    english = split_chapters("Chapter 1: Arrival\nA new beginning.\n\nChapter 2: Choice\nA hard choice.")
    plain = split_chapters("没有章节标题的短文本。")

    assert [chapter.chapter_number for chapter in english] == [1, 2]
    assert english[0].title == "Chapter 1: Arrival"
    assert len(plain) == 1
    assert plain[0].title == "全文"
