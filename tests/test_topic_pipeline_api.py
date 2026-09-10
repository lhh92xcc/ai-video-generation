from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _wait_for_run(
    client: TestClient,
    project_id: str,
    run_id: str,
    expected: set[str],
    timeout: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    latest: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/projects/{project_id}/production-runs/{run_id}"
        )
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest.get("status") in expected:
            return latest
        time.sleep(0.02)
    return latest


def _create_topic_project(client: TestClient, title: str) -> str:
    response = client.post(
        "/api/v1/projects",
        json={
            "title": title,
            "topic": "用三个步骤讲清一个适合新手的实用主题。",
            "target_duration_seconds": 15,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_topic_production_run_is_idempotent_and_queryable(client: TestClient) -> None:
    project_id = _create_topic_project(client, "主题 Run 幂等")
    headers = {"Idempotency-Key": "topic-api-idempotency"}
    payload = {
        "include_narration": False,
        "include_subtitles": False,
        "include_video": False,
        "include_assembly": False,
    }

    first = client.post(
        f"/api/v1/projects/{project_id}/production-runs",
        json=payload,
        headers=headers,
    )
    second = client.post(
        f"/api/v1/projects/{project_id}/production-runs",
        json=payload,
        headers=headers,
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    first_body = first.json()
    second_body = second.json()
    assert second_body["run_id"] == first_body["run_id"]
    assert second_body["task_ids"] == first_body["task_ids"]

    completed = _wait_for_run(
        client,
        project_id,
        first_body["run_id"],
        {"completed"},
    )
    assert completed["status"] == "completed"

    latest = client.get(f"/api/v1/projects/{project_id}/production-runs/latest")
    by_id = client.get(
        f"/api/v1/projects/{project_id}/production-runs/{first_body['run_id']}"
    )
    assert latest.status_code == 200, latest.text
    assert by_id.status_code == 200, by_id.text
    assert latest.json()["run_id"] == first_body["run_id"]
    assert by_id.json()["status"] == "completed"


def test_topic_production_run_with_unconfigured_provider_is_durable_blocked(
    client: TestClient,
) -> None:
    project_id = _create_topic_project(client, "主题 Provider 门禁")
    response = client.post(
        f"/api/v1/projects/{project_id}/production-runs",
        json={
            "include_narration": False,
            "include_subtitles": False,
            "include_video": True,
            "include_assembly": False,
            "video_provider_profile_id": "video.openai_compatible",
        },
        headers={"Idempotency-Key": "topic-unconfigured-provider"},
    )

    assert response.status_code == 202, response.text
    run = response.json()
    blocked = _wait_for_run(client, project_id, run["run_id"], {"blocked"})
    assert blocked["status"] == "blocked"
    assert blocked["error_code"] == "PROVIDER_PROFILE_NOT_CONFIGURED"
    assert blocked["error_message"]


def test_topic_production_run_rejects_assembly_without_video(client: TestClient) -> None:
    project_id = _create_topic_project(client, "主题 Assembly 参数校验")
    response = client.post(
        f"/api/v1/projects/{project_id}/production-runs",
        json={
            "include_narration": True,
            "include_subtitles": False,
            "include_video": False,
            "include_assembly": True,
        },
        headers={"Idempotency-Key": "topic-invalid-assembly"},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert any(
        "topic assembly requires video clips" in detail["message"]
        for detail in body["error"]["details"]
    )
    tasks = client.get(f"/api/v1/tasks?project_id={project_id}&limit=100")
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["items"] == []


def test_topic_mock_video_failure_marks_run_failed_without_fake_artifact(
    client: TestClient,
) -> None:
    project_id = _create_topic_project(client, "主题 Mock 视频失败")
    response = client.post(
        f"/api/v1/projects/{project_id}/production-runs",
        json={
            "include_narration": False,
            "include_subtitles": False,
            "include_video": True,
            "include_assembly": False,
            "video_provider_profile_id": "video.mock",
        },
        headers={"Idempotency-Key": "topic-mock-video-failure"},
    )

    assert response.status_code == 202, response.text
    run = response.json()
    failed = _wait_for_run(client, project_id, run["run_id"], {"failed"})
    assert failed["status"] == "failed"
    assert failed["error_code"] == "VIDEO_PROVIDER_OUTPUT_NOT_STORABLE"

    tasks = client.get(f"/api/v1/tasks?project_id={project_id}&kind=video_clip")
    assert tasks.status_code == 200, tasks.text
    video_tasks = tasks.json()["items"]
    assert video_tasks
    assert all(task["status"] == "failed" for task in video_tasks)
    assert all(
        task["error"]["code"] == "VIDEO_PROVIDER_OUTPUT_NOT_STORABLE"
        for task in video_tasks
    )
    artifacts = client.get(
        f"/api/v1/artifacts?project_id={project_id}&type=video_clip"
    )
    assert artifacts.status_code == 200, artifacts.text
    assert artifacts.json()["total"] == 0


def test_regular_generation_remains_script_only(client: TestClient) -> None:
    project_id = _create_topic_project(client, "主题兼容脚本接口")
    response = client.post(f"/api/v1/projects/{project_id}/generations")
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]

    deadline = time.monotonic() + 3
    task: dict[str, object] = {}
    while time.monotonic() < deadline:
        task_response = client.get(f"/api/v1/tasks/{task_id}")
        assert task_response.status_code == 200, task_response.text
        task = task_response.json()
        if task.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    assert task["status"] == "succeeded"
    project_tasks = client.get(f"/api/v1/tasks?project_id={project_id}&limit=100")
    assert project_tasks.status_code == 200, project_tasks.text
    assert {item["kind"] for item in project_tasks.json()["items"]} == {"info_script"}


def test_topic_production_run_unknown_run_is_not_found(client: TestClient) -> None:
    project_id = _create_topic_project(client, "主题 Run 404")
    response = client.get(
        f"/api/v1/projects/{project_id}/production-runs/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TOPIC_RUN_NOT_FOUND"
