from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

from app.domain.models import (
    ArtifactSummary,
    EpisodeOutlineContent,
    EpisodeRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    SubtitleASRCreateRequest,
    TaskStatus,
)
from app.media.audio_validation import FFprobeAudioValidator
from app.media.subtitle_quality import evaluate_subtitle_cues
from app.providers.aliyun_dashscope_asr import AliyunDashScopeASRProvider
from app.providers.openai_compatible_asr import OpenAICompatibleASRProvider
from app.providers.siliconflow_asr import SiliconFlowASRProvider
from app.queue import InProcessTaskQueue
from app.rendering.subtitles import parse_srt
from app.repositories.in_memory import InMemoryStore
from app.services.subtitle_service import SubtitleTaskService
from app.storage.s3_compatible import S3CompatibleArtifactStorage


@pytest.mark.external_integration
def test_external_asr_task_reads_audio_artifact_and_writes_srt_to_minio() -> None:
    """Opt-in end-to-end smoke for the real ASR task boundary.

    The test covers the same boundaries used by the Worker: a source
    ``audio_narration`` Artifact is stored in S3-compatible storage, the ASR
    task is queued and executed, and the resulting ``subtitle_srt`` Artifact
    is read back and checked. It is skipped by default because it calls a real
    external ASR service and may incur provider cost.
    """

    if os.getenv("AI_VIDEO_RUN_EXTERNAL_ASR_TASK_INTEGRATION") != "1":
        pytest.skip(
            "set AI_VIDEO_RUN_EXTERNAL_ASR_TASK_INTEGRATION=1 to run the real ASR task smoke"
        )

    provider_name = os.getenv("AI_VIDEO_ASR_PROVIDER", "openai_compatible")
    if provider_name not in {"aliyun_dashscope", "siliconflow", "openai_compatible"}:
        pytest.fail(
            "AI_VIDEO_ASR_PROVIDER must be aliyun_dashscope, siliconflow or "
            "openai_compatible for "
            f"external task integration, got {provider_name!r}"
        )

    api_key = os.getenv("AI_VIDEO_ASR_API_KEY") or os.getenv("AI_VIDEO_LLM_API_KEY")
    required = {
        "AI_VIDEO_ASR_API_KEY or AI_VIDEO_LLM_API_KEY": api_key,
        "AI_VIDEO_ASR_MODEL": os.getenv("AI_VIDEO_ASR_MODEL"),
    }
    if provider_name != "aliyun_dashscope":
        required["AI_VIDEO_ASR_BASE_URL"] = os.getenv("AI_VIDEO_ASR_BASE_URL")
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"external ASR task integration requires: {', '.join(missing)}")

    default_audio_path = Path(".tmp/asr-smoke/real-zh-smoke.mp3")
    audio_path = Path(
        os.getenv("AI_VIDEO_ASR_TEST_AUDIO_PATH", str(default_audio_path))
    ).expanduser()
    if not audio_path.is_file():
        pytest.fail(f"ASR task smoke audio file does not exist: {audio_path}")

    content_type = os.getenv("AI_VIDEO_ASR_TEST_MIME_TYPE") or mimetypes.guess_type(
        audio_path.name
    )[0]
    if not content_type or not content_type.startswith("audio/"):
        pytest.fail(
            "set AI_VIDEO_ASR_TEST_MIME_TYPE to an audio MIME type for an unrecognized file suffix"
        )

    async def exercise() -> None:
        audio_bytes = audio_path.read_bytes()
        probe = await FFprobeAudioValidator(
            timeout_seconds=int(
                os.getenv("AI_VIDEO_ASR_TEST_PROBE_TIMEOUT_SECONDS", "30")
            )
        ).validate_bytes(audio_bytes, content_type)

        timeout_seconds = int(os.getenv("AI_VIDEO_ASR_TIMEOUT_SECONDS", "180"))
        provider_client = httpx.AsyncClient(timeout=timeout_seconds)
        provider = _create_provider(
            provider_name=provider_name,
            base_url=str(
                required.get("AI_VIDEO_ASR_BASE_URL")
                or AliyunDashScopeASRProvider.DEFAULT_BASE_URL
            ),
            api_key=str(api_key),
            model=str(required["AI_VIDEO_ASR_MODEL"]),
            timeout_seconds=timeout_seconds,
            client=provider_client,
        )
        endpoint = os.getenv("AI_VIDEO_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
        bucket = os.getenv("AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
        access_key = os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY", "ai-video-dev")
        secret_key = os.getenv("AI_VIDEO_STORAGE_SECRET_KEY", "ai-video-dev-password")
        storage = S3CompatibleArtifactStorage(
            endpoint=endpoint,
            bucket=bucket,
            region=os.getenv("AI_VIDEO_STORAGE_REGION", "us-east-1"),
            access_key=access_key,
            secret_key=secret_key,
            public_endpoint=os.getenv("AI_VIDEO_STORAGE_PUBLIC_ENDPOINT"),
            timeout_seconds=int(os.getenv("AI_VIDEO_STORAGE_TIMEOUT_SECONDS", "120")),
            signed_url_expire_seconds=300,
        )
        queue = InProcessTaskQueue()
        store = InMemoryStore()
        service = SubtitleTaskService(
            store,
            queue,
            storage,
            asr_provider=provider,
        )
        queue.set_handler(service.run_task)

        project_id = uuid4()
        episode = _sample_episode(project_id)
        source_key = f"audio-narration/{episode.id}/external-asr-{uuid4()}{audio_path.suffix}"
        output_key: str | None = None
        try:
            await store.save_episodes([episode])
            source_audio = await storage.put_bytes(source_key, audio_bytes, content_type)
            source_task = GenerationTaskRecord(
                project_id=project_id,
                kind=GenerationTaskKind.AUDIO_NARRATION,
                input_data={"episode_id": str(episode.id), "source": "external-asr-smoke"},
                status=TaskStatus.SUCCEEDED,
                current_stage=None,
                progress=100,
                artifacts=[
                    ArtifactSummary(
                        type="audio_narration",
                        provider="external-asr-fixture",
                        metadata={
                            "episode_id": str(episode.id),
                            "content_type": source_audio.content_type,
                            "storage_key": source_audio.storage_key,
                            "size_bytes": source_audio.size_bytes,
                            "sha256": source_audio.sha256,
                            "duration_seconds": probe.duration_seconds,
                            "ffprobe": probe.as_metadata(),
                        },
                    )
                ],
            )
            source_task, _ = await store.create_task(source_task)
            source_artifact = source_task.artifacts[0]
            reference_text = os.getenv("AI_VIDEO_ASR_TEST_REFERENCE_TEXT") or None

            task, reused = await service.create_asr_task(
                episode.id,
                SubtitleASRCreateRequest(
                    audio_artifact_id=source_artifact.id,
                    language=os.getenv("AI_VIDEO_ASR_TEST_LANGUAGE", "zh-CN"),
                    reference_text=reference_text,
                ),
                idempotency_key=f"external-asr-task-{uuid4()}",
            )
            await queue.close()

            assert reused is False
            saved_task = await store.get_task(task.id)
            assert saved_task is not None
            assert saved_task.status == TaskStatus.SUCCEEDED, saved_task.error
            assert saved_task.kind == GenerationTaskKind.SUBTITLE_ASR
            assert saved_task.current_stage is None
            assert saved_task.stages[0].stage == StageName.SUBTITLE
            assert saved_task.stages[0].status == TaskStatus.SUCCEEDED
            assert len(saved_task.artifacts) == 1

            artifact = saved_task.artifacts[0]
            output_key = str(artifact.metadata["storage_key"])
            assert artifact.type == "subtitle_srt"
            assert artifact.metadata["alignment_method"] == "asr"
            assert artifact.metadata["alignment_provider"] in {
                "aliyun_dashscope_asr",
                "siliconflow_asr",
                "openai_compatible_asr",
            }
            assert artifact.metadata["alignment_precision"] in {
                "segment_asr",
                "word_boundary",
                "transcript_sentence_estimate",
            }
            assert artifact.metadata["audio_artifact_id"] == str(source_artifact.id)
            assert artifact.metadata["audio_duration_seconds"] == probe.duration_seconds

            registry_artifact = await store.get_artifact(artifact.id)
            assert registry_artifact is not None
            assert registry_artifact.project_id == project_id
            assert registry_artifact.task_id == saved_task.id
            assert registry_artifact.type == "subtitle_srt"

            subtitle_bytes = await storage.get_bytes(output_key)
            assert subtitle_bytes
            cues = parse_srt(subtitle_bytes)
            assert cues
            assert all(cue.text.strip() for cue in cues)
            assert all(0 <= cue.start_seconds < cue.end_seconds for cue in cues)
            assert cues[-1].end_seconds <= probe.duration_seconds + 5.0
            assert hashlib.sha256(subtitle_bytes).hexdigest() == artifact.metadata["sha256"]

            quality_report = evaluate_subtitle_cues(
                cues,
                audio_duration_seconds=probe.duration_seconds,
                reference_text=reference_text,
                alignment_precision=str(artifact.metadata["alignment_precision"]),
            )
            assert quality_report.structural_gate_passed is True
            if reference_text:
                assert quality_report.text_accuracy_passed is not None
            quality_report_path = os.getenv("AI_VIDEO_ASR_QUALITY_REPORT_PATH")
            if quality_report_path:
                report_path = Path(quality_report_path).expanduser()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(quality_report.as_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(storage.create_download_url(output_key))
            assert response.status_code == 200
            assert response.content == subtitle_bytes
        finally:
            await queue.close()
            await provider.close()
            await provider_client.aclose()
            await storage.close()
            if (
                os.getenv("AI_VIDEO_MINIO_COMPOSE_CLEANUP", "1") == "1"
                and urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
            ):
                _cleanup_local_minio_objects(bucket, source_key, output_key)

    asyncio.run(exercise())


def _create_provider(
    *,
    provider_name: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: int,
    client: httpx.AsyncClient,
) -> AliyunDashScopeASRProvider | SiliconFlowASRProvider | OpenAICompatibleASRProvider:
    if provider_name == "aliyun_dashscope":
        return AliyunDashScopeASRProvider(
            base_url=base_url or AliyunDashScopeASRProvider.DEFAULT_BASE_URL,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            client=client,
        )
    if provider_name == "siliconflow":
        return SiliconFlowASRProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            client=client,
        )
    return OpenAICompatibleASRProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        client=client,
    )


def _sample_episode(project_id: UUID) -> EpisodeRecord:
    return EpisodeRecord(
        project_id=project_id,
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="真实 ASR 任务样例",
            logline="验证旁白音频到字幕 Artifact 的任务闭环",
            objective="完成真实 ASR 转写",
            conflict="外部 Provider 返回协议可能不同",
            turning_point="任务生成可读取的 SRT Artifact",
            ending_hook="进入字幕质量和 word-boundary 评估",
            source_chapter_numbers=[1],
            target_duration_seconds=30,
        ),
        provider="external-asr-integration",
        model="integration-fixture",
        duration_ms=0,
    )


def _cleanup_local_minio_objects(bucket: str, *storage_keys: str | None) -> None:
    for storage_key in storage_keys:
        if not storage_key:
            continue
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
