"""Story-bible provider used to validate the novel-analysis pipeline."""

from __future__ import annotations

from time import monotonic
from uuid import UUID

from app.domain.models import (
    CharacterProfile,
    ChapterRecord,
    LocationProfile,
    PropProfile,
    StoryBibleContent,
    StoryBibleRecord,
)


class StoryBibleGenerationProvider:
    async def generate(
        self,
        project_id: UUID,
        project_title: str,
        chapters: list[ChapterRecord],
    ) -> StoryBibleRecord:
        raise NotImplementedError


class MockStoryBibleProvider(StoryBibleGenerationProvider):
    """Return a deterministic, explicitly review-required story bible."""

    async def generate(
        self,
        project_id: UUID,
        project_title: str,
        chapters: list[ChapterRecord],
    ) -> StoryBibleRecord:
        started = monotonic()
        chapter_numbers = [chapter.chapter_number for chapter in chapters]
        content = StoryBibleContent(
            title=project_title,
            logline=f"围绕《{project_title}》中的核心人物和冲突展开的一段连续故事。",
            genre=["待审核类型"],
            setting="待从原文和目标市场进一步提炼的故事世界。",
            themes=["选择", "成长"],
            characters=[
                CharacterProfile(
                    name="主角",
                    role="protagonist",
                    traits=["待从原文提取"],
                    appearance="待审核",
                    relationships=["待从原文提取"],
                    voice_notes="待设定",
                )
            ],
            locations=[
                LocationProfile(
                    name="主要场景",
                    description="待从原文提取并由人工确认。",
                    visual_keywords=["cinematic", "story setting"],
                )
            ],
            props=[
                PropProfile(
                    name="关键道具",
                    purpose="推动主角追查核心冲突",
                    description="待从原文提取并确认外观、材质和连续性要求。",
                    visual_keywords=["story prop", "cinematic detail"],
                    continuity_notes="在相关镜头中保持形状、颜色和损耗状态一致。",
                )
            ],
            timeline=[f"按照导入文本的章节顺序：共 {len(chapters)} 章。"],
            conflicts=["核心冲突待从原文提取并人工确认。"],
            source_chapter_numbers=chapter_numbers or [0],
        )
        return StoryBibleRecord(
            project_id=project_id,
            source_id=chapters[0].source_id,
            content=content,
            provider="mock",
            model="mock-story-bible-v1",
            duration_ms=max(1, int((monotonic() - started) * 1000)),
        )
