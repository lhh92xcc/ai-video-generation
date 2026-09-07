import json
import importlib.util
from pathlib import Path

import pytest


_RUNNER_PATH = Path(__file__).parents[1] / "scripts/run-local-portfolio-sample.py"
_SPEC = importlib.util.spec_from_file_location("portfolio_runner", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_load_reference_manifest = _MODULE._load_reference_manifest
_reference_prompt = _MODULE._reference_prompt


def test_reference_manifest_resolves_each_named_asset(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact = artifact_root / "reference-images" / "asset-key" / "1" / "image.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"png-fixture")
    manifest = tmp_path / "reference-manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "assets": {"林默": {"storage_key": str(artifact.relative_to(artifact_root))}}}),
        encoding="utf-8",
    )

    resolved = _load_reference_manifest(manifest, artifact_root, ["林默"])

    assert resolved["林默"] == artifact.resolve()


def test_reference_manifest_rejects_missing_asset_or_path_escape(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    manifest = tmp_path / "reference-manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "assets": {"林默": {"storage_key": "../outside.png"}}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="escapes storage root"):
        _load_reference_manifest(manifest, artifact_root, ["林默"])


def test_reference_manifest_supports_relative_artifact_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    artifact_root = Path("artifacts")
    artifact = artifact_root / "reference-images" / "asset-key" / "1" / "image.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"png-fixture")
    manifest = tmp_path / "reference-manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "assets": {"林默": {"storage_key": str(artifact.relative_to(artifact_root))}}}),
        encoding="utf-8",
    )

    resolved = _load_reference_manifest(manifest, artifact_root, ["林默"])

    assert resolved["林默"] == artifact.resolve()


def test_portfolio_reference_prompts_keep_props_in_the_2d_style() -> None:
    prompt = _reference_prompt("铜色怀表")

    assert "clean 2D illustration plate" in prompt
    assert "product photograph" not in prompt
