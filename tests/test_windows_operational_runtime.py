from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import load_settings
from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    TaskError,
    TaskStatus,
)


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
    assert settings.video_output_width == 384
    assert settings.video_output_height == 672
    assert settings.video_fps == 12
    assert settings.video_steps == 8
    assert settings.video_cfg == 5.5
    assert settings.video_noise_aug_strength == 0.015
    assert "identity" in settings.video_prompt_suffix
    assert settings.worker_gpu_lock_enabled is True
    assert settings.worker_scheduler_enabled is True
    assert settings.worker_cleanup_enabled is True
    assert settings.lip_sync_provider == "musetalk_http"
    assert settings.lip_sync_base_url == "http://host.docker.internal:8090"


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
