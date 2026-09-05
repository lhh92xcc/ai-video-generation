from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.domain.models import (
    ChapterRecord,
    EpisodeOutlineContent,
    EpisodeRecord,
    EpisodeScriptContent,
    EpisodeScriptRecord,
    EpisodeStatus,
    StoryBibleContent,
    StoryBibleRecord,
)
from app.providers.errors import TextProviderError
from app.providers.openai_compatible_novel import (
    OpenAICompatibleNovelDramaProvider,
    OpenAICompatibleStoryBibleProvider,
)


def story_bible_payload() -> dict:
    return {
        "title": "联调故事",
        "logline": "主角寻找真相。",
        "genre": ["悬疑"],
        "setting": "现代城市",
        "themes": ["成长"],
        "characters": [
            {
                "name": "主角",
                "role": "protagonist",
                "traits": ["坚定"],
                "appearance": "黑发，深色外套",
                "relationships": [],
                "voice_notes": "克制",
            }
        ],
        "locations": [
            {
                "name": "街道",
                "description": "夜晚街道",
                "visual_keywords": ["night street"],
            }
        ],
        "props": [
            {
                "name": "旧信",
                "purpose": "提供线索",
                "description": "泛黄的旧信",
                "visual_keywords": ["old letter"],
                "continuity_notes": "信封边缘有折痕",
            }
        ],
        "timeline": ["第一章"],
        "conflicts": ["真相与阻力"],
        "source_chapter_numbers": [1],
    }


def episode_outline_payload() -> dict:
    return {
        "episode_number": 1,
        "title": "第一集",
        "logline": "主角发现线索。",
        "objective": "找到旧信来源",
        "conflict": "有人阻止调查",
        "turning_point": "发现新的证据",
        "ending_hook": "陌生人出现",
        "source_chapter_numbers": [1],
        "target_duration_seconds": 30,
    }


def episode_script_payload() -> dict:
    return {
        "episode_number": 1,
        "title": "第一集",
        "logline": "主角发现线索。",
        "opening_hook": "平静被打破。",
        "ending_hook": "陌生人出现。",
        "total_duration_seconds": 30,
        "scenes": [
            {
                "scene_index": 1,
                "title": "街道",
                "location": "街道",
                "time": "夜晚",
                "characters": ["主角"],
                "duration_seconds": 30,
                "action": "主角发现旧信。",
                "narration": "旁白交代线索。",
                "dialogues": [
                    {
                        "line_index": 1,
                        "speaker": "主角",
                        "text": "我得查清楚。",
                        "emotion": "坚定",
                    }
                ],
                "emotion": "悬疑",
                "source_chapter_numbers": [1],
            }
        ],
    }


def shot_payload() -> dict:
    return {
        "shots": [
            {
                "shot_index": 1,
                "scene_index": 1,
                "duration_seconds": 30,
                "shot_size": "wide",
                "camera_movement": "fixed",
                "characters": ["主角"],
                "location": "街道",
                "visual_prompt": "电影感夜晚街道，主角发现旧信",
                "dialogue_refs": [1],
                "audio_requirements": ["对白", "环境声"],
                "asset_requirements": ["character:主角", "location:街道", "prop:旧信"],
            }
        ]
    }


def test_openai_compatible_novel_provider_parses_all_content_stages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system_prompt = payload["messages"][0]["content"]
        if "scene-script-generation-v1" in system_prompt:
            result = episode_script_payload()
        elif "episode-planning-v1" in system_prompt:
            result = {"items": [episode_outline_payload()]}
        elif "shot-list-generation-v2" in system_prompt:
            result = shot_payload()
            result["shots"][0]["characters"] = ["character:主角"]
            result["shots"][0]["location"] = "location:街道"
        elif "story-bible-generation-v1" in system_prompt:
            result = story_bible_payload()
        else:
            raise AssertionError(f"unexpected prompt version: {system_prompt[:120]}")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        story_provider = OpenAICompatibleStoryBibleProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="long-context-test",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        drama_provider = OpenAICompatibleNovelDramaProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="long-context-test",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        source_id = uuid4()
        chapters = [
            ChapterRecord(
                source_id=source_id,
                chapter_number=1,
                title="第一章",
                content="主角发现旧信。",
                start_offset=0,
                end_offset=10,
            )
        ]
        story = await story_provider.generate(uuid4(), "联调故事", chapters)
        assert story.content.props[0].name == "旧信"
        episodes = await drama_provider.plan_episodes(uuid4(), story, 1, 30)
        assert episodes[0].outline.title == "第一集"
        script = await drama_provider.generate_episode_script(episodes[0], story)
        assert script.content.scenes[0].dialogues[0].speaker == "主角"
        shots = await drama_provider.generate_shot_list(episodes[0], script, story)
        assert shots.shots[0].asset_requirements[-1] == "prop:旧信"
        assert shots.shots[0].characters == ["主角"]
        assert shots.shots[0].location == "街道"
        await story_provider.close()
        await drama_provider.close()
        await client.aclose()

    asyncio.run(exercise())


def test_openai_compatible_story_provider_requires_api_key() -> None:
    async def exercise() -> None:
        provider = OpenAICompatibleStoryBibleProvider(
            base_url="https://example.test",
            api_key=None,
            model="test-model",
            temperature=0.2,
            timeout_seconds=5,
        )
        with pytest.raises(TextProviderError) as error:
            await provider.generate(
                uuid4(),
                "无 Key",
                [
                    ChapterRecord(
                        source_id=uuid4(),
                        chapter_number=1,
                        title="第一章",
                        content="内容",
                        start_offset=0,
                        end_offset=2,
                    )
                ],
            )
        await provider.close()
        assert error.value.code == "PROVIDER_AUTH_FAILED"

    asyncio.run(exercise())


def test_openai_compatible_novel_provider_repairs_invalid_episode_script() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        system_prompt = payload["messages"][0]["content"]
        if "scene-script-generation-repair-v1" in system_prompt:
            repair_payload = json.loads(payload["messages"][1]["content"])
            assert repair_payload["validation_error"]
            assert repair_payload["invalid_output"]["scenes"][0]["action"] == ""
            result = episode_script_payload()
        else:
            result = episode_script_payload()
            result["scenes"][0]["action"] = ""
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleNovelDramaProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="long-context-test",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        project_id = uuid4()
        story = StoryBibleRecord(
            project_id=project_id,
            source_id=uuid4(),
            content=StoryBibleContent.model_validate(story_bible_payload()),
            provider="mock",
            model="story-test",
            duration_ms=1,
        )
        episode = EpisodeRecord(
            project_id=project_id,
            story_bible_id=story.id,
            episode_number=1,
            status=EpisodeStatus.PLANNED,
            outline=EpisodeOutlineContent.model_validate(episode_outline_payload()),
            provider="mock",
            model="episode-test",
            duration_ms=1,
        )
        script = await provider.generate_episode_script(episode, story)
        await provider.close()
        await client.aclose()

        assert calls == 2
        assert script.content.scenes[0].action == "主角发现旧信。"

    asyncio.run(exercise())


def test_openai_compatible_novel_provider_repairs_invalid_shot_list() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        system_prompt = payload["messages"][0]["content"]
        if "shot-list-generation-repair-v1" in system_prompt:
            repair_payload = json.loads(payload["messages"][1]["content"])
            assert repair_payload["validation_error"]
            assert repair_payload["invalid_output"]["shots"][0]["shot_size"] == ""
            assert repair_payload["invalid_output"]["shots"][0]["asset_requirements"] == ["主角"]
            result = shot_payload()
        else:
            result = shot_payload()
            result["shots"][0]["shot_size"] = ""
            result["shots"][0]["asset_requirements"] = ["主角"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleNovelDramaProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="long-context-test",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        project_id = uuid4()
        story = StoryBibleRecord(
            project_id=project_id,
            source_id=uuid4(),
            content=StoryBibleContent.model_validate(story_bible_payload()),
            provider="mock",
            model="story-test",
            duration_ms=1,
        )
        episode = EpisodeRecord(
            project_id=project_id,
            story_bible_id=story.id,
            episode_number=1,
            status=EpisodeStatus.SCRIPTED,
            outline=EpisodeOutlineContent.model_validate(episode_outline_payload()),
            provider="mock",
            model="episode-test",
            duration_ms=1,
        )
        script = EpisodeScriptRecord(
            project_id=project_id,
            episode_id=episode.id,
            content=EpisodeScriptContent.model_validate(episode_script_payload()),
            provider="mock",
            model="script-test",
            duration_ms=1,
        )

        shots = await provider.generate_shot_list(episode, script, story)
        await provider.close()
        await client.aclose()

        assert calls == 2
        assert shots.shots[0].shot_size == "wide"

    asyncio.run(exercise())
