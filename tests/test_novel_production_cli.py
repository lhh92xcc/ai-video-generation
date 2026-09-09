from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts/run-novel-production.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("run_novel_production", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_creates_project_uploads_novel_and_starts_full_run(tmp_path: Path, capsys) -> None:
    module = _load_cli_module()
    novel_path = tmp_path / "demo.md"
    novel_path.write_text("# 第一章\n雨夜里有人敲门。", encoding="utf-8")
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, {}))
        if request.url.path == "/api/v1/novel-projects":
            payload = json.loads(request.content)
            calls[-1] = (request.method, request.url.path, payload)
            return httpx.Response(201, json={"id": "project-123"})
        if request.url.path == "/api/v1/novel-projects/project-123/sources":
            assert b"demo.md" in request.content
            return httpx.Response(201, json={"id": "source-123"})
        if request.url.path == "/api/v1/novel-projects/project-123/production-runs":
            payload = json.loads(request.content)
            calls[-1] = (request.method, request.url.path, payload)
            assert request.headers["Idempotency-Key"] == "novel-production-fixed"
            assert payload["include_reference_images"] is True
            assert payload["include_narration"] is True
            assert payload["include_subtitles"] is True
            assert payload["include_video"] is True
            assert payload["include_assembly"] is True
            assert payload["visual_quality_profile_id"] == "local_safe"
            return httpx.Response(
                202,
                json={
                    "project_id": "project-123",
                    "run_id": "run-123",
                    "status": "active",
                    "stage": "novel_story_bible",
                    "task_ids": [],
                    "auto_advance": True,
                    "message": "已启动",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        args = module._parser().parse_args(
            [
                "--base-url",
                "https://example.test",
                "--novel",
                str(novel_path),
                "--title",
                "短剧 Demo",
                "--episodes",
                "1",
                "--episode-duration",
                "60",
                "--quality-profile",
                "local_safe",
                "--idempotency-key",
                "novel-production-fixed",
                "--no-wait",
            ]
        )
        assert module.run(args, client=client) == 0
    finally:
        client.close()

    output = capsys.readouterr().out
    assert "Run ID：run-123" in output
    assert [path for _, path, _ in calls] == [
        "/api/v1/novel-projects",
        "/api/v1/novel-projects/project-123/sources",
        "/api/v1/novel-projects/project-123/production-runs",
    ]
    assert calls[0][2]["target_episode_duration_seconds"] == 60


def test_cli_stops_at_human_review_gate_and_prints_resume_context(capsys) -> None:
    module = _load_cli_module()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "project_id": "project-123",
                    "run_id": "run-123",
                    "status": "active",
                    "stage": "asset_reference_image",
                    "task_ids": [],
                    "auto_advance": True,
                    "message": "等待审核",
                },
            )
        return httpx.Response(
            200,
            json={
                "project_id": "project-123",
                "run_id": "run-123",
                "status": "blocked",
                "stage": "asset_reference_image",
                "task_ids": [],
                "auto_advance": True,
                "message": "等待资产审核",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        args = module._parser().parse_args(
            [
                "--project-id",
                "project-123",
                "--idempotency-key",
                "resume-key",
                "--poll-interval",
                "0.01",
            ]
        )
        assert module.run(args, client=client) == module.BLOCKED_EXIT_CODE
    finally:
        client.close()

    output = capsys.readouterr()
    assert calls == [
        "/api/v1/novel-projects/project-123/production-runs",
        "/api/v1/novel-projects/project-123/production-runs/run-123",
    ]
    assert "--project-id project-123" in output.err
    assert "--idempotency-key resume-key" in output.err


def test_cli_rejects_novel_and_project_id_together(tmp_path: Path) -> None:
    module = _load_cli_module()
    novel_path = tmp_path / "demo.txt"
    novel_path.write_text("内容", encoding="utf-8")
    args = module._parser().parse_args(
        ["--novel", str(novel_path), "--project-id", "project-123"]
    )

    try:
        module.run(args)
    except module.ProductionCliError as exc:
        assert "不要同时提供" in str(exc)
    else:
        raise AssertionError("expected conflicting CLI arguments to fail")


def test_cli_controls_existing_run_without_starting_a_new_run(capsys) -> None:
    module = _load_cli_module()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "project_id": "project-123",
                    "run_id": "run-123",
                    "status": "paused",
                    "stage": "asset_reference_image",
                    "message": "等待审核",
                },
            )
        return httpx.Response(
            202,
            json={
                "project_id": "project-123",
                "run_id": "run-123",
                "status": "paused",
                "stage": "asset_reference_image",
                "message": "已暂停",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        for action in ("pause", "resume", "cancel"):
            args = module._parser().parse_args(
                [
                    "--base-url",
                    "https://example.test",
                    "--project-id",
                    "project-123",
                    "--run-id",
                    "run-123",
                    "--control",
                    action,
                ]
            )
            assert module.run(args, client=client) == 0

        status_args = module._parser().parse_args(
            [
                "--base-url",
                "https://example.test",
                "--project-id",
                "project-123",
                "--run-id",
                "run-123",
                "--control",
                "status",
            ]
        )
        assert module.run(status_args, client=client) == 0
    finally:
        client.close()

    assert calls == [
        ("POST", "/api/v1/novel-projects/project-123/production-runs/run-123/pause"),
        ("POST", "/api/v1/novel-projects/project-123/production-runs/run-123/resume"),
        ("POST", "/api/v1/novel-projects/project-123/production-runs/run-123/cancel"),
        ("GET", "/api/v1/novel-projects/project-123/production-runs/run-123"),
    ]
    assert "Run 操作已提交：cancel" in capsys.readouterr().out


def test_cli_rejects_incomplete_run_control_arguments() -> None:
    module = _load_cli_module()
    args = module._parser().parse_args(
        ["--project-id", "project-123", "--control", "pause"]
    )

    try:
        module.run(args)
    except module.ProductionCliError as exc:
        assert "--project-id 和 --run-id" in str(exc)
    else:
        raise AssertionError("expected incomplete Run control arguments to fail")
