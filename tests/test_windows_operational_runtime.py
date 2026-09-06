from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import load_settings


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
    assert settings.queue_backend == "redis"
    assert settings.video_provider == "comfyui_wan_i2v"
    assert settings.image_width == 768
    assert settings.image_height == 1024
    assert settings.image_steps == 4
    assert settings.image_guidance == 3.5
    assert settings.image_identity_weight == 0.9
    assert settings.video_output_width == 320
    assert settings.video_output_height == 576
    assert settings.video_fps == 8
    assert settings.video_steps == 6
    assert settings.video_cfg == 5.0
    assert settings.video_noise_aug_strength == 0.02
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

    second = client.post(
        f"/api/v1/novel-projects/{project_id}/production-runs",
        json={"target_episode_count": 1},
        headers=headers,
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    assert second_body["run_id"] == first_body["run_id"]
