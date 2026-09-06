from __future__ import annotations

import time

from fastapi.testclient import TestClient


def wait_for_task(client: TestClient, task_id: str) -> dict:
    for _ in range(50):
        response = client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not finish: {task_id}")


def create_ready_character(client: TestClient) -> dict:
    project = client.post("/api/v1/novel-projects", json={"title": "参考图测试"}).json()
    client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", "第一章\n主角拿到旧信。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project['id']}/story-bible/generate").raise_for_status()
    assets = client.post(f"/api/v1/novel-projects/{project['id']}/assets/sync").json()
    character = next(item for item in assets if item["asset_type"] == "character")
    ready = client.post(
        f"/api/v1/assets/{character['id']}/versions",
        json={
            "status": "ready",
            "content": {
                "age_range": "成年",
                "role": "protagonist",
                "traits": ["坚定"],
                "appearance": "黑发、深色外套",
                "relationships": [],
                "voice_notes": "克制",
            },
        },
    )
    assert ready.status_code == 201
    return ready.json()


def test_reference_image_requires_ready_asset_and_is_version_bound(client: TestClient) -> None:
    project = client.post("/api/v1/novel-projects", json={"title": "参考图门禁"}).json()
    client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", "第一章\n主角拿到旧信。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project['id']}/story-bible/generate").raise_for_status()
    asset = client.post(f"/api/v1/novel-projects/{project['id']}/assets/sync").json()[0]

    blocked = client.post(f"/api/v1/assets/{asset['id']}/reference-images", json={})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ASSET_NOT_READY"


def test_mock_reference_image_task_and_idempotency(client: TestClient) -> None:
    asset = create_ready_character(client)
    endpoint = f"/api/v1/assets/{asset['id']}/reference-images"
    first = client.post(
        endpoint,
        json={"style": "cinematic", "provider_profile_id": "image.mock"},
        headers={"Idempotency-Key": "ref-1"},
    )
    second = client.post(
        endpoint,
        json={"style": "cinematic", "provider_profile_id": "image.mock"},
        headers={"Idempotency-Key": "ref-1"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    task = wait_for_task(client, first.json()["id"])
    assert task["status"] == "succeeded"
    assert task["kind"] == "asset_reference_image"
    assert task["current_stage"] is None
    assert task["input_data"]["provider_profile_id"] == "image.mock"
    assert task["artifacts"][0]["type"] == "reference_image"

    images = client.get(endpoint)
    assert images.status_code == 200
    assert len(images.json()) == 1
    image = images.json()[0]
    assert image["status"] == "succeeded"
    assert image["asset_key"] == asset["asset_key"]
    assert image["asset_version"] == asset["version"]
    assert image["output_uri"].startswith("mock://reference-images/")

    loaded = client.get(f"/api/v1/reference-images/{image['id']}")
    assert loaded.status_code == 200
    assert loaded.json()["task_id"] == first.json()["id"]


def test_reference_image_does_not_reuse_an_asset_version(client: TestClient) -> None:
    asset = create_ready_character(client)
    first = client.post(f"/api/v1/assets/{asset['id']}/reference-images", json={}).json()
    wait_for_task(client, first["id"])

    version = client.post(
        f"/api/v1/assets/{asset['id']}/versions",
        json={
            "status": "ready",
            "content": {
                "age_range": "成年",
                "role": "protagonist",
                "traits": ["坚定", "谨慎"],
                "appearance": "黑发、浅色风衣、旧表",
                "relationships": [],
                "voice_notes": "克制",
            },
        },
    ).json()
    second = client.post(f"/api/v1/assets/{version['id']}/reference-images", json={}).json()
    wait_for_task(client, second["id"])

    old_images = client.get(f"/api/v1/assets/{asset['id']}/reference-images").json()
    new_images = client.get(f"/api/v1/assets/{version['id']}/reference-images").json()
    assert [image["asset_version"] for image in old_images] == [2]
    assert [image["asset_version"] for image in new_images] == [3]
    assert old_images[0]["output_uri"] != new_images[0]["output_uri"]
