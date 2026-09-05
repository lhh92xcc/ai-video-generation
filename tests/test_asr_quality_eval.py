from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from pathlib import Path

import tomllib

import evaluation.run_asr_quality_eval as asr_eval
from app.config import load_settings
from app.domain.models import SubtitleASRGenerationResult, SubtitleCueRequest
from app.providers.mock_tts import MockTTSProvider
from app.providers.errors_asr import SubtitleASRProviderError


def _args(tmp_path: Path, **overrides: object) -> Namespace:
    values = {
        "dataset": asr_eval.DEFAULT_DATASET,
        "output_dir": tmp_path,
        "config": None,
        "run_id": "test-asr-quality",
        "case_id": None,
        "limit": None,
        "retain_audio": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_asr_quality_dataset_loads_with_unique_case_ids() -> None:
    dataset, cases = asr_eval._load_dataset(asr_eval.DEFAULT_DATASET)

    assert dataset["dataset_version"] == "asr-quality-v1"
    assert len(cases) == 5
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["reference_text"] for case in cases)


def test_asr_quality_runner_does_not_call_services_by_default(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fail_if_called(*args, **kwargs):
        calls.append("called")
        raise AssertionError("external provider must not be created when opt-in is absent")

    monkeypatch.delenv(asr_eval.EXTERNAL_EVAL_ENV, raising=False)
    monkeypatch.setattr(asr_eval, "load_settings", fail_if_called)
    monkeypatch.setattr(asr_eval, "create_tts_provider", fail_if_called)
    monkeypatch.setattr(asr_eval, "create_asr_provider", fail_if_called)

    summary = asyncio.run(asr_eval._run(_args(tmp_path)))

    assert summary["status"] == "skipped"
    assert summary["cases"]["selected"] == 5
    assert calls == []


class _FakeASRProvider:
    def __init__(self, fail_case: str | None = None) -> None:
        self.fail_case = fail_case
        self.calls = 0

    async def transcribe(self, request):
        self.calls += 1
        if self.fail_case and self.fail_case in request.reference_text:
            raise SubtitleASRProviderError("ASR_PROVIDER_RATE_LIMITED", "test rate limit")
        return SubtitleASRGenerationResult(
            cues=[
                SubtitleCueRequest(
                    start_seconds=0,
                    end_seconds=request.audio_duration_seconds,
                    text=request.reference_text or "测试",
                )
            ],
            provider="mock-asr-evaluation",
            model="mock-asr-v1",
            precision="segment_asr",
            duration_ms=3,
            metadata={"audio_inspected": True},
        )

    async def close(self) -> None:
        return None


def test_asr_quality_runner_writes_batch_summary_with_mock_provider(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(asr_eval.EXTERNAL_EVAL_ENV, "1")
    monkeypatch.setattr(asr_eval, "create_tts_provider", lambda _settings: MockTTSProvider())
    monkeypatch.setattr(asr_eval, "create_asr_provider", lambda _settings: _FakeASRProvider())

    # The runner's production guard only rejects the configured mock ASR name;
    # use a test settings replacement while keeping the provider fake/offline.
    from dataclasses import replace

    monkeypatch.setattr(
        asr_eval,
        "load_settings",
        lambda _config: replace(
            load_settings("config/config.example.toml"), asr_provider="siliconflow"
        ),
    )
    summary = asyncio.run(asr_eval._run(_args(tmp_path, limit=2, retain_audio=True)))

    assert summary["status"] == "completed"
    assert summary["cases"]["total"] == 2
    assert summary["cases"]["succeeded"] == 2
    assert (tmp_path / "test-asr-quality" / "summary.json").exists()
    result = json.loads(
        (tmp_path / "test-asr-quality" / "short-01" / "result.json").read_text(encoding="utf-8")
    )
    assert result["quality"]["character_error_rate"] == 0.0
    assert result["quality"]["transcript_text"]
    assert result["audio"]["retained"] is True
    assert result["audio"]["content_type"] == "audio/wav"
    assert "api_key" not in json.dumps(result, ensure_ascii=False).lower()
    assert (tmp_path / "test-asr-quality" / "short-01" / "audio.wav").exists()
    assert (tmp_path / "test-asr-quality" / "short-01" / "human-review.toml").exists()
    parsed_toml = tomllib.loads(
        (tmp_path / "test-asr-quality" / "short-01" / "result.toml").read_text(encoding="utf-8")
    )
    assert parsed_toml["provider"]["alignment_precision"] == "segment_asr"


def test_asr_quality_runner_records_failure_and_continues(
    tmp_path: Path, monkeypatch
) -> None:
    from dataclasses import replace

    monkeypatch.setenv(asr_eval.EXTERNAL_EVAL_ENV, "1")
    monkeypatch.setattr(
        asr_eval,
        "load_settings",
        lambda _config: replace(
            load_settings("config/config.example.toml"), asr_provider="siliconflow"
        ),
    )
    monkeypatch.setattr(asr_eval, "create_tts_provider", lambda _settings: MockTTSProvider())
    monkeypatch.setattr(
        asr_eval,
        "create_asr_provider",
        lambda _settings: _FakeASRProvider(fail_case="固定字幕质量评估样本"),
    )

    summary = asyncio.run(asr_eval._run(_args(tmp_path, limit=2, run_id="failure-run")))

    assert summary["cases"]["total"] == 2
    assert summary["cases"]["succeeded"] == 1
    assert summary["cases"]["failed"] == 1
    failure = json.loads(
        (tmp_path / "failure-run" / "short-01" / "result.json").read_text(encoding="utf-8")
    )
    assert failure["status"] == "failed"
    assert failure["failure"]["error_code"] == "ASR_PROVIDER_RATE_LIMITED"
    assert (tmp_path / "failure-run" / "paragraph-01" / "result.json").exists()
