from __future__ import annotations

from fastapi.testclient import TestClient


NOVEL_TEXT = """序言\n\n第一章 初遇\n主角在雨中醒来。\n\n第二章 决定\n主角决定寻找真相。\n"""


def test_novel_import_chapters_and_story_bible(client: TestClient) -> None:
    project_response = client.post(
        "/api/v1/novel-projects",
        json={"title": "雨夜来信", "target_episode_count": 3},
    )
    assert project_response.status_code == 201
    project = project_response.json()

    source_response = client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", NOVEL_TEXT.encode("utf-8"), "text/plain")},
        data={"rights_status": "pending"},
    )
    assert source_response.status_code == 201
    source = source_response.json()
    assert source["chapter_count"] == 3
    assert source["rights_status"] == "pending"
    assert "content" not in source

    chapters_response = client.get(f"/api/v1/novel-projects/{project['id']}/chapters")
    assert chapters_response.status_code == 200
    chapters = chapters_response.json()
    assert [chapter["chapter_number"] for chapter in chapters] == [0, 1, 2]

    bible_response = client.post(
        f"/api/v1/novel-projects/{project['id']}/story-bible/generate"
    )
    assert bible_response.status_code == 201
    bible = bible_response.json()
    assert bible["provider"] == "mock"
    assert bible["version"] == 1
    assert bible["content"]["source_chapter_numbers"] == [0, 1, 2]

    project_after = client.get(f"/api/v1/novel-projects/{project['id']}").json()
    assert project_after["status"] == "ready"
    assert project_after["story_bible_id"] == bible["id"]


def test_novel_upload_rejects_unsupported_extension(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "格式测试"}
    ).json()

    response = client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NOVEL_INPUT_INVALID"


def test_story_bible_requires_source(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "缺少原文"}
    ).json()

    response = client.post(
        f"/api/v1/novel-projects/{project['id']}/story-bible/generate"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOVEL_SOURCE_REQUIRED"
