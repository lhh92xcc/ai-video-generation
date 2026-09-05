from __future__ import annotations

import time

from fastapi.testclient import TestClient


NOVEL_TEXT = """序言\n\n第一章 雨夜\n主角在雨中醒来。\n\n第二章 线索\n主角找到一封旧信。\n\n第三章 追问\n主角开始寻找真相。\n"""


def wait_for_task(client: TestClient, task_id: str) -> dict:
    for _ in range(50):
        response = client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not finish: {task_id}")


def create_novel_with_bible(client: TestClient) -> dict:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "异步剧本工厂", "target_episode_count": 2},
    ).json()
    client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", NOVEL_TEXT.encode("utf-8"), "text/plain")},
    ).raise_for_status()
    task = client.post(
        f"/api/v1/novel-projects/{project['id']}/story-bible/tasks",
        headers={"Idempotency-Key": "story-bible-1"},
    )
    assert task.status_code == 202
    task_body = task.json()
    assert task_body["kind"] == "novel_story_bible"
    assert task_body["current_stage"] == "story_bible"
    completed = wait_for_task(client, task_body["id"])
    assert completed["status"] == "succeeded"
    assert completed["artifacts"][0]["type"] == "story_bible_json"
    return project


def test_async_novel_pipeline_tasks_are_queryable_and_idempotent(client: TestClient) -> None:
    project = create_novel_with_bible(client)
    project_id = project["id"]

    reused = client.post(
        f"/api/v1/novel-projects/{project_id}/episodes/tasks",
        json={"target_episode_count": 2},
        headers={"Idempotency-Key": "plan-1"},
    ).json()
    same_task = client.post(
        f"/api/v1/novel-projects/{project_id}/episodes/tasks",
        json={"target_episode_count": 2},
        headers={"Idempotency-Key": "plan-1"},
    ).json()
    assert reused["id"] == same_task["id"]
    assert reused["kind"] == "novel_episode_plan"
    plan_task = wait_for_task(client, reused["id"])
    assert plan_task["status"] == "succeeded"
    assert plan_task["artifacts"][0]["type"] == "episode_outline_json"

    episodes = client.get(f"/api/v1/novel-projects/{project_id}/episodes").json()
    assert len(episodes) == 2
    episode_id = episodes[0]["id"]

    script_task_response = client.post(f"/api/v1/episodes/{episode_id}/script/tasks")
    assert script_task_response.status_code == 202
    script_task = wait_for_task(client, script_task_response.json()["id"])
    assert script_task["status"] == "succeeded"
    assert script_task["artifacts"][0]["type"] == "episode_script_json"

    shots_task_response = client.post(f"/api/v1/episodes/{episode_id}/shots/tasks")
    assert shots_task_response.status_code == 202
    shots_task = wait_for_task(client, shots_task_response.json()["id"])
    assert shots_task["status"] == "succeeded"
    assert shots_task["artifacts"][0]["type"] == "shot_list_json"

    episode = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert episode["status"] == "shots_ready"


def test_async_novel_story_bible_requires_source(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "异步前置条件"}
    ).json()

    response = client.post(
        f"/api/v1/novel-projects/{project['id']}/story-bible/tasks"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOVEL_SOURCE_REQUIRED"


def test_retry_rejects_a_successful_task(client: TestClient) -> None:
    project = client.post(
        "/api/v1/projects", json={"title": "重试测试", "topic": "异步任务"}
    ).json()
    task = client.post(f"/api/v1/projects/{project['id']}/generations").json()
    completed = wait_for_task(client, task["id"])
    assert completed["status"] == "succeeded"

    response = client.post(f"/api/v1/tasks/{task['id']}/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TASK_NOT_RETRYABLE"
