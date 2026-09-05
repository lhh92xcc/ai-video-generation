from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from urllib.parse import urlsplit
from uuid import uuid4

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
    VideoClipCreateRequest,
)
from app.media.video_validation import FFprobeVideoValidator
from app.providers.openai_compatible_video import OpenAICompatibleVideoGenerationProvider
from app.providers.siliconflow_video import SiliconFlowVideoGenerationProvider
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.video_clip_service import VideoClipTaskService
from app.storage.s3_compatible import S3CompatibleArtifactStorage


@pytest.mark.external_integration
def test_external_video_provider_writes_valid_clip_to_minio() -> None:
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
        pytest.fail(f"external video integration requires: {', '.join(missing)}")

    duration_seconds = int(os.getenv("AI_VIDEO_VIDEO_TEST_DURATION_SECONDS", "5"))
    if not 1 <= duration_seconds <= 120:
        pytest.fail("AI_VIDEO_VIDEO_TEST_DURATION_SECONDS must be between 1 and 120")

    endpoint = os.getenv("AI_VIDEO_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    bucket = os.getenv("AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
    access_key = os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY", "ai-video-dev")
    secret_key = os.getenv("AI_VIDEO_STORAGE_SECRET_KEY", "ai-video-dev-password")
    provider_name = os.getenv("AI_VIDEO_VIDEO_PROVIDER", "openai_compatible")

    async def exercise() -> None:
        timeout_seconds = int(os.getenv("AI_VIDEO_VIDEO_TIMEOUT_SECONDS", "300"))
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
        queue = InProcessTaskQueue()
        store = InMemoryStore()
        service = VideoClipTaskService(
            store,
            queue,
            provider,
            storage,
            video_validator=FFprobeVideoValidator(
                timeout_seconds=int(
                    os.getenv("AI_VIDEO_VIDEO_PROBE_TIMEOUT_SECONDS", "30")
                )
            ),
        )
        queue.set_handler(service.run_task)
        storage_key: str | None = None
        try:
            episode, shot_list = _sample_episode_and_shot(duration_seconds)
            await store.save_episodes([episode])
            await store.save_shot_list(shot_list)

            task, reused = await service.create_task(
                episode.id,
                1,
                VideoClipCreateRequest(
                    prompt_override=os.getenv(
                        "AI_VIDEO_VIDEO_TEST_PROMPT",
                        "A cinematic short film shot of a lone character walking through a rainy neon street at night",
                    ),
                    negative_prompt=os.getenv(
                        "AI_VIDEO_VIDEO_TEST_NEGATIVE_PROMPT",
                        "blurry, flicker, distorted anatomy, text, watermark",
                    ),
                ),
                idempotency_key=f"external-video-smoke-{uuid4()}",
            )
            await queue.close()

            assert reused is False
            saved_task = await store.get_task(task.id)
            assert saved_task is not None
            assert saved_task.status.value == "succeeded", saved_task.error
            assert len(saved_task.artifacts) == 1

            artifact = saved_task.artifacts[0]
            assert artifact.type == "video_clip"
            storage_key = str(artifact.metadata["storage_key"])
            content_type = str(artifact.metadata["content_type"])
            size_bytes = int(artifact.metadata["size_bytes"])
            expected_sha256 = str(artifact.metadata["sha256"])
            ffprobe = artifact.metadata["ffprobe"]
            assert artifact.provider == provider_name
            assert content_type.startswith("video/")
            assert size_bytes > 0
            assert isinstance(ffprobe, dict)
            assert float(ffprobe["duration_seconds"]) > 0
            assert int(ffprobe["width"]) > 0
            assert int(ffprobe["height"]) > 0
            assert int(ffprobe["video_stream_count"]) >= 1

            downloaded = await storage.get_bytes(storage_key)
            assert len(downloaded) == size_bytes
            assert hashlib.sha256(downloaded).hexdigest() == expected_sha256

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(storage.create_download_url(storage_key))
            assert response.status_code == 200
            assert response.content == downloaded
        finally:
            await queue.close()
            await provider.close()
            await provider_client.aclose()
            await storage.close()
            if (
                storage_key
                and os.getenv("AI_VIDEO_MINIO_COMPOSE_CLEANUP", "1") == "1"
                and urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
            ):
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


def _sample_episode_and_shot(duration_seconds: int) -> tuple[EpisodeRecord, ShotListRecord]:
    project_id = uuid4()
    episode = EpisodeRecord(
        project_id=project_id,
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="真实视频 Provider 样片",
            logline="主角在雨夜发现一条线索",
            objective="验证单镜头视频生成链路",
            conflict="时间紧迫且线索即将消失",
            turning_point="主角在霓虹灯下找到关键物证",
            ending_hook="远处有人跟随",
            source_chapter_numbers=[1],
            target_duration_seconds=max(30, duration_seconds),
        ),
        provider="external-video-integration",
        model="integration-fixture",
        duration_ms=0,
    )
    shot_list = ShotListRecord(
        project_id=project_id,
        episode_id=episode.id,
        script_id=uuid4(),
        shots=[
            ShotContent(
                shot_index=1,
                scene_index=1,
                duration_seconds=duration_seconds,
                shot_size="medium",
                camera_movement="tracking",
                characters=["主角"],
                location="雨夜霓虹街道",
                visual_prompt="主角在雨夜霓虹街道缓慢前行，电影感光影",
                dialogue_refs=[],
                audio_requirements=["雨声"],
                asset_requirements=["主角"],
                asset_refs=[
                    ShotAssetReference(
                        asset_key=uuid4(),
                        asset_type=AssetType.CHARACTER,
                        name="主角",
                        version=1,
                        status=AssetStatus.READY,
                        match_kind="name",
                        matched_text="主角",
                    )
                ],
            )
        ],
        provider="external-video-integration",
        model="integration-fixture",
        duration_ms=0,
    )
    return episode, shot_list
