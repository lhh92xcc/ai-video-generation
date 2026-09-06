from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from app.auth.identity import sign_identity_headers
from app.config import load_settings
from app.domain.models import (
    NovelProjectRecord,
    ProjectInvitationRecord,
    ProjectRole,
    utc_now,
)
from app.main import create_app
from app.repositories.in_memory import InMemoryStore


def test_local_identity_and_project_membership_are_visible(client: TestClient) -> None:
    headers = {"X-Actor-Id": "owner-1", "X-Actor-Name": "Owner One"}
    project_response = client.post(
        "/api/v1/novel-projects",
        json={"title": "权限测试项目"},
        headers=headers,
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    access_response = client.get(f"/api/v1/novel-projects/{project_id}/access", headers=headers)
    assert access_response.status_code == 200
    assert access_response.json()["actor_id"] == "owner-1"
    assert access_response.json()["role"] == "owner"
    assert "project:manage_members" in access_response.json()["permissions"]

    members_response = client.get(f"/api/v1/novel-projects/{project_id}/members", headers=headers)
    assert members_response.status_code == 200
    assert members_response.json()["items"][0]["actor_id"] == "owner-1"


def test_project_owner_can_assign_viewer_but_viewer_cannot_manage_members(client: TestClient) -> None:
    owner_headers = {"X-Actor-Id": "owner-2", "X-Actor-Name": "Owner Two"}
    project_id = client.post(
        "/api/v1/novel-projects",
        json={"title": "成员管理测试"},
        headers=owner_headers,
    ).json()["id"]

    assign_response = client.put(
        f"/api/v1/novel-projects/{project_id}/members/viewer-1",
        json={"actor_name": "Viewer One", "role": "viewer"},
        headers=owner_headers,
    )
    assert assign_response.status_code == 200

    viewer_headers = {"X-Actor-Id": "viewer-1", "X-Actor-Name": "Viewer One"}
    access_response = client.get(f"/api/v1/novel-projects/{project_id}/access", headers=viewer_headers)
    assert access_response.status_code == 200
    assert access_response.json()["role"] == "viewer"
    assert access_response.json()["permissions"] == ["project:read"]

    denied_response = client.put(
        f"/api/v1/novel-projects/{project_id}/members/editor-1",
        json={"actor_name": "Editor One", "role": "editor"},
        headers=viewer_headers,
    )
    assert denied_response.status_code == 403
    assert denied_response.json()["error"]["code"] == "PROJECT_PERMISSION_DENIED"


def test_member_mutations_write_audit_logs_and_keep_last_owner(client: TestClient) -> None:
    owner_headers = {"X-Actor-Id": "owner-audit", "X-Actor-Name": "Audit Owner"}
    project_id = client.post(
        "/api/v1/novel-projects",
        json={"title": "成员审计测试"},
        headers=owner_headers,
    ).json()["id"]

    added = client.put(
        f"/api/v1/novel-projects/{project_id}/members/editor-audit",
        json={"actor_name": "Audit Editor", "role": "viewer"},
        headers=owner_headers,
    )
    assert added.status_code == 200

    changed = client.put(
        f"/api/v1/novel-projects/{project_id}/members/editor-audit",
        json={"actor_name": "Audit Editor", "role": "editor"},
        headers=owner_headers,
    )
    assert changed.status_code == 200

    removed = client.delete(
        f"/api/v1/novel-projects/{project_id}/members/editor-audit",
        headers=owner_headers,
    )
    assert removed.status_code == 204

    last_owner_change = client.put(
        f"/api/v1/novel-projects/{project_id}/members/owner-audit",
        json={"actor_name": "Audit Owner", "role": "editor"},
        headers=owner_headers,
    )
    assert last_owner_change.status_code == 409
    assert last_owner_change.json()["error"]["code"] == "PROJECT_OWNER_REQUIRED"

    audit_response = client.get(f"/api/v1/novel-projects/{project_id}/audit-logs", headers=owner_headers)
    assert audit_response.status_code == 200
    member_logs = [item for item in audit_response.json()["items"] if item["entity_type"] == "project_member"]
    assert [item["action"] for item in member_logs[:3]] == [
        "project_member_removed",
        "project_member_role_changed",
        "project_member_added",
    ]
    assert member_logs[0]["metadata"]["member_actor_id"] == "editor-audit"


def test_member_update_cannot_demote_the_last_other_owner(client: TestClient) -> None:
    owner_headers = {"X-Actor-Id": "owner-boundary", "X-Actor-Name": "Boundary Owner"}
    project_id = client.post(
        "/api/v1/novel-projects",
        json={"title": "最后 owner 边界"},
        headers=owner_headers,
    ).json()["id"]
    response = client.put(
        f"/api/v1/novel-projects/{project_id}/members/owner-boundary",
        json={"actor_name": "Boundary Owner", "role": "viewer"},
        headers=owner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_OWNER_REQUIRED"


def test_owner_can_create_and_invitee_can_accept_one_time_invitation(client: TestClient) -> None:
    owner_headers = {"X-Actor-Id": "owner-invite", "X-Actor-Name": "Invite Owner"}
    invitee_headers = {"X-Actor-Id": "invitee-01", "X-Actor-Name": "Invitee One"}
    project_id = client.post(
        "/api/v1/novel-projects",
        json={"title": "邀请闭环测试"},
        headers=owner_headers,
    ).json()["id"]

    created = client.post(
        f"/api/v1/novel-projects/{project_id}/invitations",
        json={
            "invitee_actor_id": "invitee-01",
            "invitee_name": "Invitee One",
            "role": "reviewer",
            "expires_in_seconds": 3600,
        },
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["invitation"]["status"] == "pending"
    assert "token_hash" not in body["invitation"]
    token = body["accept_token"]

    duplicate = client.post(
        f"/api/v1/novel-projects/{project_id}/invitations",
        json={"invitee_actor_id": "invitee-01", "invitee_name": "Invitee One", "role": "viewer"},
        headers=owner_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROJECT_INVITATION_CONFLICT"

    wrong_actor = client.post(
        f"/api/v1/project-invitations/{body['invitation']['id']}/accept",
        json={"token": token},
        headers={"X-Actor-Id": "other-01", "X-Actor-Name": "Other One"},
    )
    assert wrong_actor.status_code == 403
    assert wrong_actor.json()["error"]["code"] == "PROJECT_INVITATION_INVALID"

    accepted = client.post(
        f"/api/v1/project-invitations/{body['invitation']['id']}/accept",
        json={"token": token},
        headers=invitee_headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["member"]["role"] == "reviewer"
    assert accepted.json()["invitation"]["status"] == "accepted"

    repeated = client.post(
        f"/api/v1/project-invitations/{body['invitation']['id']}/accept",
        json={"token": token},
        headers=invitee_headers,
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "PROJECT_INVITATION_NOT_PENDING"

    access = client.get(f"/api/v1/novel-projects/{project_id}/access", headers=invitee_headers)
    assert access.status_code == 200
    assert access.json()["role"] == "reviewer"

    invitations = client.get(
        f"/api/v1/novel-projects/{project_id}/invitations",
        headers=owner_headers,
    )
    assert invitations.status_code == 200
    assert invitations.json()["items"][0]["status"] == "accepted"

    audit_logs = client.get(
        f"/api/v1/novel-projects/{project_id}/audit-logs",
        params={"entity_type": "project_invitation"},
        headers=owner_headers,
    )
    assert audit_logs.status_code == 200
    assert {item["action"] for item in audit_logs.json()["items"]} == {
        "project_invitation_created",
        "project_invitation_accepted",
    }


def test_owner_can_revoke_pending_invitation_and_only_owner_can_list(client: TestClient) -> None:
    owner_headers = {"X-Actor-Id": "owner-revoke", "X-Actor-Name": "Revoke Owner"}
    viewer_headers = {"X-Actor-Id": "viewer-revoke", "X-Actor-Name": "Revoke Viewer"}
    project_id = client.post(
        "/api/v1/novel-projects",
        json={"title": "撤销邀请测试"},
        headers=owner_headers,
    ).json()["id"]
    created = client.post(
        f"/api/v1/novel-projects/{project_id}/invitations",
        json={"invitee_actor_id": "viewer-revoke", "invitee_name": "Revoke Viewer", "role": "viewer"},
        headers=owner_headers,
    )
    invitation_id = created.json()["invitation"]["id"]
    token = created.json()["accept_token"]
    member_setup = client.put(
        f"/api/v1/novel-projects/{project_id}/members/viewer-revoke",
        json={"actor_name": "Revoke Viewer", "role": "viewer"},
        headers=owner_headers,
    )
    assert member_setup.status_code == 200

    denied_list = client.get(f"/api/v1/novel-projects/{project_id}/invitations", headers=viewer_headers)
    assert denied_list.status_code == 403

    revoked = client.delete(
        f"/api/v1/novel-projects/{project_id}/invitations/{invitation_id}",
        headers=owner_headers,
    )
    assert revoked.status_code == 204
    accepted = client.post(
        f"/api/v1/project-invitations/{invitation_id}/accept",
        json={"token": token},
        headers=viewer_headers,
    )
    assert accepted.status_code == 409
    assert accepted.json()["error"]["code"] == "PROJECT_INVITATION_NOT_PENDING"


def test_in_memory_invitation_acceptance_is_atomic_under_concurrent_attempts() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        project = await store.create_novel_project(
            NovelProjectRecord(
                title="并发邀请测试",
                language="zh-CN",
                target_episode_count=1,
                target_episode_duration_seconds=30,
            )
        )
        token_hash = hashlib.sha256("concurrent-token".encode("utf-8")).hexdigest()
        invitation = await store.create_novel_project_invitation(
            ProjectInvitationRecord(
                project_id=project.id,
                invitee_actor_id="concurrent-invitee",
                invitee_name="Concurrent Invitee",
                role=ProjectRole.EDITOR,
                token_hash=token_hash,
                expires_at=utc_now() + timedelta(hours=1),
                invited_by_actor_id="owner-concurrent",
                invited_by_name="Concurrent Owner",
            )
        )

        results = await asyncio.gather(
            *[
                store.accept_novel_project_invitation(
                    invitation.id,
                    token_hash,
                    "concurrent-invitee",
                    utc_now(),
                )
                for _ in range(2)
            ]
        )

        assert sum(result is not None for result in results) == 1
        saved_invitation = await store.get_novel_project_invitation(invitation.id)
        saved_member = await store.get_novel_project_member(project.id, "concurrent-invitee")
        assert saved_invitation is not None
        assert saved_invitation.status.value == "accepted"
        assert saved_member is not None
        assert saved_member.role == ProjectRole.EDITOR

    asyncio.run(exercise())


def test_signed_header_mode_rejects_unsigned_requests_and_accepts_gateway_identity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AI_VIDEO_AUTH_MODE", "signed_header")
    monkeypatch.setenv("AI_VIDEO_AUTH_SHARED_SECRET", "test-shared-secret")
    with TestClient(create_app()) as signed_client:
        unsigned = signed_client.get("/api/v1/auth/me")
        assert unsigned.status_code == 401
        assert unsigned.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        path = "/api/v1/novel-projects"
        headers = sign_identity_headers(
            secret="test-shared-secret",
            method="POST",
            path=path,
            actor_id="signed-owner",
            actor_name="Signed Owner",
        )
        created = signed_client.post(path, json={"title": "签名身份项目"}, headers=headers)
        assert created.status_code == 201

        invalid_headers = dict(headers)
        invalid_headers["X-Auth-Signature"] = "0" * 64
        invalid = signed_client.get("/api/v1/auth/me", headers=invalid_headers)
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "AUTHENTICATION_INVALID"


def test_artifact_routes_require_project_access_before_download_url() -> None:
    settings = replace(
        load_settings(),
        auth_mode="signed_header",
        auth_shared_secret="artifact-access-test-secret",
    )
    with TestClient(create_app(settings)) as signed_client:
        create_path = "/api/v1/projects"
        owner_headers = sign_identity_headers(
            secret="artifact-access-test-secret",
            method="POST",
            path=create_path,
            actor_id="signed-owner",
            actor_name="Signed Owner",
        )
        project_response = signed_client.post(
            create_path,
            json={"title": "Artifact 权限测试", "topic": "权限化下载"},
            headers=owner_headers,
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        generation_path = f"/api/v1/projects/{project_id}/generations"
        generation_response = signed_client.post(
            generation_path,
            headers=sign_identity_headers(
                secret="artifact-access-test-secret",
                method="POST",
                path=generation_path,
                actor_id="signed-owner",
                actor_name="Signed Owner",
            ),
        )
        assert generation_response.status_code == 202
        task_id = generation_response.json()["id"]

        task_path = f"/api/v1/tasks/{task_id}"
        deadline = time.monotonic() + 2
        artifact_id = None
        while time.monotonic() < deadline:
            task_response = signed_client.get(
                task_path,
                headers=sign_identity_headers(
                    secret="artifact-access-test-secret",
                    method="GET",
                    path=task_path,
                    actor_id="signed-owner",
                    actor_name="Signed Owner",
                ),
            )
            artifacts = task_response.json().get("artifacts", [])
            artifact_id = artifacts[0].get("id") if artifacts else None
            if artifact_id:
                break
            time.sleep(0.02)
        assert artifact_id is not None

        artifact_path = f"/api/v1/artifacts/{artifact_id}"
        denied_get = signed_client.get(
            artifact_path,
            headers=sign_identity_headers(
                secret="artifact-access-test-secret",
                method="GET",
                path=artifact_path,
                actor_id="signed-outsider",
                actor_name="Signed Outsider",
            ),
        )
        assert denied_get.status_code == 403
        assert denied_get.json()["error"]["code"] == "PROJECT_PERMISSION_DENIED"

        content_path = f"/api/v1/artifacts/{artifact_id}/content"
        denied_content = signed_client.get(
            content_path,
            headers=sign_identity_headers(
                secret="artifact-access-test-secret",
                method="GET",
                path=content_path,
                actor_id="signed-outsider",
                actor_name="Signed Outsider",
            ),
        )
        assert denied_content.status_code == 403
        assert denied_content.json()["error"]["code"] == "PROJECT_PERMISSION_DENIED"

        artifact_list_path = "/api/v1/artifacts"
        denied_list = signed_client.get(
            artifact_list_path,
            params={"project_id": project_id},
            headers=sign_identity_headers(
                secret="artifact-access-test-secret",
                method="GET",
                path=artifact_list_path,
                actor_id="signed-outsider",
                actor_name="Signed Outsider",
            ),
        )
        assert denied_list.status_code == 403
