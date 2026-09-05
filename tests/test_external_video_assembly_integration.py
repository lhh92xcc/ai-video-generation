from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

from app.domain.models import (
    AssetStatus,
    AssetType,
    EpisodeOutlineContent,
    EpisodeRecord,
    ShotAssetReference,
    ShotContent,
    ShotListRecord,
    VideoAssemblyCreateRequest,
    VideoClipCreateRequest,
)
from app.media.video_validation import FFprobeVideoValidator
from app.providers.openai_compatible_video import OpenAICompatibleVideoGenerationProvider
from app.providers.siliconflow_video import SiliconFlowVideoGenerationProvider
from app.queue import InProcessTaskQueue
from app.rendering.ffmpeg_renderer import FFmpegVideoRenderer
from app.repositories.in_memory import InMemoryStore
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.video_clip_service import VideoClipTaskService
from app.storage.s3_compatible import S3CompatibleArtifactStorage


@pytest.mark.external_integration
def test_external_video_clips_assemble_into_sample_on_minio() -> None:
    if os.getenv("AI_VIDEO_RUN_EXTERNAL_VIDEO_INTEGRATION") != "1":
        pytest.skip(
            "set AI_VIDEO_RUN_EXTERNAL_VIDEO_INTEGRATION=1 to call the configured video API"
        )

    required = {
        "AI_VIDEO_VIDEO_API_KEY": os.getenv("AI_VIDEO_VIDEO_API_KEY"),
        "AI_VIDEO_VIDEO_BASE_URL": os.getenv("AI_VIDEO_VIDEO_BASE_URL"),
        "AI_VIDEO_VIDEO_MODEL": os.getenv("AI_VIDEO_VIDEO_MODEL"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"external video assembly requires: {', '.join(missing)}")

    clip_count = int(os.getenv("AI_VIDEO_VIDEO_ASSEMBLY_CLIP_COUNT", "2"))
    shot_duration_seconds = int(
        os.getenv("AI_VIDEO_VIDEO_ASSEMBLY_SHOT_DURATION_SECONDS", "30")
    )
    requested_duration_seconds = clip_count * shot_duration_seconds
    if not 2 <= clip_count <= 12:
        pytest.fail("AI_VIDEO_VIDEO_ASSEMBLY_CLIP_COUNT must be between 2 and 12")
    if not 1 <= shot_duration_seconds <= 120:
        pytest.fail(
            "AI_VIDEO_VIDEO_ASSEMBLY_SHOT_DURATION_SECONDS must be between 1 and 120"
        )
    if not 60 <= requested_duration_seconds <= 180:
        pytest.fail(
            "assembly smoke requested duration must be between 60 and 180 seconds"
        )

    endpoint = os.getenv("AI_VIDEO_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    bucket = os.getenv("AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
    access_key = os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY", "ai-video-dev")
    secret_key = os.getenv("AI_VIDEO_STORAGE_SECRET_KEY", "ai-video-dev-password")
    provider_name = os.getenv("AI_VIDEO_VIDEO_PROVIDER", "openai_compatible")

    async def exercise() -> None:
        timeout_seconds = int(os.getenv("AI_VIDEO_VIDEO_TIMEOUT_SECONDS", "300"))
        probe_timeout_seconds = int(
            os.getenv("AI_VIDEO_VIDEO_PROBE_TIMEOUT_SECONDS", "30")
        )
        provider_client = httpx.AsyncClient(timeout=timeout_seconds)
        if provider_name == "siliconflow":
            provider = SiliconFlowVideoGenerationProvider(
                base_url=str(required["AI_VIDEO_VIDEO_BASE_URL"]),
                api_key=str(required["AI_VIDEO_VIDEO_API_KEY"]),
                model=str(required["AI_VIDEO_VIDEO_MODEL"]),
                image_size=os.getenv("AI_VIDEO_VIDEO_IMAGE_SIZE", "720x1280"),
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=float(
                    os.getenv("AI_VIDEO_VIDEO_POLL_INTERVAL_SECONDS", "2")
                ),
                max_poll_seconds=int(os.getenv("AI_VIDEO_VIDEO_MAX_POLL_SECONDS", "900")),
                max_download_bytes=int(
                    os.getenv("AI_VIDEO_VIDEO_MAX_DOWNLOAD_BYTES", "524288000")
                ),
                client=provider_client,
            )
        elif provider_name == "openai_compatible":
            provider = OpenAICompatibleVideoGenerationProvider(
                base_url=str(required["AI_VIDEO_VIDEO_BASE_URL"]),
                api_key=str(required["AI_VIDEO_VIDEO_API_KEY"]),
                model=str(required["AI_VIDEO_VIDEO_MODEL"]),
                timeout_seconds=timeout_seconds,
                create_path=os.getenv(
                    "AI_VIDEO_VIDEO_CREATE_PATH", "/videos/generations"
                ),
                status_path_template=os.getenv(
                    "AI_VIDEO_VIDEO_STATUS_PATH_TEMPLATE",
                    "/videos/generations/{task_id}",
                ),
                poll_interval_seconds=float(
                    os.getenv("AI_VIDEO_VIDEO_POLL_INTERVAL_SECONDS", "2")
                ),
                max_poll_seconds=int(os.getenv("AI_VIDEO_VIDEO_MAX_POLL_SECONDS", "900")),
                max_download_bytes=int(
                    os.getenv("AI_VIDEO_VIDEO_MAX_DOWNLOAD_BYTES", "524288000")
                ),
                client=provider_client,
            )
        else:
            raise AssertionError(
                "AI_VIDEO_VIDEO_PROVIDER must be siliconflow or openai_compatible "
                f"for external integration, got {provider_name!r}"
            )
        storage = S3CompatibleArtifactStorage(
            endpoint=endpoint,
            bucket=bucket,
            region=os.getenv("AI_VIDEO_STORAGE_REGION", "us-east-1"),
            access_key=access_key,
            secret_key=secret_key,
            timeout_seconds=int(os.getenv("AI_VIDEO_STORAGE_TIMEOUT_SECONDS", "120")),
            signed_url_expire_seconds=300,
        )
        store = InMemoryStore()
        clip_queue = InProcessTaskQueue()
        assembly_queue = InProcessTaskQueue()
        video_validator = FFprobeVideoValidator(timeout_seconds=probe_timeout_seconds)
        clip_service = VideoClipTaskService(
            store,
            clip_queue,
            provider,
            storage,
            video_validator=video_validator,
        )
        assembly_service = VideoAssemblyTaskService(
            store,
            assembly_queue,
            storage,
            renderer=FFmpegVideoRenderer(
                timeout_seconds=timeout_seconds,
                video_validator=FFprobeVideoValidator(
                    timeout_seconds=probe_timeout_seconds
                ),
            ),
        )
        clip_queue.set_handler(clip_service.run_task)
        assembly_queue.set_handler(assembly_service.run_task)
        cleanup_keys: list[str] = []
        try:
            episode, shot_list = _sample_episode_and_shot_list(
                clip_count,
                shot_duration_seconds,
            )
            await store.save_episodes([episode])
            await store.save_shot_list(shot_list)

            clip_task_ids: list[UUID] = []
            for shot_index in range(1, clip_count + 1):
                task, reused = await clip_service.create_task(
                    episode.id,
                    shot_index,
                    VideoClipCreateRequest(
                        negative_prompt=os.getenv(
                            "AI_VIDEO_VIDEO_TEST_NEGATIVE_PROMPT",
                            "blurry, flicker, distorted anatomy, text, watermark",
                        )
                    ),
                    idempotency_key=f"external-video-assembly-{uuid4()}",
                )
                await clip_queue.close()

                assert reused is False
                saved_clip = await store.get_task(task.id)
                assert saved_clip is not None
                assert saved_clip.status.value == "succeeded", saved_clip.error
                assert len(saved_clip.artifacts) == 1
                clip_artifact = saved_clip.artifacts[0]
                assert clip_artifact.type == "video_clip"
                assert clip_artifact.provider == provider_name
                clip_key = str(clip_artifact.metadata["storage_key"])
                cleanup_keys.append(clip_key)
                clip_task_ids.append(task.id)
                clip_content_type = str(clip_artifact.metadata["content_type"])
                clip_size = int(clip_artifact.metadata["size_bytes"])
                clip_probe = clip_artifact.metadata["ffprobe"]
                assert clip_content_type.startswith("video/")
                assert clip_size > 0
                assert isinstance(clip_probe, dict)
                assert float(clip_probe["duration_seconds"]) > 0
                assert int(clip_probe["width"]) > 0
                assert int(clip_probe["height"]) > 0

            assembly_task, reused = await assembly_service.create_task(
                episode.id,
                VideoAssemblyCreateRequest(clip_task_ids=clip_task_ids),
                idempotency_key=f"external-video-assembly-render-{uuid4()}",
            )
            await assembly_queue.close()

            assert reused is False
            completed = await store.get_task(assembly_task.id)
            assert completed is not None
            assert completed.status.value == "succeeded", completed.error
            assert len(completed.artifacts) == 1
            rendered = completed.artifacts[0]
            assert rendered.type == "rendered_video"
            assert rendered.provider == "ffmpeg"
            assert rendered.metadata["input_clip_count"] == clip_count
            output_key = str(rendered.metadata["storage_key"])
            cleanup_keys.append(output_key)
            output_content_type = str(rendered.metadata["content_type"])
            output_size = int(rendered.metadata["size_bytes"])
            output_sha256 = str(rendered.metadata["sha256"])
            output_probe = rendered.metadata["ffprobe"]
            assert output_content_type == "video/mp4"
            assert output_size > 0
            assert isinstance(output_probe, dict)
            assert float(output_probe["duration_seconds"]) > 0
            assert int(output_probe["width"]) > 0
            assert int(output_probe["height"]) > 0
            assert int(output_probe["video_stream_count"]) >= 1

            output = await storage.get_bytes(output_key)
            assert len(output) == output_size
            assert hashlib.sha256(output).hexdigest() == output_sha256
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(storage.create_download_url(output_key))
            assert response.status_code == 200
            assert response.content == output
        finally:
            await clip_queue.close()
            await assembly_queue.close()
            await provider.close()
            await provider_client.aclose()
            await storage.close()
            if (
                cleanup_keys
                and os.getenv("AI_VIDEO_MINIO_COMPOSE_CLEANUP", "1") == "1"
                and urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
            ):
                for storage_key in cleanup_keys:
                    subprocess.run(
                        [
                            "docker",
                            "compose",
                            "exec",
                            "-T",
                            "minio",
                            "mc",
                            "rm",
                            "--quiet",
                            f"local/{bucket}/{storage_key}",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )

    asyncio.run(exercise())


def _sample_episode_and_shot_list(
    clip_count: int,
    shot_duration_seconds: int,
) -> tuple[EpisodeRecord, ShotListRecord]:
    project_id = uuid4()
    asset_key = uuid4()
    total_duration_seconds = clip_count * shot_duration_seconds
    episode = EpisodeRecord(
        project_id=project_id,
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="真实视频多镜头样片",
            logline="主角在雨夜追踪一条线索",
            objective="验证多镜头真实视频拼接链路",
            conflict="线索正在被对手销毁",
            turning_point="主角找到关键物证",
            ending_hook="远处出现跟踪者",
            source_chapter_numbers=[1],
            target_duration_seconds=total_duration_seconds,
        ),
        provider="external-video-assembly-integration",
        model="integration-fixture",
        duration_ms=0,
    )
    prompts = [
        "主角在雨夜霓虹街道缓慢前行，电影感光影，镜头跟随",
        "主角在废弃巷口发现一封旧信，镜头从远景推进到中景",
        "主角抬头看向远处的跟踪者，雨幕和霓虹灯形成悬疑氛围",
    ]
    shots = [
        ShotContent(
            shot_index=shot_index,
            scene_index=1,
            duration_seconds=shot_duration_seconds,
            shot_size="medium",
            camera_movement="tracking" if shot_index % 2 else "dolly",
            characters=["主角"],
            location="雨夜霓虹街道",
            visual_prompt=prompts[(shot_index - 1) % len(prompts)],
            dialogue_refs=[],
            audio_requirements=["雨声"],
            asset_requirements=["主角"],
            asset_refs=[
                ShotAssetReference(
                    asset_key=asset_key,
                    asset_type=AssetType.CHARACTER,
                    name="主角",
                    version=1,
                    status=AssetStatus.READY,
                    match_kind="name",
                    matched_text="主角",
                )
            ],
        )
        for shot_index in range(1, clip_count + 1)
    ]
    return episode, ShotListRecord(
        project_id=project_id,
        episode_id=episode.id,
        script_id=uuid4(),
        shots=shots,
        provider="external-video-assembly-integration",
        model="integration-fixture",
        duration_ms=0,
    )
