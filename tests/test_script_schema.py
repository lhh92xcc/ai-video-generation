from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.domain.models import ScriptContent, ScriptGenerationRequest, SceneContent
from app.providers.mock_text import MockTextProvider


def valid_script() -> dict:
    return {
        "title": "测试脚本",
        "hook": "先看一个关键问题",
        "summary": "用三个分镜讲清楚一个主题。",
        "duration_seconds": 15,
        "scenes": [
            {
                "scene_index": 1,
                "duration_seconds": 5,
                "voiceover": "第一段旁白",
                "caption": "第一段字幕",
                "visual_keywords": ["opening shot"],
                "transition": "cut",
                "risk_notes": [],
            },
            {
                "scene_index": 2,
                "duration_seconds": 5,
                "voiceover": "第二段旁白",
                "caption": "第二段字幕",
                "visual_keywords": ["detail shot"],
                "transition": "cut",
                "risk_notes": [],
            },
            {
                "scene_index": 3,
                "duration_seconds": 5,
                "voiceover": "第三段旁白",
                "caption": "第三段字幕",
                "visual_keywords": ["summary"],
                "transition": "fade",
                "risk_notes": [],
            },
        ],
        "cta": "收藏后再看",
        "risk_notes": [],
    }


def test_script_schema_accepts_valid_content() -> None:
    script = ScriptContent.model_validate(valid_script())

    assert script.scenes[0].scene_index == 1
    assert sum(scene.duration_seconds for scene in script.scenes) == 15


def test_script_schema_rejects_non_sequential_scenes() -> None:
    payload = valid_script()
    payload["scenes"][1]["scene_index"] = 3

    with pytest.raises(ValidationError, match="scene_index"):
        ScriptContent.model_validate(payload)


def test_script_schema_rejects_mismatched_timeline() -> None:
    payload = valid_script()
    payload["duration_seconds"] = 60

    with pytest.raises(ValidationError, match="scene durations"):
        ScriptContent.model_validate(payload)


def test_mock_provider_returns_validated_script() -> None:
    async def exercise() -> None:
        result = await MockTextProvider().generate(
            ScriptGenerationRequest(
                topic="Mock Schema",
                language="zh-CN",
                target_duration_seconds=15,
                aspect_ratio="9:16",
                tone="清晰",
            )
        )
        assert isinstance(result.content, ScriptContent)
        assert result.content.duration_seconds == 15

    asyncio.run(exercise())


def test_scene_transition_is_limited_to_supported_values() -> None:
    with pytest.raises(ValidationError):
        SceneContent(
            scene_index=1,
            duration_seconds=5,
            voiceover="旁白",
            caption="字幕",
            visual_keywords=["shot"],
            transition="slide",
        )
