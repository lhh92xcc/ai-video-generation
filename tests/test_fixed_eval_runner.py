from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from pathlib import Path

import tomllib

import evaluation.run_fixed_eval as fixed_eval
from app.config import load_settings
from app.providers.mock_text import MockTextProvider


def test_fixed_eval_runner_writes_a_case_without_secrets(tmp_path: Path, monkeypatch) -> None:
    settings = load_settings("config/config.example.toml")
    monkeypatch.setattr(fixed_eval, "load_settings", lambda _config: settings)
    monkeypatch.setattr(fixed_eval, "create_text_provider", lambda _settings: MockTextProvider())

    summary = asyncio.run(
        fixed_eval._run(
            Namespace(
                dataset=fixed_eval.DEFAULT_DATASET,
                output_dir=tmp_path,
                config=None,
                run_id="test-run",
                case_id=["science-01"],
                limit=None,
                max_retries=0,
                retry_backoff_seconds=0,
            )
        )
    )

    assert summary["succeeded"] == 1
    result_dir = tmp_path / "test-run" / "science-01"
    result = tomllib.loads((result_dir / "result.toml").read_text(encoding="utf-8"))
    assert result["status"] == "succeeded"
    assert result["quality"]["script_structure"] == 0
    assert result["quality"]["hard_gate_passed"] is False
    assert result["system"]["cost_status"] == "待供应商账单核对"
    assert "api_key" not in json.dumps(result, ensure_ascii=False).lower()
    assert (result_dir / "script-output.json").exists()


def test_fixed_eval_runner_recovers_from_one_transient_failure(tmp_path: Path, monkeypatch) -> None:
    settings = load_settings("config/config.example.toml")

    class RetryOnceProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                from app.providers.errors import TextProviderError

                raise TextProviderError("PROVIDER_UNAVAILABLE", "temporary test failure")
            return await MockTextProvider().generate(request)

        async def close(self) -> None:
            return None

    provider = RetryOnceProvider()
    monkeypatch.setattr(fixed_eval, "load_settings", lambda _config: settings)
    monkeypatch.setattr(fixed_eval, "create_text_provider", lambda _settings: provider)

    summary = asyncio.run(
        fixed_eval._run(
            Namespace(
                dataset=fixed_eval.DEFAULT_DATASET,
                output_dir=tmp_path,
                config=None,
                run_id="retry-run",
                case_id=["science-01"],
                limit=None,
                max_retries=1,
                retry_backoff_seconds=0,
            )
        )
    )

    assert summary["first_pass_succeeded"] == 0
    assert summary["succeeded"] == 1
    assert summary["recovered_after_retry"] == 1
    assert summary["retry_attempts"] == 1
    result_dir = tmp_path / "retry-run" / "science-01"
    result = tomllib.loads((result_dir / "result.toml").read_text(encoding="utf-8"))
    assert result["system"]["retry_count"] == 1
    assert result["system"]["first_attempt_error_code"] == "PROVIDER_UNAVAILABLE"
    assert result["system"]["recovered_after_retry"] is True
    history = json.loads((result_dir / "attempt-history.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in history] == ["failed", "succeeded"]


def test_fixed_eval_runner_repairs_one_schema_failure(tmp_path: Path, monkeypatch) -> None:
    settings = load_settings("config/config.example.toml")

    class RepairProvider:
        def __init__(self) -> None:
            self.generate_calls = 0
            self.repair_calls = 0

        async def generate(self, request):
            self.generate_calls += 1
            from app.providers.errors import TextProviderError

            raise TextProviderError(
                "SCRIPT_INVALID_OUTPUT",
                "scenes.1.voiceover: String should have at least 1 character",
                raw_output='{"scenes":[{"voiceover":""}]}',
                input_tokens=5,
                output_tokens=6,
            )

        async def repair(self, request, *, raw_output, validation_error):
            self.repair_calls += 1
            assert raw_output == '{"scenes":[{"voiceover":""}]}'
            assert "voiceover" in validation_error
            result = await MockTextProvider().generate(request)
            result.input_tokens = 7
            result.output_tokens = 8
            return result

        async def close(self) -> None:
            return None

    provider = RepairProvider()
    monkeypatch.setattr(fixed_eval, "load_settings", lambda _config: settings)
    monkeypatch.setattr(fixed_eval, "create_text_provider", lambda _settings: provider)

    summary = asyncio.run(
        fixed_eval._run(
            Namespace(
                dataset=fixed_eval.DEFAULT_DATASET,
                output_dir=tmp_path,
                config=None,
                run_id="schema-repair-run",
                case_id=["interview-04"],
                limit=None,
                max_retries=0,
                retry_backoff_seconds=0,
                max_schema_repairs=1,
            )
        )
    )

    assert provider.generate_calls == 1
    assert provider.repair_calls == 1
    assert summary["first_pass_succeeded"] == 0
    assert summary["succeeded"] == 1
    assert summary["recovered_after_retry"] == 0
    assert summary["repaired_after_schema_error"] == 1
    assert summary["schema_repair_attempts"] == 1
    result_dir = tmp_path / "schema-repair-run" / "interview-04"
    result = tomllib.loads((result_dir / "result.toml").read_text(encoding="utf-8"))
    assert result["system"]["schema_repair_count"] == 1
    assert result["system"]["repaired_after_schema_error"] is True
    assert result["system"]["first_schema_error_code"] == "SCRIPT_INVALID_OUTPUT"
    assert result["system"]["input_tokens"] == 12
    assert result["system"]["output_tokens"] == 14
    repair_history = json.loads((result_dir / "repair-history.json").read_text(encoding="utf-8"))
    assert repair_history[0]["raw_output"] == '{"scenes":[{"voiceover":""}]}'
    assert repair_history[1]["status"] == "succeeded"
