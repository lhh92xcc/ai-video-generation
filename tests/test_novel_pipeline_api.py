from __future__ import annotations

from copy import deepcopy
import time

from fastapi.testclient import TestClient


NOVEL_TEXT = """序言\n\n第一章 雨夜\n主角在雨中醒来。\n\n第二章 线索\n主角找到一封旧信。\n\n第三章 追问\n主角开始寻找真相。\n"""


def test_story_bible_to_episode_script_and_shots(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "连续剧链路", "target_episode_count": 3},
    ).json()
    project_id = project["id"]
    upload = client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", NOVEL_TEXT.encode("utf-8"), "text/plain")},
        data={"rights_status": "confirmed"},
    )
    assert upload.status_code == 201

    bible = client.post(
        f"/api/v1/novel-projects/{project_id}/story-bible/generate"
    )
    assert bible.status_code == 201

    assets = client.post(f"/api/v1/novel-projects/{project_id}/assets/sync")
    assert assets.status_code == 201
    for asset in assets.json():
        if asset["asset_type"] in {"character", "location"}:
            approved = client.post(
                f"/api/v1/assets/{asset['id']}/reviews",
                json={
                    "status": "ready",
                    "reviewer": "pipeline-test",
                    "comment": "测试资产审核通过",
                },
            )
            assert approved.status_code == 201, approved.text

    plan = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan")
    assert plan.status_code == 201
    episodes = plan.json()
    assert len(episodes) == 3
    assert [episode["episode_number"] for episode in episodes] == [1, 2, 3]
    assert all(episode["status"] == "planned" for episode in episodes)
    assert all(episode["outline"]["source_chapter_numbers"] for episode in episodes)

    listed = client.get(f"/api/v1/novel-projects/{project_id}/episodes")
    assert listed.status_code == 200
    assert len(listed.json()) == 3

    episode_id = episodes[0]["id"]
    script = client.post(f"/api/v1/episodes/{episode_id}/script/generate")
    assert script.status_code == 201
    script_body = script.json()
    assert script_body["version"] == 1
    assert len(script_body["content"]["scenes"]) == 3
    assert [scene["scene_index"] for scene in script_body["content"]["scenes"]] == [1, 2, 3]
    assert sum(
        scene["duration_seconds"] for scene in script_body["content"]["scenes"]
    ) == script_body["content"]["total_duration_seconds"]

    script_read = client.get(f"/api/v1/episodes/{episode_id}/script")
    assert script_read.status_code == 200
    assert script_read.json()["id"] == script_body["id"]

    shots = client.post(f"/api/v1/episodes/{episode_id}/shots/generate")
    assert shots.status_code == 201
    shot_body = shots.json()
    assert shot_body["version"] == 1
    assert len(shot_body["shots"]) == 6
    assert [shot["shot_index"] for shot in shot_body["shots"]] == list(range(1, 7))
    assert all(shot["visual_prompt"] for shot in shot_body["shots"])
    assert all(shot["asset_refs"] for shot in shot_body["shots"])
    assert all(shot["unresolved_asset_requirements"] == [] for shot in shot_body["shots"])
    assert all(shot["asset_binding_warnings"] == [] for shot in shot_body["shots"])
    assert all(
        reference["match_kind"] == "name"
        for shot in shot_body["shots"]
        for reference in shot["asset_refs"]
    )

    episode_after = client.get(f"/api/v1/episodes/{episode_id}")
    assert episode_after.status_code == 200
    assert episode_after.json()["status"] == "shots_ready"

    shots_read = client.get(f"/api/v1/episodes/{episode_id}/shots")
    assert shots_read.status_code == 200
    assert shots_read.json()["id"] == shot_body["id"]

    subtitle_response = client.post(
        f"/api/v1/episodes/{episode_id}/subtitles",
        json={
            "language": "zh-CN",
            "cues": [
                {"start_seconds": 0, "end_seconds": 1, "text": "第一句字幕"},
                {"start_seconds": 1.2, "end_seconds": 2.4, "text": "第二句字幕"},
            ],
        },
    )
    assert subtitle_response.status_code == 202, subtitle_response.text
    subtitle_task_id = subtitle_response.json()["id"]
    subtitle_task = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        subtitle_task = client.get(f"/api/v1/tasks/{subtitle_task_id}").json()
        if subtitle_task["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert subtitle_task is not None
    assert subtitle_task["status"] == "succeeded"
    assert subtitle_task["kind"] == "subtitle_srt"
    assert subtitle_task["stages"][0]["stage"] == "subtitle"
    assert subtitle_task["artifacts"][0]["type"] == "subtitle_srt"
    assert subtitle_task["artifacts"][0]["metadata"]["cue_count"] == 2

    alignment_response = client.post(
        f"/api/v1/episodes/{episode_id}/subtitles/align",
        json={
            "language": "zh-CN",
            "text": "第一句旁白。第二句旁白！",
            "audio_duration_seconds": 4,
        },
    )
    assert alignment_response.status_code == 202, alignment_response.text
    alignment_task_id = alignment_response.json()["id"]
    alignment_task = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        alignment_task = client.get(f"/api/v1/tasks/{alignment_task_id}").json()
        if alignment_task["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert alignment_task is not None
    assert alignment_task["status"] == "succeeded"
    assert alignment_task["kind"] == "subtitle_align"
    assert alignment_task["artifacts"][0]["type"] == "subtitle_srt"
    assert alignment_task["artifacts"][0]["metadata"]["alignment_precision"] == (
        "sentence_estimate"
    )

    clip_response = client.post(f"/api/v1/episodes/{episode_id}/shots/1/video-clips")
    assert clip_response.status_code == 202, clip_response.text
    clip_task_id = clip_response.json()["id"]
    deadline = time.monotonic() + 2
    clip_task = None
    while time.monotonic() < deadline:
        clip_task = client.get(f"/api/v1/tasks/{clip_task_id}").json()
        if clip_task["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    assert clip_task is not None
    assert clip_task["status"] == "succeeded"
    assert clip_task["kind"] == "video_clip"
    assert clip_task["stages"][0]["stage"] == "video_clip"
    assert clip_task["artifacts"][0]["type"] == "video_clip"
    assert clip_task["artifacts"][0]["metadata"]["output_uri"].startswith(
        "mock://video-clips/"
    )

    second_clip_response = client.post(
        f"/api/v1/episodes/{episode_id}/shots/2/video-clips"
    )
    assert second_clip_response.status_code == 202, second_clip_response.text
    second_clip_task_id = second_clip_response.json()["id"]
    second_clip_task = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        second_clip_task = client.get(f"/api/v1/tasks/{second_clip_task_id}").json()
        if second_clip_task["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert second_clip_task is not None
    assert second_clip_task["status"] == "succeeded"

    render_response = client.post(
        f"/api/v1/episodes/{episode_id}/video-renders",
        json={"clip_task_ids": [clip_task_id, second_clip_task_id]},
    )
    assert render_response.status_code == 409
    assert render_response.json()["error"]["code"] == "VIDEO_ASSEMBLY_ARTIFACT_MISSING"


def test_episode_script_manual_version_save_and_conflict(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "剧本版本编辑", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    episode = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()[0]
    episode_id = episode["id"]
    script = client.post(f"/api/v1/episodes/{episode_id}/script/generate").json()
    edited_content = script["content"]
    edited_content["title"] = "人工编辑后的剧本"

    saved = client.post(
        f"/api/v1/episodes/{episode_id}/script/versions",
        json={"expected_version": 1, "content": edited_content},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["version"] == 2
    assert saved.json()["provider"] == "manual"
    assert saved.json()["content"]["title"] == "人工编辑后的剧本"

    edited_content["scenes"][0]["dialogues"] = []
    saved_narration_only = client.post(
        f"/api/v1/episodes/{episode_id}/script/versions",
        json={"expected_version": 2, "content": edited_content},
    )
    assert saved_narration_only.status_code == 201, saved_narration_only.text
    assert saved_narration_only.json()["version"] == 3

    shots = client.post(f"/api/v1/episodes/{episode_id}/shots/generate")
    assert shots.status_code == 201, shots.text
    first_scene_shots = [shot for shot in shots.json()["shots"] if shot["scene_index"] == 1]
    assert first_scene_shots
    assert all(shot["dialogue_refs"] == [] for shot in first_scene_shots)
    assert all("角色对白" not in shot["audio_requirements"] for shot in first_scene_shots)

    stale = client.post(
        f"/api/v1/episodes/{episode_id}/script/versions",
        json={"expected_version": 2, "content": edited_content},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EPISODE_SCRIPT_VERSION_CONFLICT"


def test_episode_script_draft_autosave_resume_conflict_and_publish_cleanup(
    client: TestClient,
) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "剧本草稿自动保存", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    episode_id = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()[0]["id"]
    script = client.post(f"/api/v1/episodes/{episode_id}/script/generate").json()

    draft_content = dict(script["content"])
    draft_content["title"] = "自动保存中的剧本"
    first = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        json={"expected_revision": 0, "content": draft_content},
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    assert first.json()["base_script_id"] == script["id"]

    recovered = client.get(f"/api/v1/episodes/{episode_id}/script/draft")
    assert recovered.status_code == 200
    assert recovered.json()["content"]["title"] == "自动保存中的剧本"

    draft_content["title"] = "第二次自动保存"
    second = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        json={"expected_revision": 1, "content": draft_content},
    )
    assert second.status_code == 200
    assert second.json()["revision"] == 2

    conflicting_content = dict(draft_content)
    conflicting_content["title"] = "另一个页面的修订"
    stale = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        json={"expected_revision": 1, "content": conflicting_content},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "EPISODE_SCRIPT_DRAFT_CONFLICT"
    assert stale.json()["error"]["details"]["current_revision"] == 2
    assert "title" in stale.json()["error"]["details"]["conflict_paths"]

    published = client.post(
        f"/api/v1/episodes/{episode_id}/script/versions",
        json={"expected_version": 1, "content": draft_content},
    )
    assert published.status_code == 201
    assert published.json()["version"] == 2
    assert client.get(f"/api/v1/episodes/{episode_id}/script/draft").status_code == 404


def test_episode_script_draft_merge_preview_is_read_only_and_conservative(
    client: TestClient,
) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "剧本草稿安全合并", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    episode_id = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()[0]["id"]
    script = client.post(f"/api/v1/episodes/{episode_id}/script/generate").json()
    base_content = deepcopy(script["content"])
    base_request = {
        "base_script_id": script["id"],
        "base_script_version": script["version"],
    }

    server_content = deepcopy(base_content)
    server_content["title"] = "服务端草稿标题"
    saved = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        json={"expected_revision": 0, "content": server_content},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1

    local_content = deepcopy(base_content)
    local_content["logline"] = "页面本地修改的梗概"
    safe_preview = client.post(
        f"/api/v1/episodes/{episode_id}/script/draft/merge-preview",
        json={**base_request, "content": local_content},
    )
    assert safe_preview.status_code == 200, safe_preview.text
    safe_body = safe_preview.json()
    assert safe_body["safe_to_apply"] is True
    assert safe_body["conflict_paths"] == []
    assert {"title", "logline"}.issubset(set(safe_body["mergeable_paths"]))
    assert safe_body["merged_content"]["title"] == "服务端草稿标题"
    assert safe_body["merged_content"]["logline"] == "页面本地修改的梗概"

    unchanged = client.get(f"/api/v1/episodes/{episode_id}/script/draft")
    assert unchanged.status_code == 200
    assert unchanged.json()["revision"] == 1
    assert unchanged.json()["content"]["title"] == "服务端草稿标题"
    assert unchanged.json()["content"]["logline"] == base_content["logline"]

    server_content["scenes"][0]["title"] = "服务端修改的场景"
    updated = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        json={"expected_revision": 1, "content": server_content},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2

    scalar_conflict = deepcopy(base_content)
    scalar_conflict["title"] = "页面本地冲突标题"
    conflict_preview = client.post(
        f"/api/v1/episodes/{episode_id}/script/draft/merge-preview",
        json={**base_request, "content": scalar_conflict},
    )
    assert conflict_preview.status_code == 200, conflict_preview.text
    conflict_body = conflict_preview.json()
    assert conflict_body["safe_to_apply"] is False
    assert conflict_body["merged_content"] is None
    assert conflict_body["conflict_paths"] == ["title"]

    list_conflict = deepcopy(base_content)
    list_conflict["scenes"][0]["action"] = "页面本地修改的动作"
    list_preview = client.post(
        f"/api/v1/episodes/{episode_id}/script/draft/merge-preview",
        json={**base_request, "content": list_conflict},
    )
    assert list_preview.status_code == 200, list_preview.text
    list_body = list_preview.json()
    assert list_body["safe_to_apply"] is False
    assert list_body["merged_content"] is None
    assert list_body["conflict_paths"] == ["scenes"]

    still_unchanged = client.get(f"/api/v1/episodes/{episode_id}/script/draft")
    assert still_unchanged.status_code == 200
    assert still_unchanged.json()["revision"] == 2
    assert still_unchanged.json()["content"]["scenes"][0]["title"] == "服务端修改的场景"


def test_episode_script_impact_report_tracks_stale_shots(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "剧本影响分析", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    episode_id = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()[0]["id"]

    script = client.post(f"/api/v1/episodes/{episode_id}/script/generate").json()
    missing_shots = client.get(f"/api/v1/episodes/{episode_id}/script/impact")
    assert missing_shots.status_code == 200
    assert missing_shots.json()["shot_list_state"] == "missing"
    assert missing_shots.json()["recommended_actions"] == ["generate_shot_list"]

    shots = client.post(f"/api/v1/episodes/{episode_id}/shots/generate")
    assert shots.status_code == 201
    current = client.get(f"/api/v1/episodes/{episode_id}/script/impact")
    assert current.status_code == 200
    assert current.json()["shot_list_state"] == "current"
    assert current.json()["requires_shot_regeneration"] is False
    assert current.json()["affected_shot_count"] == 0
    assert len(current.json()["shots"]) == len(shots.json()["shots"])

    edited_content = dict(script["content"])
    edited_content["title"] = "人工修订后的影响分析剧本"
    saved = client.post(
        f"/api/v1/episodes/{episode_id}/script/versions",
        json={"expected_version": 1, "content": edited_content},
    )
    assert saved.status_code == 201

    stale = client.get(f"/api/v1/episodes/{episode_id}/script/impact")
    assert stale.status_code == 200
    stale_body = stale.json()
    assert stale_body["shot_list_state"] == "stale"
    assert stale_body["requires_shot_regeneration"] is True
    assert stale_body["video_generation_gate"] == "blocked"
    assert stale_body["affected_shot_count"] == len(shots.json()["shots"])
    assert stale_body["recommended_actions"][0] == "regenerate_shot_list"
    assert all(item["requires_regeneration"] for item in stale_body["shots"])


def test_episode_shots_keep_unresolved_asset_requirements(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "未绑定资产", "target_episode_count": 1}
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n内容".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    episodes = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()
    episode_id = episodes[0]["id"]
    client.post(f"/api/v1/episodes/{episode_id}/script/generate").raise_for_status()

    shots = client.post(f"/api/v1/episodes/{episode_id}/shots/generate")
    assert shots.status_code == 201
    assert all(not shot["asset_refs"] for shot in shots.json()["shots"])
    assert all(shot["unresolved_asset_requirements"] for shot in shots.json()["shots"])


def test_asset_review_rebinds_an_existing_shot_list(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "审核后重新绑定", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角在雨夜发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    episode_id = client.post(
        f"/api/v1/novel-projects/{project_id}/episodes/plan"
    ).json()[0]["id"]
    client.post(f"/api/v1/episodes/{episode_id}/script/generate").raise_for_status()

    before = client.post(f"/api/v1/episodes/{episode_id}/shots/generate")
    assert before.status_code == 201, before.text
    assert before.json()["version"] == 1
    assert all(shot["unresolved_asset_requirements"] for shot in before.json()["shots"])

    synced = client.post(f"/api/v1/novel-projects/{project_id}/assets/sync")
    assert synced.status_code == 201, synced.text
    review = client.post(
        f"/api/v1/novel-projects/{project_id}/assets/review-batch",
        json={"reviewer": "rebind-test", "comment": "资产已核对"},
    )
    assert review.status_code == 201, review.text

    after = client.get(f"/api/v1/episodes/{episode_id}/shots")
    assert after.status_code == 200, after.text
    assert after.json()["version"] > before.json()["version"]
    assert all(shot["asset_refs"] for shot in after.json()["shots"])
    assert all(shot["unresolved_asset_requirements"] == [] for shot in after.json()["shots"])
    assert all(shot["asset_binding_warnings"] == [] for shot in after.json()["shots"])


def test_episode_shots_require_script(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects", json={"title": "前置条件", "target_episode_count": 1}
    ).json()
    client.post(
        f"/api/v1/novel-projects/{project['id']}/sources",
        files={"file": ("novel.txt", "第一章\n内容".encode("utf-8"), "text/plain")},
    )
    client.post(f"/api/v1/novel-projects/{project['id']}/story-bible/generate")
    episodes = client.post(f"/api/v1/novel-projects/{project['id']}/episodes/plan").json()

    response = client.post(f"/api/v1/episodes/{episodes[0]['id']}/shots/generate")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EPISODE_SCRIPT_REQUIRED"


def test_episode_shots_bind_ready_assets_by_alias(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "别名绑定", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角在街道发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()

    character = client.post(
        f"/api/v1/novel-projects/{project_id}/assets",
        json={
            "asset_type": "character",
            "name": "Protagonist",
            "aliases": ["主角", " protagonist "],
            "content": {
                "role": "protagonist",
                "traits": ["坚定"],
                "appearance": "黑发、深色外套",
            },
        },
    )
    assert character.status_code == 201, character.text
    location = client.post(
        f"/api/v1/novel-projects/{project_id}/assets",
        json={
            "asset_type": "location",
            "name": "Main Street",
            "aliases": ["主要场景"],
            "content": {
                "description": "夜晚的城市街道",
                "visual_keywords": ["night street"],
            },
        },
    )
    assert location.status_code == 201, location.text
    for asset in (character.json(), location.json()):
        approved = client.post(
            f"/api/v1/assets/{asset['id']}/reviews",
            json={"status": "ready", "reviewer": "alias-test"},
        )
        assert approved.status_code == 201, approved.text

    episode_id = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()[0]["id"]
    client.post(f"/api/v1/episodes/{episode_id}/script/generate").raise_for_status()
    shots = client.post(f"/api/v1/episodes/{episode_id}/shots/generate")
    assert shots.status_code == 201, shots.text
    references = shots.json()["shots"][0]["asset_refs"]
    assert {reference["name"] for reference in references} == {"Protagonist", "Main Street"}
    assert all(reference["match_kind"] == "alias" for reference in references)
    assert all(shot["asset_binding_warnings"] == [] for shot in shots.json()["shots"])


def test_editing_operations_write_audit_logs_and_filter_by_project(client: TestClient) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "审计日志闭环", "target_episode_count": 1},
    ).json()
    project_id = project["id"]
    client.post(
        f"/api/v1/novel-projects/{project_id}/sources",
        files={"file": ("novel.txt", "第一章\n主角发现线索。".encode("utf-8"), "text/plain")},
    ).raise_for_status()
    client.post(f"/api/v1/novel-projects/{project_id}/story-bible/generate").raise_for_status()
    assets = client.post(f"/api/v1/novel-projects/{project_id}/assets/sync").json()
    assert assets
    asset = assets[0]
    episode_id = client.post(f"/api/v1/novel-projects/{project_id}/episodes/plan").json()[0]["id"]
    script = client.post(f"/api/v1/episodes/{episode_id}/script/generate").json()

    draft_content = deepcopy(script["content"])
    draft_content["title"] = "带审计的草稿"
    saved_draft = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        headers={"X-Actor-Id": "editor-01", "X-Actor-Name": "Editor One"},
        json={"expected_revision": 0, "content": draft_content},
    )
    assert saved_draft.status_code == 200, saved_draft.text

    published_content = deepcopy(script["content"])
    published_content["title"] = "已发布版本"
    published = client.post(
        f"/api/v1/episodes/{episode_id}/script/versions",
        headers={"X-Actor-Id": "editor-02", "X-Actor-Name": "Editor Two"},
        json={"expected_version": 1, "content": published_content},
    )
    assert published.status_code == 201, published.text

    second_draft = client.put(
        f"/api/v1/episodes/{episode_id}/script/draft",
        json={"expected_revision": 0, "content": published_content},
    )
    assert second_draft.status_code == 200, second_draft.text
    deleted = client.delete(
        f"/api/v1/episodes/{episode_id}/script/draft",
        headers={"X-Actor-Id": "editor-03", "X-Actor-Name": "Editor Three"},
    )
    assert deleted.status_code == 204

    asset_version = client.post(
        f"/api/v1/assets/{asset['id']}/versions",
        headers={"X-Actor-Id": "asset-editor", "X-Actor-Name": "Asset Editor"},
        json={
            "expected_version": asset["version"],
            "status": asset["status"],
            "content": asset["content"],
            "aliases": asset["aliases"],
            "source_chapter_numbers": asset["source_chapter_numbers"],
        },
    )
    assert asset_version.status_code == 201, asset_version.text

    reviewed = client.post(
        f"/api/v1/assets/{asset['id']}/reviews",
        headers={"X-Actor-Id": "reviewer-01", "X-Actor-Name": "Reviewer One"},
        json={"status": "ready", "reviewer": "内容审核员", "comment": "审计测试"},
    )
    assert reviewed.status_code == 201, reviewed.text

    audit_response = client.get(f"/api/v1/novel-projects/{project_id}/audit-logs")
    assert audit_response.status_code == 200, audit_response.text
    audit_items = audit_response.json()["items"]
    actions = {item["action"] for item in audit_items}
    assert actions == {
        "script_draft_saved",
        "script_draft_deleted",
        "script_version_published",
        "asset_version_created",
        "asset_review_created",
    }
    editor_log = next(item for item in audit_items if item["actor_id"] == "editor-01")
    assert editor_log["actor_name"] == "Editor One"
    assert editor_log["metadata"]["revision"] == 1

    filtered = client.get(
        f"/api/v1/novel-projects/{project_id}/audit-logs",
        params={"entity_type": "episode_script"},
    )
    assert filtered.status_code == 200
    assert all(item["entity_type"] == "episode_script" for item in filtered.json()["items"])

    other_project = client.post(
        "/api/v1/novel-projects",
        json={"title": "无审计记录项目", "target_episode_count": 1},
    ).json()
    other_logs = client.get(f"/api/v1/novel-projects/{other_project['id']}/audit-logs")
    assert other_logs.status_code == 200
    assert other_logs.json()["items"] == []
