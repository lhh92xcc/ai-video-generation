from __future__ import annotations

import asyncio
import sys

from app.media.identity_audit import IdentityConsistencyAuditor, _parse_json_object


def test_identity_audit_parser_accepts_last_json_diagnostic_line() -> None:
    payload = _parse_json_object(b"diagnostic\n{\"status\":\"no_face\"}\n")

    assert payload == {"status": "no_face"}


def test_identity_audit_parser_rejects_non_object_output() -> None:
    assert _parse_json_object(b"not-json") is None
    assert _parse_json_object(b"[]") is None


def test_identity_audit_reports_missing_python_as_unavailable() -> None:
    async def exercise() -> None:
        report = await IdentityConsistencyAuditor(
            python_path="/tmp/ai-video-python-that-does-not-exist",
        ).audit(b"reference", "image/png", b"video", "video/mp4")

        assert report["status"] == "unavailable"
        assert report["reason"] == "identity_audit_python_not_found"
        assert report["human_review_required"] is True

    asyncio.run(exercise())

def test_identity_audit_reports_missing_script_and_model_as_unavailable(tmp_path) -> None:
    async def exercise() -> None:
        missing_script = await IdentityConsistencyAuditor(
            python_path=sys.executable,
            script_path=str(tmp_path / "missing-helper.py"),
        ).audit(b"reference", "image/png", b"video", "video/mp4")
        assert missing_script["status"] == "unavailable"
        assert missing_script["reason"] == "identity_audit_script_not_found"

        helper = tmp_path / "helper.py"
        helper.write_text("print('{}')\n", encoding="utf-8")
        missing_model = await IdentityConsistencyAuditor(
            python_path=sys.executable,
            script_path=str(helper),
            model_root=str(tmp_path / "missing-model-root"),
        ).audit(b"reference", "image/png", b"video", "video/mp4")
        assert missing_model["status"] == "unavailable"
        assert missing_model["reason"] == "identity_audit_model_root_not_found"

    asyncio.run(exercise())


def test_identity_audit_runs_configured_helper_and_preserves_report(tmp_path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text(
        "import json\n"
        "print(json.dumps({'status': 'passed', 'min_similarity': 0.82}))\n",
        encoding="utf-8",
    )

    async def exercise() -> None:
        report = await IdentityConsistencyAuditor(
            python_path=sys.executable,
            script_path=str(helper),
            model_root=str(tmp_path),
        ).audit(b"reference", "image/png", b"video", "video/mp4")

        assert report == {"status": "passed", "min_similarity": 0.82}

    asyncio.run(exercise())
