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
