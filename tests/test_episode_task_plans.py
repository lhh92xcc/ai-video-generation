from __future__ import annotations

import time
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.domain.models import (
    AssetType,
    ReferenceImageRecord,
    ReferenceImageStatus,
    utc_now,
)
from app.services.episode_task_plan_service import EpisodeTaskPlanService


NOVEL_TEXT = """第一章 雨夜
主角在雨中醒来。

第二章 线索
主角找到一封旧信。

第三章 追问
主角开始寻找真相。
"""


def wait_for_task(client: TestClient, task_id: str) -> dict:
    for _ in range(500):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not finish: {task_id}")


def create_plannable_project(client: TestClient) -> tuple[str, list[dict]]:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "按分集自动计划", "target_episode_count": 2},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", NOVEL_TEXT.encode("utf-8"), "text/plain")},
    ).raise_for_status()
    story_task = client.post(
        f"/api/v1/novel-projects/{project_id}/story-bible/tasks",
        headers={"Idempotency-Key": "plan-story-bible"},
    ).json()
    assert wait_for_task(client, story_task["id"])["status"] == "succeeded"
    episode_plan_task = client.post(
        f"/api/v1/novel-projects/{project_id}/episodes/tasks",
        json={"target_episode_count": 2},
        headers={"Idempotency-Key": "plan-episode-outline"},
    ).json()
    assert wait_for_task(client, episode_plan_task["id"])["status"] == "succeeded"
    episodes = client.get(f"/api/v1/novel-projects/{project_id}/episodes").json()
    return project_id, episodes


def test_episode_task_plan_advances_script_then_shot_tasks(client: TestClient) -> None:
    project_id, episodes = create_plannable_project(client)
    episode_ids = [episode["id"] for episode in episodes]

    first = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json={"episode_ids": episode_ids, "label": "第一阶段分集计划"},
        headers={"Idempotency-Key": "episode-plan-wave-1"},
    )
    assert first.status_code == 202, first.text
    first_body = first.json()
    assert first_body["batch"] is not None
    assert first_body["created_count"] == 2
    assert first_body["reused_count"] == 0
    assert {item["stage"] for item in first_body["items"]} == {"novel_episode_script"}

    for task_id in first_body["batch"]["task_ids"]:
        assert wait_for_task(client, task_id)["status"] == "succeeded"

    second = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json={"episode_ids": episode_ids, "label": "第二阶段分集计划"},
        headers={"Idempotency-Key": "episode-plan-wave-2"},
    )
    assert second.status_code == 202, second.text
    second_body = second.json()
    assert second_body["created_count"] == 2
    assert {item["stage"] for item in second_body["items"]} == {"novel_shot_list"}
    for task_id in second_body["batch"]["task_ids"]:
        assert wait_for_task(client, task_id)["status"] == "succeeded"

    completed = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json={"episode_ids": episode_ids, "label": "已完成检查"},
        headers={"Idempotency-Key": "episode-plan-wave-3"},
    )
    assert completed.status_code == 202, completed.text
    completed_body = completed.json()
    assert completed_body["batch"] is None
    assert completed_body["skipped_count"] == 2
    assert all(item["action"] == "skipped" for item in completed_body["items"])


def test_episode_task_plan_is_idempotent_and_rejects_cross_project_episode(client: TestClient) -> None:
    project_id, episodes = create_plannable_project(client)
    payload = {"episode_ids": [episodes[0]["id"]], "label": "幂等计划"}
    first = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json=payload,
        headers={"Idempotency-Key": "same-plan"},
    )
    second = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json=payload,
        headers={"Idempotency-Key": "same-plan"},
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["batch"]["id"] == second.json()["batch"]["id"]
    assert first.json()["items"][0]["task_id"] == second.json()["items"][0]["task_id"]

    other_project, other_episodes = create_plannable_project(client)
    mismatch = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json={"episode_ids": [other_episodes[0]["id"]]},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "EPISODE_TASK_PLAN_EPISODE_MISMATCH"
    assert other_project != project_id


def test_plan_prefers_character_standard_identity_over_newer_variant() -> None:
    now = utc_now()
    anchor = ReferenceImageRecord(
        task_id=uuid4(),
        project_id=uuid4(),
        asset_id=uuid4(),
        asset_key=uuid4(),
        asset_type=AssetType.CHARACTER,
        asset_version=1,
        prompt="标准人设",
        negative_prompt="模糊",
        status=ReferenceImageStatus.SUCCEEDED,
        width=512,
        height=512,
        created_at=now - timedelta(minutes=1),
        metadata={"storage_key": "references/anchor.png", "identity_anchor": True},
    )
    variant = anchor.model_copy(
        update={
            "id": uuid4(),
            "task_id": uuid4(),
            "created_at": now,
            "metadata": {
                "storage_key": "references/variant.png",
                "reference_role": "identity_locked_variant",
                "identity_anchor_reference_image_id": str(anchor.id),
            },
        },
        deep=True,
    )

    selected = EpisodeTaskPlanService._preferred_reference_image(
        [variant, anchor],
        AssetType.CHARACTER,
    )

    assert selected is not None
    assert selected.id == anchor.id


def test_production_plan_advances_narration_then_subtitles_and_preserves_asset_gate(client: TestClient) -> None:
    project_id, episodes = create_plannable_project(client)
    episode_id = episodes[0]["id"]
    content_wave = {"episode_ids": [episode_id]}
    script_wave = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json=content_wave,
        headers={"Idempotency-Key": "production-content-script"},
    )
    assert script_wave.status_code == 202, script_wave.text
    assert wait_for_task(client, script_wave.json()["batch"]["task_ids"][0])["status"] == "succeeded"
    shot_wave = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json=content_wave,
        headers={"Idempotency-Key": "production-content-shot"},
    )
    assert shot_wave.status_code == 202, shot_wave.text
    assert wait_for_task(client, shot_wave.json()["batch"]["task_ids"][0])["status"] == "succeeded"
    production_payload = {
        "episode_ids": [episode_id],
        "production_mode": True,
        "include_reference_images": False,
        "include_narration": True,
        "include_subtitles": True,
        "include_bgm": False,
        "include_video": False,
        "include_assembly": False,
    }

    narration = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json=production_payload,
        headers={"Idempotency-Key": "production-narration"},
    )
    assert narration.status_code == 202, narration.text
    narration_body = narration.json()
    assert narration_body["items"][0]["stage"] == "audio_narration"
    assert narration_body["items"][0]["task_ids"] == [narration_body["items"][0]["task_id"]]
    assert wait_for_task(client, narration_body["batch"]["task_ids"][0])["status"] == "succeeded"

    subtitles = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json=production_payload,
        headers={"Idempotency-Key": "production-subtitles"},
    )
    assert subtitles.status_code == 202, subtitles.text
    subtitles_body = subtitles.json()
    assert subtitles_body["items"][0]["stage"] == "subtitle_align"
    assert wait_for_task(client, subtitles_body["batch"]["task_ids"][0])["status"] == "succeeded"

    blocked = client.post(
        f"/api/v1/novel-projects/{project_id}/episode-task-plans",
        json={
            "episode_ids": [episode_id],
            "production_mode": True,
            "include_reference_images": False,
            "include_narration": False,
            "include_subtitles": False,
            "include_video": True,
            "include_assembly": False,
        },
        headers={"Idempotency-Key": "production-video-gate"},
    )
    assert blocked.status_code == 202, blocked.text
    blocked_body = blocked.json()
    assert blocked_body["items"][0]["action"] == "blocked"
    assert blocked_body["items"][0]["stage"] == "video_clip"
    assert blocked_body["items"][0]["blocked_reasons"]
