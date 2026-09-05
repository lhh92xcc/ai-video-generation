"""Compare completed ASR quality runs without calling any external service.

The comparison is intentionally read-only. It requires the compared runs to
use the same dataset version and the same case IDs, then reports technical
success, CER, timing precision, production readiness and Provider latency.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


def _toml_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return '""'
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{json.dumps(str(key), ensure_ascii=False)} = {_toml_value(item)}"
            for key, item in value.items()
        ) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def _write_toml(path: Path, comparison: dict[str, Any]) -> None:
    lines = [
        f"schema_version = {_toml_value(comparison['schema_version'])}",
        f"dataset_version = {_toml_value(comparison['dataset_version'])}",
        f"run_count = {_toml_value(comparison['run_count'])}",
    ]
    for index, run in enumerate(comparison["runs"], start=1):
        lines.extend(
            [
                "",
                f"[run_{index}]",
                f"run_id = {_toml_value(run['run_id'])}",
                f"asr_provider = {_toml_value(run['asr_provider'])}",
                f"asr_model = {_toml_value(run['asr_model'])}",
                f"case_count = {_toml_value(run['case_count'])}",
                f"technical_successes = {_toml_value(run['technical_successes'])}",
                f"technical_failures = {_toml_value(run['technical_failures'])}",
                f"production_ready = {_toml_value(run['production_ready'])}",
                f"cer_mean = {_toml_value(run['cer_mean'])}",
                f"cer_max = {_toml_value(run['cer_max'])}",
                f"provider_duration_mean_ms = {_toml_value(run['provider_duration_mean_ms'])}",
                f"provider_duration_p95_ms = {_toml_value(run['provider_duration_p95_ms'])}",
                f"precision_counts = {_toml_value(run['precision_counts'])}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _load_run(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir).resolve()
    summary = _read_json(path / "summary.json")
    if summary.get("status") != "completed":
        raise ValueError(f"ASR run is not completed: {path}")
    case_results: dict[str, dict[str, Any]] = {}
    for case_dir in sorted(path.iterdir()):
        if not case_dir.is_dir() or not (case_dir / "result.json").exists():
            continue
        result = _read_json(case_dir / "result.json")
        case_id = str(result.get("case_id", case_dir.name))
        if case_id in case_results:
            raise ValueError(f"duplicate case ID in run {path}: {case_id}")
        case_results[case_id] = result
    if not case_results:
        raise ValueError(f"ASR run contains no case results: {path}")

    first_result = next(iter(case_results.values()))
    provider = first_result.get("provider", {})
    if not isinstance(provider, dict):
        raise ValueError(f"invalid provider section in run: {path}")
    return {
        "path": str(path),
        "run_id": str(summary.get("run_id", path.name)),
        "dataset_version": str(summary.get("dataset_version", "")),
        "asr_provider": str(provider.get("asr_provider", "")),
        "asr_model": str(provider.get("asr_model", "")),
        "cases": case_results,
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _run_metrics(run: dict[str, Any]) -> dict[str, Any]:
    results = list(run["cases"].values())
    successful = [result for result in results if result.get("status") == "succeeded"]
    quality_reports = [
        result.get("quality", {})
        for result in successful
        if isinstance(result.get("quality", {}), dict)
    ]
    cer_values = [
        float(report["character_error_rate"])
        for report in quality_reports
        if report.get("character_error_rate") is not None
    ]
    latencies = [
        int(result.get("system", {}).get("provider_duration_ms", 0))
        for result in successful
    ]
    precision_counts: dict[str, int] = {}
    recommendation_counts: dict[str, int] = {}
    for report in quality_reports:
        precision = str(report.get("alignment_precision", "unknown"))
        precision_counts[precision] = precision_counts.get(precision, 0) + 1
        recommendation = str(report.get("recommendation", "failed"))
        recommendation_counts[recommendation] = recommendation_counts.get(recommendation, 0) + 1
    return {
        "run_id": run["run_id"],
        "asr_provider": run["asr_provider"],
        "asr_model": run["asr_model"],
        "case_count": len(results),
        "technical_successes": len(successful),
        "technical_failures": len(results) - len(successful),
        "production_ready": sum(bool(report.get("production_ready")) for report in quality_reports),
        "recommendation_counts": recommendation_counts,
        "precision_counts": precision_counts,
        "cer_mean": round(statistics.mean(cer_values), 6) if cer_values else None,
        "cer_max": round(max(cer_values), 6) if cer_values else None,
        "provider_duration_mean_ms": round(statistics.mean(latencies)) if latencies else 0,
        "provider_duration_p95_ms": _percentile(latencies, 0.95),
    }


def compare_runs(run_dirs: list[str | Path]) -> dict[str, Any]:
    if not run_dirs:
        raise ValueError("at least one ASR run is required")
    runs = [_load_run(run_dir) for run_dir in run_dirs]
    dataset_versions = {run["dataset_version"] for run in runs}
    if len(dataset_versions) != 1:
        raise ValueError("all ASR runs must use the same dataset_version")
    case_sets = [set(run["cases"]) for run in runs]
    if any(case_set != case_sets[0] for case_set in case_sets[1:]):
        raise ValueError("all ASR runs must contain the same case IDs")

    per_case: dict[str, list[dict[str, Any]]] = {}
    for case_id in sorted(case_sets[0]):
        per_case[case_id] = []
        for run in runs:
            result = run["cases"][case_id]
            quality = result.get("quality", {})
            provider = result.get("provider", {})
            per_case[case_id].append(
                {
                    "run_id": run["run_id"],
                    "asr_provider": provider.get("asr_provider", ""),
                    "status": result.get("status", "unknown"),
                    "character_error_rate": quality.get("character_error_rate"),
                    "alignment_precision": quality.get("alignment_precision", "unknown"),
                    "production_ready": quality.get("production_ready", False),
                    "recommendation": quality.get("recommendation", "failed"),
                }
            )
    return {
        "schema_version": "asr-quality-comparison-v1",
        "dataset_version": next(iter(dataset_versions)),
        "run_count": len(runs),
        "runs": [_run_metrics(run) for run in runs],
        "per_case": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-toml", type=Path)
    args = parser.parse_args()
    comparison = compare_runs(args.run_dir)
    rendered = json.dumps(comparison, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if args.output_toml:
        args.output_toml.parent.mkdir(parents=True, exist_ok=True)
        _write_toml(args.output_toml, comparison)


if __name__ == "__main__":
    main()
