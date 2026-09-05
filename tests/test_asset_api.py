from __future__ import annotations

from fastapi.testclient import TestClient


def test_story_bible_assets_sync_and_versioning(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "资产库测试"}
    ).json()
    client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", "第一章\n主角拿到旧信。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project['id']}/story-bible/generate").raise_for_status()

    sync = client.post(f"/api/v1/novel-projects/{project['id']}/assets/sync")
    assert sync.status_code == 201
    assets = sync.json()
    assert {asset["asset_type"] for asset in assets} == {"character", "location", "prop"}
    assert all(asset["version"] == 1 for asset in assets)
    assert all(asset["status"] == "needs_review" for asset in assets)

    listed = client.get(f"/api/v1/novel-projects/{project['id']}/assets")
    assert listed.status_code == 200
    assert len(listed.json()) == 3

    character = next(asset for asset in assets if asset["asset_type"] == "character")
    stable_asset_key = character["asset_key"]
    version = client.post(
        f"/api/v1/assets/{character['id']}/versions",
        json={
            "expected_version": 1,
            "status": "ready",
            "aliases": ["主人公", "Protagonist"],
            "content": {
                "age_range": "成年",
                "role": "protagonist",
                "traits": ["坚定", "敏锐"],
                "appearance": "黑发、深色外套、左手戴旧表",
                "relationships": ["与真相相关"],
                "voice_notes": "低沉克制",
            },
            "source_chapter_numbers": [0, 1],
        },
    )
    assert version.status_code == 201
    assert version.json()["version"] == 2
    assert version.json()["status"] == "ready"
    assert version.json()["asset_key"] == stable_asset_key
    assert version.json()["aliases"] == ["主人公", "Protagonist"]

    stale_version = client.post(
        f"/api/v1/assets/{character['id']}/versions",
        json={
            "expected_version": 1,
            "status": "ready",
            "content": {
                "age_range": "成年",
                "role": "protagonist",
                "traits": ["坚定"],
                "appearance": "黑发、深色外套",
                "relationships": [],
                "voice_notes": "低沉克制",
            },
            "source_chapter_numbers": [0, 1],
        },
    )
    assert stale_version.status_code == 409
    assert stale_version.json()["error"]["code"] == "ASSET_VERSION_CONFLICT"

    latest_characters = client.get(
        f"/api/v1/novel-projects/{project['id']}/assets?asset_type=character"
    ).json()
    assert len(latest_characters) == 1
    assert latest_characters[0]["version"] == 2
    assert latest_characters[0]["content"]["age_range"] == "成年"

    review = client.post(
        f"/api/v1/assets/{version.json()['id']}/reviews",
        json={
            "status": "needs_review",
            "reviewer": "editor-1",
            "comment": "需要补充参考图后再重新确认",
        },
    )
    assert review.status_code == 201
    assert review.json()["asset"]["version"] == 3
    assert review.json()["asset"]["status"] == "needs_review"
    assert review.json()["review"]["from_status"] == "ready"
    assert review.json()["review"]["to_status"] == "needs_review"

    history = client.get(f"/api/v1/assets/{version.json()['id']}/reviews")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["reviewer"] == "editor-1"


def test_manual_prop_asset_requires_story_bible(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "道具前置条件"}
    ).json()
    response = client.post(
        f"/api/v1/novel-projects/{project['id']}/assets",
        json={
            "asset_type": "prop",
            "name": "旧信",
            "content": {
                "purpose": "提供线索",
                "description": "泛黄的信纸",
                "visual_keywords": ["old letter"],
                "continuity_notes": "折痕保持一致",
            },
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STORY_BIBLE_REQUIRED"


def test_batch_review_approves_all_needs_review_assets(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "批量审核资产"}
    ).json()
    client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", "第一章\n主角发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project['id']}/story-bible/generate").raise_for_status()
    client.post(f"/api/v1/novel-projects/{project['id']}/assets/sync").raise_for_status()

    response = client.post(
        f"/api/v1/novel-projects/{project['id']}/assets/review-batch",
        json={"reviewer": "batch-reviewer", "comment": "批量审核通过"},
    )
    assert response.status_code == 201, response.text
    assert len(response.json()) == 3
    assert all(item["asset"]["status"] == "ready" for item in response.json())

    listed = client.get(f"/api/v1/novel-projects/{project['id']}/assets")
    assert listed.status_code == 200
    assert all(asset["status"] == "ready" for asset in listed.json())
