"""Small, deterministic chapter splitter for TXT and Markdown novels."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChapterDraft:
    chapter_number: int
    title: str
    content: str
    start_offset: int
    end_offset: int


_CHAPTER_PATTERN = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:第\s*([0-9一二三四五六七八九十百千万零〇两]+)\s*([章节回部篇卷])\s*(.*)|chapter\s+(\d+)\s*(?:[:：.-]\s*)?(.*))\s*$",
    re.IGNORECASE,
)


def normalize_novel_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    return text.strip()


def split_chapters(text: str) -> list[ChapterDraft]:
    normalized = normalize_novel_text(text)
    if not normalized:
        return []

    lines = normalized.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for line in lines:
        match = _CHAPTER_PATTERN.match(line.rstrip("\n"))
        if match:
            chapter_number = _parse_number(match.group(1) or match.group(4))
            heading_title = line.strip().lstrip("#").strip()
            title = heading_title
            headings.append((offset, chapter_number, title))
        offset += len(line)

    if not headings:
        return [
            ChapterDraft(
                chapter_number=0,
                title="全文",
                content=normalized,
                start_offset=0,
                end_offset=len(normalized),
            )
        ]

    drafts: list[ChapterDraft] = []
    if headings[0][0] > 0:
        preface = normalized[: headings[0][0]].strip()
        if preface:
            drafts.append(
                ChapterDraft(
                    chapter_number=0,
                    title="序章",
                    content=preface,
                    start_offset=0,
                    end_offset=headings[0][0],
                )
            )

    for index, (start, number, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(normalized)
        body = normalized[start:end]
        body_lines = body.splitlines()
        content = "\n".join(body_lines[1:]).strip()
        if not content:
            content = title
        drafts.append(
            ChapterDraft(
                chapter_number=number,
                title=title,
                content=content,
                start_offset=start,
                end_offset=end,
            )
        )
    return drafts


def _parse_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            unit = units[char]
            if unit == 10000:
                section = (section + current) * unit
                total += section
                section = 0
            else:
                section += (current or 1) * unit
            current = 0
    return total + section + current
