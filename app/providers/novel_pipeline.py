"""Provider boundary for the novel-to-drama content pipeline.

The mock implementation intentionally produces reviewable, deterministic JSON.
It keeps the service layer independent from a specific long-context model so a
real provider can be added without changing the API or persistence contracts.
"""

from __future__ import annotations

from time import monotonic
from uuid import UUID

from app.domain.models import (
    DialogueLine,
    EpisodeOutlineContent,
    EpisodeRecord,
    EpisodeScriptContent,
    EpisodeScriptRecord,
    EpisodeStatus,
    SceneScriptContent,
    ShotContent,
    ShotListRecord,
    StoryBibleRecord,
)


class NovelDramaGenerationProvider:
    async def plan_episodes(
        self,
        project_id: UUID,
        story_bible: StoryBibleRecord,
        target_episode_count: int,
        target_duration_seconds: int,
    ) -> list[EpisodeRecord]:
        raise NotImplementedError

    async def generate_episode_script(
        self,
        episode: EpisodeRecord,
        story_bible: StoryBibleRecord,
    ) -> EpisodeScriptRecord:
        raise NotImplementedError

    async def generate_shot_list(
        self,
        episode: EpisodeRecord,
        script: EpisodeScriptRecord,
        story_bible: StoryBibleRecord,
    ) -> ShotListRecord:
        raise NotImplementedError


def _partition_chapters(chapter_numbers: list[int], episode_count: int) -> list[list[int]]:
    """Distribute source chapters deterministically, including tiny novels."""

    if not chapter_numbers:
        return [[0] for _ in range(episode_count)]

    result: list[list[int]] = []
    chapter_count = len(chapter_numbers)
    for index in range(episode_count):
        start = index * chapter_count // episode_count
        end = (index + 1) * chapter_count // episode_count
        if end <= start:
            result.append([chapter_numbers[index % chapter_count]])
        else:
            result.append(chapter_numbers[start:end])
    return result


class MockNovelDramaProvider(NovelDramaGenerationProvider):
    """Generate a stable baseline for API, persistence and UI development."""

    async def plan_episodes(
        self,
        project_id: UUID,
        story_bible: StoryBibleRecord,
        target_episode_count: int,
        target_duration_seconds: int,
    ) -> list[EpisodeRecord]:
        started = monotonic()
        chapter_groups = _partition_chapters(
            story_bible.content.source_chapter_numbers,
            target_episode_count,
        )
        elapsed_ms = max(1, int((monotonic() - started) * 1000))
        episodes: list[EpisodeRecord] = []
        for episode_number, source_chapters in enumerate(chapter_groups, start=1):
            outline = EpisodeOutlineContent(
                episode_number=episode_number,
                title=f"{story_bible.content.title}·第{episode_number}集",
                logline=(
                    f"第{episode_number}集围绕主角在第 {source_chapters[0]} 章附近面对的新问题展开。"
                ),
                objective="让主角明确本集目标，并推动核心冲突进入下一阶段。",
                conflict=story_bible.content.conflicts[0],
                turning_point="主角获得关键线索，但必须付出新的代价。",
                ending_hook="关键人物在结尾出现，留下下一集必须解决的问题。",
                source_chapter_numbers=source_chapters,
                target_duration_seconds=target_duration_seconds,
            )
            episodes.append(
                EpisodeRecord(
                    project_id=project_id,
                    story_bible_id=story_bible.id,
                    episode_number=episode_number,
                    status=EpisodeStatus.PLANNED,
                    outline=outline,
                    provider="mock",
                    model="mock-novel-drama-v1",
                    duration_ms=elapsed_ms,
                )
            )
        return episodes

    async def generate_episode_script(
        self,
        episode: EpisodeRecord,
        story_bible: StoryBibleRecord,
    ) -> EpisodeScriptRecord:
        started = monotonic()
        target_duration = episode.outline.target_duration_seconds
        scene_count = 3
        base_duration, remainder = divmod(target_duration, scene_count)
        characters = [character.name for character in story_bible.content.characters[:2]] or ["主角"]
        locations = [location.name for location in story_bible.content.locations[:1]] or ["主要场景"]
        scenes: list[SceneScriptContent] = []
        for scene_index in range(1, scene_count + 1):
            duration = base_duration + (1 if scene_index <= remainder else 0)
            scenes.append(
                SceneScriptContent(
                    scene_index=scene_index,
                    title=f"场景 {scene_index}：线索推进",
                    location=locations[0],
                    time="夜晚" if scene_index == 1 else "次日",
                    characters=characters,
                    duration_seconds=duration,
                    action=(
                        f"主角在{locations[0]}确认线索，面对第{episode.episode_number}集的核心阻力，"
                        "并做出推动剧情的选择。"
                    ),
                    narration="旁白交代当前局势，并把原文冲突转译成可拍摄的行动。",
                    dialogues=[
                        DialogueLine(
                            line_index=1,
                            speaker=characters[0],
                            text="这一次，我必须先找到真相。",
                            emotion="克制但坚定",
                        ),
                        DialogueLine(
                            line_index=2,
                            speaker=characters[-1],
                            text="你确定要继续查下去吗？",
                            emotion="担忧",
                        ),
                    ],
                    emotion="悬疑、压迫、逐步升级",
                    source_chapter_numbers=episode.outline.source_chapter_numbers,
                )
            )

        content = EpisodeScriptContent(
            episode_number=episode.episode_number,
            title=episode.outline.title,
            logline=episode.outline.logline,
            opening_hook="一个看似普通的细节打破平静。",
            ending_hook=episode.outline.ending_hook,
            total_duration_seconds=target_duration,
            scenes=scenes,
            risk_notes=["Mock 输出必须经过人工审核后才能进入资产和视频生成。"],
        )
        return EpisodeScriptRecord(
            project_id=episode.project_id,
            episode_id=episode.id,
            content=content,
            provider="mock",
            model="mock-novel-drama-v1",
            duration_ms=max(1, int((monotonic() - started) * 1000)),
        )

    async def generate_shot_list(
        self,
        episode: EpisodeRecord,
        script: EpisodeScriptRecord,
        story_bible: StoryBibleRecord,
    ) -> ShotListRecord:
        started = monotonic()
        shots: list[ShotContent] = []
        shot_index = 1
        for scene in script.content.scenes:
            dialogue_line_indexes = [dialogue.line_index for dialogue in scene.dialogues]
            durations = (
                [scene.duration_seconds]
                if scene.duration_seconds == 1
                else [scene.duration_seconds // 2, scene.duration_seconds - scene.duration_seconds // 2]
            )
            for shot_number, duration in enumerate(durations, start=1):
                if not dialogue_line_indexes:
                    dialogue_refs: list[int] = []
                elif shot_number == 1:
                    dialogue_refs = [dialogue_line_indexes[0]]
                else:
                    dialogue_refs = [dialogue_line_indexes[min(1, len(dialogue_line_indexes) - 1)]]
                shots.append(
                    ShotContent(
                        shot_index=shot_index,
                        scene_index=scene.scene_index,
                        duration_seconds=duration,
                        shot_size="wide" if shot_number == 1 else "close_up",
                        camera_movement="dolly" if shot_number == 1 else "fixed",
                        characters=scene.characters,
                        location=scene.location,
                        visual_prompt=(
                            f"电影感短剧画面，{scene.location}，{scene.emotion}，"
                            f"{scene.action}，连续角色外观，竖屏 9:16，高细节"
                        ),
                        dialogue_refs=dialogue_refs,
                        audio_requirements=(
                            (["角色对白"] if dialogue_line_indexes else [])
                            + ["环境氛围声"]
                        ),
                        asset_requirements=[
                            f"character:{character}"
                            for character in scene.characters
                        ]
                        + [f"location:{scene.location}"],
                        continuity_notes="保持角色服装、发型和光线方向与相邻镜头一致。",
                    )
                )
                shot_index += 1

        return ShotListRecord(
            project_id=episode.project_id,
            episode_id=episode.id,
            script_id=script.id,
            shots=shots,
            provider="mock",
            model="mock-novel-drama-v1",
            duration_ms=max(1, int((monotonic() - started) * 1000)),
        )
