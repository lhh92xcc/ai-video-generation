from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.compare_asr_quality_runs import compare_runs


def _write_run(
    root: Path,
    *,
    run_id: str,
    provider: str,
    precision: str,
    cer_by_case: dict[str, float],
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir()
    summary = {
        "schema_version": "asr-quality-summary-v1",
        "dataset_version": "asr-quality-v1",
        "run_id": run_id,
        "status": "completed",
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    for index, (case_id, cer) in enumerate(cer_by_case.items(), start=1):
        case_dir = run_dir / case_id
        case_dir.mkdir()
        result = {
            "case_id": case_id,
            "status": "succeeded",
            "provider": {
                "asr_provider": provider,
                "asr_model": "test-model",
            },
            "quality": {
                "character_error_rate": cer,
                "alignment_precision": precision,
                "production_ready": precision in {"segment_asr", "word_boundary"},
                "recommendation": "pass"
                if precision in {"segment_asr", "word_boundary"}
                else "needs_review",
            },
            "system": {"provider_duration_ms": index * 100},
        }
        (case_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    return run_dir


def test_compare_runs_reports_precision_and_cer(tmp_path: Path) -> None:
    sentence_run = _write_run(
        tmp_path,
        run_id="siliconflow-run",
        provider="siliconflow",
        precision="transcript_sentence_estimate",
        cer_by_case={"short-01": 0.0, "mixed-01": 0.1},
    )
    segment_run = _write_run(
        tmp_path,
        run_id="segment-run",
        provider="openai_compatible",
        precision="segment_asr",
        cer_by_case={"short-01": 0.02, "mixed-01": 0.04},
    )

    comparison = compare_runs([sentence_run, segment_run])

    assert comparison["run_count"] == 2
    assert comparison["runs"][0]["precision_counts"] == {
        "transcript_sentence_estimate": 2
    }
    assert comparison["runs"][1]["production_ready"] == 2
    assert comparison["runs"][1]["cer_mean"] == 0.03
    assert comparison["per_case"]["mixed-01"][1]["alignment_precision"] == "segment_asr"


def test_compare_runs_rejects_different_case_sets(tmp_path: Path) -> None:
    first = _write_run(
        tmp_path,
        run_id="first",
        provider="siliconflow",
        precision="transcript_sentence_estimate",
        cer_by_case={"short-01": 0.0},
    )
    second = _write_run(
        tmp_path,
        run_id="second",
        provider="openai_compatible",
        precision="segment_asr",
        cer_by_case={"paragraph-01": 0.0},
    )

    with pytest.raises(ValueError, match="same case IDs"):
        compare_runs([first, second])


def test_compare_runs_rejects_different_dataset_versions(tmp_path: Path) -> None:
    first = _write_run(
        tmp_path,
        run_id="first",
        provider="siliconflow",
        precision="transcript_sentence_estimate",
        cer_by_case={"short-01": 0.0},
    )
    summary_path = first / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["dataset_version"] = "asr-quality-v2"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    second = _write_run(
        tmp_path,
        run_id="second",
        provider="openai_compatible",
        precision="segment_asr",
        cer_by_case={"short-01": 0.0},
    )

    with pytest.raises(ValueError, match="same dataset_version"):
        compare_runs([first, second])
