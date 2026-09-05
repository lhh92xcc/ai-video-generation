from __future__ import annotations

import time

from fastapi.testclient import TestClient


def test_healthz_returns_version(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
    assert response.headers["X-Request-ID"].startswith("req_")


def test_provider_profile_list_is_safe_for_frontend_dropdown(client: TestClient) -> None:
    response = client.get("/api/v1/provider-profiles?capability=asr")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["default_profile_id"].startswith("asr.")
    profile_ids = {item["profile_id"] for item in body["items"]}
    assert profile_ids == {
        "asr.mock",
        "asr.aliyun_dashscope",
        "asr.siliconflow",
        "asr.openai_compatible",
    }
    assert all("api_key" not in item for item in body["items"])


def test_create_and_list_project(client: TestClient) -> None:
    payload = {
        "title": "露营装备推荐",
        "topic": "新手周末露营装备推荐",
        "target_duration_seconds": 60,
    }
    create_response = client.post("/api/v1/projects", json=payload)

    assert create_response.status_code == 201
    project = create_response.json()
    assert project["title"] == payload["title"]
    assert project["status"] == "draft"

    list_response = client.get("/api/v1/projects")

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["id"] == project["id"]


def test_create_and_list_novel_project(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/novel-projects",
        json={"title": "可选项目", "target_episode_count": 2},
    )

    assert create_response.status_code == 201
    project_id = create_response.json()["id"]
    list_response = client.get("/api/v1/novel-projects")

    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == project_id


def test_generation_task_runs_mock_script_provider(client: TestClient) -> None:
    project_response = client.post(
        "/api/v1/projects",
        json={"title": "面试题", "topic": "FastAPI 任务队列", "target_duration_seconds": 15},
    )
    project_id = project_response.json()["id"]

    task_response = client.post(f"/api/v1/projects/{project_id}/generations")

    assert task_response.status_code == 202
    task_id = task_response.json()["id"]
    assert task_response.json()["status"] == "queued"

    deadline = time.monotonic() + 2
    final_task = None
    while time.monotonic() < deadline:
        current_response = client.get(f"/api/v1/tasks/{task_id}")
        final_task = current_response.json()
        if final_task["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    assert final_task is not None
    assert final_task["status"] == "succeeded"
    assert final_task["progress"] == 100
    assert final_task["current_stage"] is None
    assert final_task["stages"][0]["stage"] == "script"
    assert final_task["stages"][0]["status"] == "succeeded"
    assert final_task["artifacts"][0]["type"] == "script_json"
    assert final_task["artifacts"][0]["provider"] == "mock"
    assert final_task["artifacts"][0]["preview"]["scenes"]

    artifact_id = final_task["artifacts"][0]["id"]
    artifact_response = client.get(f"/api/v1/artifacts/{artifact_id}")

    assert artifact_response.status_code == 200
    artifact = artifact_response.json()
    assert artifact["id"] == artifact_id
    assert artifact["task_id"] == task_id
    assert artifact["project_id"] == project_id
    assert artifact["type"] == "script_json"
    assert artifact["download_url"] is None

    task_list_response = client.get(
        f"/api/v1/tasks?project_id={project_id}&kind=info_script&status=succeeded"
    )
    assert task_list_response.status_code == 200
    assert task_list_response.json()["total"] == 1
    assert task_list_response.json()["items"][0]["id"] == task_id

    artifact_list_response = client.get(
        f"/api/v1/artifacts?project_id={project_id}&type=script_json"
    )
    assert artifact_list_response.status_code == 200
    assert artifact_list_response.json()["total"] == 1
    assert artifact_list_response.json()["items"][0]["id"] == artifact_id


def test_artifact_query_returns_stable_not_found_error(client: TestClient) -> None:
    response = client.get("/api/v1/artifacts/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"


def test_generation_is_idempotent_for_same_project_key(client: TestClient) -> None:
    project_response = client.post(
        "/api/v1/projects",
        json={"title": "幂等测试", "topic": "相同请求不重复创建任务"},
    )
    project_id = project_response.json()["id"]
    headers = {"Idempotency-Key": "same-generation-request"}

    first = client.post(f"/api/v1/projects/{project_id}/generations", headers=headers)
    second = client.post(f"/api/v1/projects/{project_id}/generations", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_invalid_project_payload_uses_stable_error_shape(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects",
        json={"title": "", "topic": "", "target_duration_seconds": 5},
        headers={"X-Request-ID": "req_test_validation"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["request_id"] == "req_test_validation"
    assert body["error"]["details"]


def test_missing_resources_use_stable_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


def test_audio_task_requires_existing_episode(client: TestClient) -> None:
    response = client.post(
        "/api/v1/episodes/00000000-0000-0000-0000-000000000000/audio",
        json={"text": "测试旁白"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"


def test_bgm_task_requires_existing_episode(client: TestClient) -> None:
    response = client.post(
        "/api/v1/episodes/00000000-0000-0000-0000-000000000000/bgm",
        json={"label": "测试 BGM"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"


def test_subtitle_task_requires_existing_episode(client: TestClient) -> None:
    response = client.post(
        "/api/v1/episodes/00000000-0000-0000-0000-000000000000/subtitles",
        json={
            "cues": [{"start_seconds": 0, "end_seconds": 1, "text": "测试字幕"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"


def test_subtitle_alignment_task_requires_existing_episode(client: TestClient) -> None:
    response = client.post(
        "/api/v1/episodes/00000000-0000-0000-0000-000000000000/subtitles/align",
        json={"text": "测试字幕", "audio_duration_seconds": 2},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"


def test_subtitle_asr_task_requires_existing_episode(client: TestClient) -> None:
    response = client.post(
        "/api/v1/episodes/00000000-0000-0000-0000-000000000000/subtitles/asr",
        json={"audio_artifact_id": "00000000-0000-0000-0000-000000000001"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"
