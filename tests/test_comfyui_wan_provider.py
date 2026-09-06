from __future__ import annotations

from uuid import uuid4

from app.domain.models import VideoClipGenerationRequest
from app.providers.comfyui_wan_i2v import ComfyUIWanI2VVideoGenerationProvider


def test_comfyui_wan_retry_attempt_changes_seed() -> None:
    base = VideoClipGenerationRequest(
        episode_id=uuid4(),
        shot_list_id=uuid4(),
        shot_index=1,
        duration_seconds=3,
        prompt="角色回头",
        negative_prompt="变脸",
        keyframe_bytes=b"reference",
        keyframe_mime_type="image/png",
        generation_attempt=1,
    )
    retry = base.model_copy(update={"generation_attempt": 2})

    assert ComfyUIWanI2VVideoGenerationProvider._seed_for(base) != (
        ComfyUIWanI2VVideoGenerationProvider._seed_for(retry)
    )


def test_comfyui_wan_replaces_quality_parameters_as_numbers() -> None:
    workflow = {
        "1": {
            "class_type": "WanVideoTextEncode",
            "inputs": {
                "positive_prompt": "__AI_VIDEO_PROMPT__",
                "image": "__AI_VIDEO_INPUT_IMAGE__",
                "steps": "__AI_VIDEO_STEPS__",
                "cfg": "__AI_VIDEO_CFG__",
                "noise": "__AI_VIDEO_NOISE_AUG_STRENGTH__",
            },
        }
    }
    rendered = ComfyUIWanI2VVideoGenerationProvider._replace_placeholders(
        workflow,
        {
            "__AI_VIDEO_PROMPT__": "one continuous shot",
            "__AI_VIDEO_INPUT_IMAGE__": "keyframe.png",
            "__AI_VIDEO_STEPS__": 6,
            "__AI_VIDEO_CFG__": 5.0,
            "__AI_VIDEO_NOISE_AUG_STRENGTH__": 0.02,
        },
    )
    inputs = rendered["1"]["inputs"]
    assert inputs["steps"] == 6
    assert inputs["cfg"] == 5.0
    assert inputs["noise"] == 0.02
