from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.models import EpisodeScriptContent, ShotContent


def valid_episode_script() -> dict:
    return {
        "episode_number": 1,
        "title": "第一集",
        "logline": "主角发现关键线索。",
        "opening_hook": "平静被打破。",
        "ending_hook": "新的敌人出现。",
        "total_duration_seconds": 30,
        "scenes": [
            {
                "scene_index": 1,
                "title": "雨夜",
                "location": "街道",
                "time": "夜晚",
                "characters": ["主角"],
                "duration_seconds": 15,
                "action": "主角发现线索。",
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
            },
            {
                "scene_index": 2,
                "title": "追问",
                "location": "房间",
                "time": "次日",
                "characters": ["主角"],
                "duration_seconds": 15,
                "action": "主角继续追问。",
                "dialogues": [
                    {
                        "line_index": 1,
                        "speaker": "主角",
                        "text": "你知道些什么？",
                        "emotion": "压迫",
                    }
                ],
                "emotion": "紧张",
                "source_chapter_numbers": [1],
            },
        ],
    }


def test_episode_script_schema_requires_sequential_scenes() -> None:
    payload = valid_episode_script()
    payload["scenes"][1]["scene_index"] = 3

    with pytest.raises(ValidationError, match="scene_index"):
        EpisodeScriptContent.model_validate(payload)


def test_episode_script_schema_allows_narration_only_scene() -> None:
    payload = valid_episode_script()
    payload["scenes"][0]["dialogues"] = []

    content = EpisodeScriptContent.model_validate(payload)

    assert content.scenes[0].dialogues == []


def test_episode_script_schema_requires_sequential_dialogues() -> None:
    payload = valid_episode_script()
    payload["scenes"][0]["dialogues"][0]["line_index"] = 2

    with pytest.raises(ValidationError, match="line_index"):
        EpisodeScriptContent.model_validate(payload)


def test_shot_schema_rejects_unsupported_camera_movement() -> None:
    with pytest.raises(ValidationError):
        ShotContent(
            shot_index=1,
            scene_index=1,
            duration_seconds=5,
            shot_size="wide",
            camera_movement="orbit",
            characters=["主角"],
            location="街道",
            visual_prompt="电影感街道夜景",
            audio_requirements=["环境声"],
            asset_requirements=["character:主角"],
        )


def test_shot_schema_allows_pure_prop_insert_without_character() -> None:
    shot = ShotContent(
        shot_index=1,
        scene_index=1,
        duration_seconds=3,
        shot_size="insert",
        camera_movement="fixed",
        characters=[],
        location="房间",
        visual_prompt="桌面上的小瓶特写，冷光从侧面掠过",
        audio_requirements=["环境声"],
        asset_requirements=["prop:小瓶", "location:房间"],
    )

    assert shot.characters == []
