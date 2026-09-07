from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    TaskError,
    TaskStatus,
)
from app.main import create_app
from app.rendering.ffmpeg_renderer import FFmpegVideoRenderer, RenderedVideo


def wait_for_task(client: TestClient, task_id: str) -> dict:
    for _ in range(300):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task.get("status") in {"succeeded", "failed"}:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not finish: {task_id}")


def test_windows_gpu_profile_contains_serial_gpu_runtime_defaults() -> None:
    settings = load_settings("config/config.windows_gpu.toml")

    assert settings.runtime_profile == "windows_gpu"
    assert settings.visual_quality_profile == "local_balanced"
    assert settings.queue_backend == "redis"
    assert settings.video_provider == "comfyui_wan_i2v"
    assert settings.image_model == "flux1-schnell-Q4_K_S.gguf"
    assert settings.video_model == "wan2.1-i2v-14b-480p-Q4_K_S.gguf"
    assert settings.image_width == 576
    assert settings.image_height == 1024
    assert settings.image_steps == 6
    assert settings.image_guidance == 4.0
    assert settings.image_identity_weight == 0.92
    assert settings.video_output_width == 432
    assert settings.video_output_height == 768
    assert settings.video_fps == 16
    assert settings.video_steps == 8
    assert settings.video_cfg == 5.0
    assert settings.video_noise_aug_strength == 0.01
    assert "identity" in settings.video_prompt_suffix
    assert settings.worker_gpu_lock_enabled is True
    assert settings.worker_scheduler_enabled is True
    assert settings.worker_cleanup_enabled is True
    assert settings.lip_sync_provider == "musetalk_http"
    assert settings.lip_sync_base_url == "http://host.docker.internal:8090"


def test_windows_smoke_uses_host_paths_and_passes_quality_profile() -> None:
    launcher = Path("scripts/start-windows-gpu.ps1").read_text(encoding="utf-8")

    assert '$env:AI_VIDEO_STORAGE_PROVIDER = "local"' in launcher
    assert '$env:AI_VIDEO_STORAGE_BASE_PATH = (Join-Path $ProjectRoot ".tmp/windows-portfolio-artifacts")' in launcher
    assert '$env:AI_VIDEO_IMAGE_BASE_URL = "http://127.0.0.1:8188"' in launcher
    assert '$env:AI_VIDEO_VIDEO_BASE_URL = "http://127.0.0.1:8188"' in launcher
    assert '"--quality-profile", $SmokeQualityProfile' in launcher


def test_operational_health_and_remote_queue_endpoints_are_queryable(client: TestClient) -> None:
    health = client.get("/api/v1/system/health")
    assert health.status_code == 200, health.text
    health_body = health.json()
    assert health_body["profile"] == "default"
    assert "gpu_lock_enabled" in health_body
    assert {item["name"] for item in health_body["components"]} == {
        "redis",
        "ollama",
        "comfyui",
        "musetalk",
    }

    queue = client.get("/api/v1/system/queue?limit=10")
    assert queue.status_code == 200, queue.text
    queue_body = queue.json()
    assert queue_body["profile"] == "default"
    assert isinstance(queue_body["counts"], dict)
    assert queue_body["tasks"] == []


def test_operational_health_can_probe_frontend_selected_local_profiles(client: TestClient) -> None:
    health = client.get(
        "/api/v1/system/health"
        "?image_provider_profile_id=image.comfyui"
        "&video_provider_profile_id=video.comfyui_wan_i2v"
    )
    assert health.status_code == 200, health.text
    comfyui = next(item for item in health.json()["components"] if item["name"] == "comfyui")
    assert comfyui["status"] != "not_configured"


def test_one_click_production_run_starts_and_reuses_same_idempotency_key(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "Windows 自动生产", "target_episode_count": 1},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    source = client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("story.txt", "第一章\n雨夜里有人敲门。".encode("utf-8"), "text/plain")},
    )
    assert source.status_code == 201, source.text

    headers = {"Idempotency-Key": "windows-production-run"}
    first = client.post(
        f"/api/v1/novel-projects/{project_id}/production-runs",
        json={"target_episode_count": 1},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    first_body = first.json()
    assert first_body["status"] in {"active", "blocked", "completed"}
    assert first_body["run_id"]
    assert first_body["task_ids"]
    seed_task = client.get(f"/api/v1/tasks/{first_body['task_ids'][0]}")
    assert seed_task.status_code == 200, seed_task.text
    assert seed_task.json()["input_data"]["auto_run_plan"]["production_mode"] is True

    content_only = client.post(
        f"/api/v1/novel-projects/{project_id}/production-runs",
        json={"target_episode_count": 1, "production_mode": False},
        headers={"Idempotency-Key": "windows-production-run-content-only"},
    )
    assert content_only.status_code == 422, content_only.text

    second = client.post(
        f"/api/v1/novel-projects/{project_id}/production-runs",
        json={"target_episode_count": 1},
        headers=headers,
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    assert second_body["run_id"] == first_body["run_id"]

    latest = client.get(
        f"/api/v1/novel-projects/{project_id}/production-runs/latest"
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["run_id"] == first_body["run_id"]
    assert latest.json()["project_id"] == project_id

    by_id = client.get(
        f"/api/v1/novel-projects/{project_id}/production-runs/{first_body['run_id']}"
    )
    assert by_id.status_code == 200, by_id.text
    assert by_id.json()["run_id"] == first_body["run_id"]

    missing = client.get(
        f"/api/v1/novel-projects/{project_id}/production-runs/00000000-0000-0000-0000-000000000000"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PRODUCTION_RUN_NOT_FOUND"


def test_terminal_auto_run_failure_is_not_reported_as_active(client: TestClient) -> None:
    project_id = uuid4()
    run_id = uuid4()

    async def exercise() -> tuple[str, bool, str]:
        task = GenerationTaskRecord(
            project_id=project_id,
            kind=GenerationTaskKind.NOVEL_STORY_BIBLE,
            input_data={
                "auto_run_id": str(run_id),
                "auto_advance": True,
                "auto_run_status": "active",
            },
            status=TaskStatus.FAILED,
            error=TaskError(code="PROVIDER_AUTH_FAILED", message="test failure"),
        )
        await client.app.state.store.create_task(task)
        await client.app.state.production_orchestrator.on_task_failed(task.id)
        response = await client.app.state.production_orchestrator.get_run(project_id, run_id)
        return response.status, response.auto_advance, response.message

    status, auto_advance, message = asyncio.run(exercise())
    assert status == "failed"
    assert auto_advance is False
    assert "不可自动恢复" in message


def test_production_run_reaches_completed_after_final_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the complete media DAG without treating fixture media as a demo."""

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for the media DAG regression")

    monkeypatch.setenv("AI_VIDEO_TTS_PROVIDER", "mock")
    monkeypatch.setenv("AI_VIDEO_VIDEO_PROVIDER", "local_fixture")
    monkeypatch.setenv("AI_VIDEO_IMAGE_PROVIDER", "mock")
    monkeypatch.setenv("AI_VIDEO_SUBTITLE_ALIGNMENT_PROVIDER", "mock_sentence")

    class AssemblyProbeRenderer:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int]] = []

        async def render(
            self,
            clips,
            input_content_types=None,
            *,
            audio_tracks=None,
            subtitles=None,
            **kwargs,
        ) -> RenderedVideo:
            del kwargs
            # The host test FFmpeg may not ship libass. Exercise real clip
            # concatenation and audio mixing, while the renderer contract test
            # separately covers the subtitle filter gate. The parsed subtitle
            # count is retained so this test still verifies artifact handoff.
            rendered = await FFmpegVideoRenderer(timeout_seconds=30).render(
                clips,
                input_content_types,
                audio_tracks=audio_tracks,
                subtitles=(),
            )
            rendered = replace(rendered, subtitle_count=len(subtitles or ()))
            self.calls.append((len(clips), len(audio_tracks or ()), len(subtitles or ())))
            return rendered

    with TestClient(create_app()) as test_client:
        renderer = AssemblyProbeRenderer()
        test_client.app.state.video_assembly_task_service._renderer = renderer

        project = test_client.post(
            "/api/v1/novel-projects",
            json={"title": "完整媒体 DAG 回归", "target_episode_count": 1},
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]

        source = test_client.post(
            f"/api/v1/novel-projects/{project_id}/sources",
            files={
                "file": (
                    "story.txt",
                    "第一章\n雨夜里有人敲门。".encode("utf-8"),
                    "text/plain",
                )
            },
        )
        assert source.status_code == 201, source.text
        run = test_client.post(
            f"/api/v1/novel-projects/{project_id}/production-runs",
            json={
                "target_episode_count": 1,
                "include_reference_images": False,
                "include_narration": True,
                "include_subtitles": True,
                "include_video": True,
                "include_assembly": True,
            },
            headers={"Idempotency-Key": "complete-media-dag"},
        )
        assert run.status_code == 202, run.text

        # Let the automatic content stages reach their asset gate first. This
        # proves that the one-click command starts at the uploaded novel rather
        # than requiring separate StoryBible/episode HTTP calls.
        gate_deadline = time.monotonic() + 8
        gate_state = None
        while time.monotonic() < gate_deadline:
            episodes = test_client.get(
                f"/api/v1/novel-projects/{project_id}/episodes"
            ).json()
            latest_response = test_client.get(
                f"/api/v1/novel-projects/{project_id}/production-runs/latest"
            )
            assert latest_response.status_code == 200, latest_response.text
            gate_state = latest_response.json()
            if episodes and gate_state["status"] == "blocked":
                break
            time.sleep(0.02)
        assert episodes, "automatic Run did not create an episode"
        assert gate_state is not None
        assert gate_state["status"] == "blocked", gate_state

        assets = test_client.post(f"/api/v1/novel-projects/{project_id}/assets/sync")
        assert assets.status_code == 201, assets.text
        for asset in assets.json():
            review = test_client.post(
                f"/api/v1/assets/{asset['id']}/reviews",
                json={
                    "status": "ready",
                    "reviewer": "dag-regression",
                    "comment": "Fixture-only automated gate test",
                },
            )
            assert review.status_code == 201, review.text

        deadline = time.monotonic() + 8
        latest = None
        while time.monotonic() < deadline:
            latest_response = test_client.get(
                f"/api/v1/novel-projects/{project_id}/production-runs/latest"
            )
            assert latest_response.status_code == 200, latest_response.text
            latest = latest_response.json()
            if latest["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)

        assert latest is not None
        assert latest["status"] == "completed", latest
        assert latest["auto_advance"] is False
        tasks = test_client.get(
            f"/api/v1/tasks?project_id={project_id}&limit=200"
        ).json()["items"]
        kinds = {task["kind"] for task in tasks}
        assert {
            "novel_episode_script",
            "novel_shot_list",
            "audio_narration",
            "subtitle_align",
            "video_clip",
            "video_assembly",
        } <= kinds
        assembly_tasks = [task for task in tasks if task["kind"] == "video_assembly"]
        assert len(assembly_tasks) == 1
        assembly = assembly_tasks[0]
        assert assembly["status"] == "succeeded"
        assert assembly["artifacts"][0]["type"] == "rendered_video"
        assert assembly["artifacts"][0]["metadata"]["subtitle_count"] > 0
        assert assembly["artifacts"][0]["metadata"]["audio_track_count"] == 1
        assert renderer.calls
        clip_count, audio_count, subtitle_count = renderer.calls[-1]
        assert clip_count == len([task for task in tasks if task["kind"] == "video_clip"])
        assert audio_count == 1
        assert subtitle_count > 0
