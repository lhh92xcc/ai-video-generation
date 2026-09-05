"""Run deterministic script-quality preflight checks for a fixed evaluation run.

This tool does not assign subjective quality scores and never mutates the
original evaluation result. It writes a separate ``quality-preflight.json``
for each case and a ``quality-preflight-summary.json`` for the run.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import tomllib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.domain.models import ScriptContent


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPOSITORY_ROOT / "evaluation" / "fixed-evaluation-set.toml"
CAPTION_REVIEW_LIMIT = 22
HIGH_RISK_TERMS = ("医疗", "金融", "法律", "时政")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_dataset(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    with path.open("rb") as dataset_file:
        dataset = tomllib.load(dataset_file)
    cases = dataset.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation dataset must contain at least one case")
    return dataset, {str(case["id"]): case for case in cases}


def _normalize_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())


def _narrative_text(script: ScriptContent) -> str:
    parts = [script.title, script.hook, script.summary, script.cta]
    for scene in script.scenes:
        parts.extend((scene.voiceover, scene.caption))
    return "\n".join(parts)


def _error_details(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    ]


def _evaluate_case(
    *,
    run_id: str,
    case: dict[str, Any],
    target_duration: int,
    case_dir: Path,
) -> dict[str, Any]:
    case_id = str(case["id"])
    script_path = case_dir / "script-output.json"
    checks: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    hard_gate_passed = True

    def add_check(
        code: str,
        *,
        status: str,
        message: str,
        hard_gate: bool = False,
        details: Any = None,
    ) -> None:
        nonlocal hard_gate_passed
        check: dict[str, Any] = {"status": status, "message": message}
        if hard_gate:
            check["hard_gate"] = True
        if details is not None:
            check["details"] = details
        checks[code] = check
        if status == "fail" and hard_gate:
            hard_gate_passed = False
        if status == "warn":
            warnings.append(f"{code}: {message}")

    if not script_path.exists():
        add_check(
            "SCRIPT_OUTPUT_MISSING",
            status="fail",
            message="script-output.json is missing; manual review cannot start",
            hard_gate=True,
        )
        return _document(
            run_id=run_id,
            case=case,
            target_duration=target_duration,
            hard_gate_passed=False,
            checks=checks,
            warnings=warnings,
            metrics={},
        )

    try:
        payload = json.loads(script_path.read_text(encoding="utf-8"))
        script = ScriptContent.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        details = _error_details(exc) if isinstance(exc, ValidationError) else [str(exc)]
        add_check(
            "SCRIPT_SCHEMA_INVALID",
            status="fail",
            message="script output cannot be validated by ScriptContent",
            hard_gate=True,
            details=details,
        )
        return _document(
            run_id=run_id,
            case=case,
            target_duration=target_duration,
            hard_gate_passed=False,
            checks=checks,
            warnings=warnings,
            metrics={},
        )

    add_check(
        "SCRIPT_SCHEMA_VALID",
        status="pass",
        message="script output passed the Pydantic ScriptContent schema",
        hard_gate=True,
    )

    duration_delta = script.duration_seconds - target_duration
    add_check(
        "TOP_LEVEL_DURATION_EXACT",
        status="pass" if duration_delta == 0 else "fail",
        message=(
            "top-level duration matches the evaluation target"
            if duration_delta == 0
            else f"top-level duration differs from target by {duration_delta} seconds"
        ),
        hard_gate=True,
        details={"returned_seconds": script.duration_seconds, "target_seconds": target_duration},
    )

    scene_duration_total = sum(scene.duration_seconds for scene in script.scenes)
    scene_duration_delta = scene_duration_total - target_duration
    add_check(
        "SCENE_DURATION_EXACT",
        status="pass" if scene_duration_delta == 0 else "fail",
        message=(
            "scene duration total matches the evaluation target"
            if scene_duration_delta == 0
            else f"scene duration total differs from target by {scene_duration_delta} seconds"
        ),
        hard_gate=True,
        details={"scene_total_seconds": scene_duration_total, "target_seconds": target_duration},
    )

    captions = [scene.caption for scene in script.scenes]
    caption_lengths = [len(caption) for caption in captions]
    long_caption_indexes = [index + 1 for index, length in enumerate(caption_lengths) if length > CAPTION_REVIEW_LIMIT]
    add_check(
        "CAPTION_LENGTH_REVIEW",
        status="warn" if long_caption_indexes else "pass",
        message=(
            "all captions are within the 22-character mobile review guideline"
            if not long_caption_indexes
            else "some captions exceed the 22-character mobile review guideline"
        ),
        details={"scene_indexes": long_caption_indexes, "limit": CAPTION_REVIEW_LIMIT},
    )

    required_points = [str(item) for item in case.get("must_include", [])]
    narrative = _normalize_text(_narrative_text(script))
    required_point_checks = [
        {"point": point, "exact_match": bool(_normalize_text(point) and _normalize_text(point) in narrative)}
        for point in required_points
    ]
    missing_points = [item["point"] for item in required_point_checks if not item["exact_match"]]
    add_check(
        "REQUIRED_POINTS_REVIEW",
        status="warn" if missing_points else "pass",
        message=(
            "all required points have an exact normalized text match"
            if not missing_points
            else "some required points need human semantic verification"
        ),
        details={"items": required_point_checks, "missing_exact_matches": missing_points},
    )

    topic = str(case.get("topic", ""))
    high_risk = any(term in topic for term in HIGH_RISK_TERMS)
    if high_risk and not script.risk_notes:
        add_check(
            "RISK_NOTE_REVIEW",
            status="warn",
            message="high-risk topic has no risk_notes; require human fact-checking",
            details={"matched_terms": [term for term in HIGH_RISK_TERMS if term in topic]},
        )
    else:
        add_check(
            "RISK_NOTE_REVIEW",
            status="pass",
            message="risk-note presence is acceptable for this preflight",
        )

    transitions = Counter(scene.transition for scene in script.scenes)
    metrics = {
        "scene_count": len(script.scenes),
        "target_duration_seconds": target_duration,
        "script_duration_seconds": script.duration_seconds,
        "scene_duration_total_seconds": scene_duration_total,
        "duration_delta_seconds": duration_delta,
        "scene_duration_delta_seconds": scene_duration_delta,
        "caption_count": len(captions),
        "caption_max_characters": max(caption_lengths, default=0),
        "caption_average_characters": round(statistics.mean(caption_lengths), 2) if caption_lengths else 0,
        "captions_over_review_limit": len(long_caption_indexes),
        "voiceover_total_characters": sum(len(scene.voiceover) for scene in script.scenes),
        "required_points_count": len(required_points),
        "required_points_exact_matches": len(required_points) - len(missing_points),
        "risk_note_count": len(script.risk_notes),
        "transition_counts": dict(transitions),
    }
    return _document(
        run_id=run_id,
        case=case,
        target_duration=target_duration,
        hard_gate_passed=hard_gate_passed,
        checks=checks,
        warnings=warnings,
        metrics=metrics,
    )


def _document(
    *,
    run_id: str,
    case: dict[str, Any],
    target_duration: int,
    hard_gate_passed: bool,
    checks: dict[str, dict[str, Any]],
    warnings: list[str],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "quality-preflight-v1",
        "run_id": run_id,
        "case_id": str(case["id"]),
        "created_at": _now(),
        "target_duration_seconds": target_duration,
        "hard_gate_passed": hard_gate_passed,
        "checks": checks,
        "metrics": metrics,
        "manual_review": {
            "required": True,
            "score_fields": ["script_structure", "content_accuracy", "shot_alignment"],
            "scores": {"script_structure": 0, "content_accuracy": 0, "shot_alignment": 0},
            "notes": warnings,
        },
    }


def run_preflight(
    run_dir: Path,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    output_dir: Path | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run directory does not exist: {run_dir}")
    dataset, cases_by_id = _load_dataset(dataset_path.resolve())
    selected = [case_id for case_id in sorted(cases_by_id) if not case_ids or case_id in case_ids]
    if case_ids and len(selected) != len(case_ids):
        missing = sorted(case_ids - set(selected))
        raise ValueError(f"unknown case id(s): {', '.join(missing)}")
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("no preflight cases selected")

    target_duration = int(dataset.get("default_duration_seconds", 60))
    destination = (output_dir or run_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    documents: list[dict[str, Any]] = []
    for case_id in selected:
        case_dir = run_dir / case_id
        document = _evaluate_case(
            run_id=run_dir.name,
            case=cases_by_id[case_id],
            target_duration=target_duration,
            case_dir=case_dir,
        )
        documents.append(document)
        output_case_dir = destination / case_id
        output_case_dir.mkdir(parents=True, exist_ok=True)
        (output_case_dir / "quality-preflight.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    failed_checks = Counter(
        code
        for document in documents
        for code, check in document["checks"].items()
        if check["status"] == "fail"
    )
    warning_checks = Counter(
        code
        for document in documents
        for code, check in document["checks"].items()
        if check["status"] == "warn"
    )
    complete_metrics = [document["metrics"] for document in documents if document["metrics"]]
    total_required = sum(int(metrics.get("required_points_count", 0)) for metrics in complete_metrics)
    matched_required = sum(int(metrics.get("required_points_exact_matches", 0)) for metrics in complete_metrics)
    summary = {
        "schema_version": "quality-preflight-summary-v1",
        "run_id": run_dir.name,
        "dataset_version": str(dataset["dataset_version"]),
        "created_at": _now(),
        "case_count": len(documents),
        "hard_gate_passed": sum(1 for document in documents if document["hard_gate_passed"]),
        "hard_gate_failed": sum(1 for document in documents if not document["hard_gate_passed"]),
        "hard_gate_pass_rate": round(
            sum(1 for document in documents if document["hard_gate_passed"]) / len(documents), 4
        ),
        "manual_review_required": len(documents),
        "required_points_exact_match_rate": round(matched_required / total_required, 4)
        if total_required
        else 0,
        "caption_review_case_count": sum(
            1 for document in documents if document["checks"].get("CAPTION_LENGTH_REVIEW", {}).get("status") == "warn"
        ),
        "failed_checks": dict(failed_checks),
        "warning_checks": dict(warning_checks),
        "average_scene_count": round(
            statistics.mean(int(metrics["scene_count"]) for metrics in complete_metrics), 2
        )
        if complete_metrics
        else 0,
        "average_caption_characters": round(
            statistics.mean(float(metrics["caption_average_characters"]) for metrics in complete_metrics), 2
        )
        if complete_metrics
        else 0,
        "output_dir": str(destination),
    }
    (destination / "quality-preflight-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--case-id", action="append", help="Limit to one or more case IDs")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = run_preflight(
        args.run_dir,
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        case_ids=set(args.case_id or []),
        limit=args.limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
