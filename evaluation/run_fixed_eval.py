"""Run the fixed-v1 script evaluation without putting secrets in result files.

This runner intentionally evaluates only the LLM script stage. It keeps each
case independent, writes a durable result before moving to the next case, and
leaves human quality scores at zero until a reviewer has inspected the output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings, load_settings
from app.domain.models import ScriptGenerationRequest
from app.providers.errors import TextProviderError
from app.providers.factory import create_text_provider


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPOSITORY_ROOT / "evaluation" / "fixed-evaluation-set.toml"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "evaluation" / "results"
TRANSIENT_PROVIDER_ERRORS = frozenset(
    {
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_HTTP_ERROR",
        "PROVIDER_UNAVAILABLE",
    }
)
STRUCTURED_REPAIR_ERRORS = frozenset({"PROVIDER_INVALID_RESPONSE", "SCRIPT_INVALID_OUTPUT"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _prompt_version() -> str:
    prompt_path = REPOSITORY_ROOT / "prompts" / "script-generation.txt"
    first_line = prompt_path.read_text(encoding="utf-8").splitlines()[0]
    return first_line.removeprefix("PROMPT_VERSION:").strip()


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def _write_toml(path: Path, top_level: dict[str, Any], sections: dict[str, dict[str, Any]]) -> None:
    lines: list[str] = []
    for key, value in top_level.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for section, values in sections.items():
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_settings_snapshot(settings: Settings) -> dict[str, dict[str, Any]]:
    return {
        "run": {"captured_at": _now(), "prompt_version": _prompt_version()},
        "llm": {
            "provider": settings.llm_provider,
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "timeout_seconds": settings.llm_timeout_seconds,
            "api_key_set": bool(settings.llm_api_key),
        },
        "image": {
            "provider": settings.image_provider,
            "base_url": settings.image_base_url,
            "model": settings.image_model,
            "api_key_set": bool(settings.image_api_key),
        },
        "video": {
            "provider": settings.video_provider,
            "base_url": settings.video_base_url,
            "model": settings.video_model,
            "api_key_set": bool(settings.video_api_key),
        },
        "tts": {"provider": settings.tts_provider, "voice": settings.tts_voice},
        "storage": {"provider": settings.storage_provider, "bucket": settings.storage_bucket},
    }


def _recoverable(code: str) -> bool:
    return code in TRANSIENT_PROVIDER_ERRORS


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _load_cases(dataset_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with dataset_path.open("rb") as dataset_file:
        dataset = tomllib.load(dataset_file)
    cases = dataset.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation dataset must contain at least one case")
    return dataset, cases


def _elapsed_ms(started_at: datetime) -> int:
    return max(1, round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))


async def _generate_with_retries(
    provider: Any,
    request: ScriptGenerationRequest,
    *,
    max_retries: int,
    retry_backoff_seconds: float,
) -> tuple[Any | None, list[dict[str, Any]], str, str, bool, TextProviderError | None]:
    """Retry only transient Provider failures with bounded exponential backoff."""

    attempts: list[dict[str, Any]] = []
    for attempt_index in range(max_retries + 1):
        attempt_started = datetime.now(timezone.utc)
        try:
            generated = await provider.generate(request)
            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "status": "succeeded",
                    "duration_ms": _elapsed_ms(attempt_started),
                    "input_tokens": int(getattr(generated, "input_tokens", 0)),
                    "output_tokens": int(getattr(generated, "output_tokens", 0)),
                }
            )
            return generated, attempts, "", "", False, None
        except TextProviderError as exc:
            recoverable = _recoverable(exc.code)
            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "status": "failed",
                    "duration_ms": _elapsed_ms(attempt_started),
                    "error_code": exc.code,
                    "message": exc.message,
                    "recoverable": recoverable,
                    "input_tokens": exc.input_tokens,
                    "output_tokens": exc.output_tokens,
                }
            )
            if not recoverable or attempt_index >= max_retries:
                return None, attempts, exc.code, exc.message, recoverable, exc
        except Exception as exc:  # pragma: no cover - final safety net for batch continuity
            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "status": "failed",
                    "duration_ms": _elapsed_ms(attempt_started),
                    "error_code": "EVALUATION_RUNNER_ERROR",
                    "message": str(exc),
                    "recoverable": False,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            )
            return None, attempts, "EVALUATION_RUNNER_ERROR", str(exc), False, None

        await asyncio.sleep(retry_backoff_seconds * (2**attempt_index))

    raise AssertionError("retry loop must return from every attempt")


def _result_document(
    *,
    dataset: dict[str, Any],
    case: dict[str, Any],
    run_id: str,
    settings: Settings,
    status: str,
    elapsed_ms: int,
    script_duration_ms: int,
    task_success: bool,
    script_output: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
    recoverable: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    retry_count: int = 0,
    first_attempt_error_code: str = "",
    recovered_after_retry: bool = False,
    schema_repair_count: int = 0,
    repaired_after_schema_error: bool = False,
    first_schema_error_code: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": "evaluation-result-v1",
        "dataset_version": str(dataset["dataset_version"]),
        "case_id": str(case["id"]),
        "run_id": run_id,
        "status": status,
        "created_at": _now(),
        "reviewer": "",
        "input": {
            "language": str(dataset.get("language", "zh-CN")),
            "duration_seconds": int(dataset.get("default_duration_seconds", 60)),
            "aspect_ratio": str(dataset.get("default_aspect_ratio", "9:16")),
            "prompt_version": _prompt_version(),
        },
        "provider": {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "video_provider": settings.video_provider,
            "video_model": settings.video_model,
            "tts_provider": settings.tts_provider,
            "tts_voice": settings.tts_voice,
            "materials_provider": "未接入",
        },
        "artifacts": {
            "script_artifact_id": "",
            "shot_list_artifact_id": "",
            "audio_artifact_id": "",
            "subtitle_artifact_id": "",
            "rendered_video_artifact_id": "",
            "preview_path": "",
        },
        "quality": {
            "script_structure": 0,
            "content_accuracy": 0,
            "shot_alignment": 0,
            "asset_consistency": 0,
            "audio_subtitle": 0,
            "deliverability": 0,
            "hard_gate_passed": False,
            "notes": "脚本阶段通过，待全链路和人工评估" if task_success else "脚本阶段失败，待排查",
        },
        "system": {
            "task_success": task_success,
            "retry_count": retry_count,
            "first_attempt_error_code": first_attempt_error_code,
            "recovered_after_retry": recovered_after_retry,
            "schema_repair_count": schema_repair_count,
            "repaired_after_schema_error": repaired_after_schema_error,
            "first_schema_error_code": first_schema_error_code,
            "total_duration_ms": elapsed_ms,
            "script_duration_ms": script_duration_ms,
            "material_duration_ms": 0,
            "tts_duration_ms": 0,
            "subtitle_duration_ms": 0,
            "render_duration_ms": 0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_cny": 0,
            "cost_status": "待供应商账单核对",
        },
        "failure": {
            "error_code": error_code,
            "message": error_message,
            "recoverable": recoverable,
        },
        "script_output": script_output or {},
        "case": {
            "category": str(case.get("category", "")),
            "title": str(case.get("title", "")),
            "topic": str(case.get("topic", "")),
            "evaluation_focus": list(case.get("evaluation_focus", [])),
            "must_include": list(case.get("must_include", [])),
        },
    }


def _write_result_files(
    case_dir: Path,
    result: dict[str, Any],
    input_payload: dict[str, Any],
    config_snapshot: dict[str, dict[str, Any]],
    attempt_history: list[dict[str, Any]],
    repair_history: list[dict[str, Any]],
) -> None:
    (case_dir / "input.json").write_text(
        json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_toml(case_dir / "config-snapshot.toml", {}, config_snapshot)
    (case_dir / "attempt-history.json").write_text(
        json.dumps(attempt_history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (case_dir / "repair-history.json").write_text(
        json.dumps(repair_history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    script_output = result.pop("script_output", None)
    if script_output:
        (case_dir / "script-output.json").write_text(
            json.dumps(script_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    _write_toml(
        case_dir / "result.toml",
        {
            key: value
            for key, value in result.items()
            if key not in {"input", "provider", "artifacts", "quality", "system", "failure", "case"}
        },
        {
            key: value
            for key, value in result.items()
            if key in {"input", "provider", "artifacts", "quality", "system", "failure", "case"}
        },
    )
    (case_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset).resolve()
    output_root = Path(args.output_dir).resolve()
    dataset, all_cases = _load_cases(dataset_path)
    selected_ids = set(args.case_id or [])
    cases = [case for case in all_cases if not selected_ids or case.get("id") in selected_ids]
    if selected_ids and len(cases) != len(selected_ids):
        known_ids = {str(case.get("id")) for case in all_cases}
        missing = sorted(selected_ids - known_ids)
        raise ValueError(f"unknown case id(s): {', '.join(missing)}")
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("no evaluation cases selected")
    if args.max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if args.retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be non-negative")
    max_schema_repairs = int(getattr(args, "max_schema_repairs", 1))
    if max_schema_repairs < 0:
        raise ValueError("max_schema_repairs must be non-negative")

    settings = load_settings(args.config)
    provider = create_text_provider(settings)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = _safe_settings_snapshot(settings)
    durations: list[int] = []
    succeeded = 0
    first_pass_succeeded = 0
    recovered_after_retry = 0
    retry_attempts = 0
    retry_exhausted = 0
    schema_repair_attempts = 0
    repaired_after_schema_error = 0
    schema_repair_exhausted = 0
    first_schema_errors: dict[str, int] = {}
    first_attempt_failures: dict[str, int] = {}
    final_failures: dict[str, int] = {}

    try:
        for case in cases:
            case_id = str(case["id"])
            case_dir = run_dir / case_id
            case_dir.mkdir()
            target_duration = int(dataset.get("default_duration_seconds", 60))
            aspect_ratio = str(dataset.get("default_aspect_ratio", "9:16"))
            language = str(dataset.get("language", "zh-CN"))
            request = ScriptGenerationRequest(
                topic=str(case["topic"]),
                language=language,
                target_duration_seconds=target_duration,
                aspect_ratio=aspect_ratio,
                tone="清晰、口语化",
                required_points=[str(item) for item in case.get("must_include", [])],
            )
            input_payload = {
                "case": case,
                "request": request.model_dump(mode="json"),
                "dataset_path": str(dataset_path),
            }
            started = datetime.now(timezone.utc)
            generated, attempt_history, error_code, error_message, recoverable, failure_error = (
                await _generate_with_retries(
                    provider,
                    request,
                    max_retries=args.max_retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
            )
            repair_history: list[dict[str, Any]] = []
            first_schema_error_code = error_code if error_code in STRUCTURED_REPAIR_ERRORS else ""
            if first_schema_error_code:
                first_schema_errors[first_schema_error_code] = first_schema_errors.get(first_schema_error_code, 0) + 1

            total_input_tokens = sum(int(item.get("input_tokens", 0)) for item in attempt_history)
            total_output_tokens = sum(int(item.get("output_tokens", 0)) for item in attempt_history)
            schema_repair_count = 0
            repaired_case = False
            repair_method = getattr(provider, "repair", None)
            if (
                generated is None
                and failure_error is not None
                and error_code in STRUCTURED_REPAIR_ERRORS
                and callable(repair_method)
                and max_schema_repairs > 0
            ):
                raw_output = failure_error.raw_output or ""
                validation_error = failure_error.message
                repair_history.append(
                    {
                        "attempt": 0,
                        "status": "failed",
                        "phase": "initial",
                        "error_code": failure_error.code,
                        "message": failure_error.message,
                        "raw_output": raw_output,
                        "input_tokens": failure_error.input_tokens,
                        "output_tokens": failure_error.output_tokens,
                    }
                )
                for repair_index in range(max_schema_repairs):
                    repair_started = datetime.now(timezone.utc)
                    schema_repair_count += 1
                    schema_repair_attempts += 1
                    try:
                        repaired = await repair_method(
                            request,
                            raw_output=raw_output,
                            validation_error=validation_error,
                        )
                        total_input_tokens += int(getattr(repaired, "input_tokens", 0))
                        total_output_tokens += int(getattr(repaired, "output_tokens", 0))
                        repair_history.append(
                            {
                                "attempt": repair_index + 1,
                                "status": "succeeded",
                                "phase": "repair",
                                "duration_ms": _elapsed_ms(repair_started),
                                "input_tokens": int(getattr(repaired, "input_tokens", 0)),
                                "output_tokens": int(getattr(repaired, "output_tokens", 0)),
                            }
                        )
                        generated = repaired
                        error_code = ""
                        error_message = ""
                        recoverable = False
                        repaired_case = True
                        break
                    except TextProviderError as exc:
                        total_input_tokens += exc.input_tokens
                        total_output_tokens += exc.output_tokens
                        repair_history.append(
                            {
                                "attempt": repair_index + 1,
                                "status": "failed",
                                "phase": "repair",
                                "duration_ms": _elapsed_ms(repair_started),
                                "error_code": exc.code,
                                "message": exc.message,
                                "raw_output": exc.raw_output or "",
                                "input_tokens": exc.input_tokens,
                                "output_tokens": exc.output_tokens,
                            }
                        )
                        error_code = exc.code
                        error_message = exc.message
                        recoverable = _recoverable(exc.code)
                        raw_output = exc.raw_output or raw_output
                        validation_error = exc.message
                        if exc.code not in STRUCTURED_REPAIR_ERRORS:
                            break
                if not repaired_case:
                    schema_repair_exhausted += 1
            elif failure_error is not None and error_code in STRUCTURED_REPAIR_ERRORS:
                repair_history.append(
                    {
                        "attempt": 0,
                        "status": "skipped",
                        "phase": "initial",
                        "error_code": failure_error.code,
                        "message": failure_error.message,
                        "reason": "provider does not support structured repair or repair limit is zero",
                        "raw_output": failure_error.raw_output or "",
                    }
                )
            if repaired_case:
                repaired_after_schema_error += 1
            elapsed_ms = _elapsed_ms(started)
            retry_count = max(0, len(attempt_history) - 1)
            retry_attempts += retry_count
            first_attempt = attempt_history[0]
            first_error_code = str(first_attempt.get("error_code", ""))
            if first_attempt["status"] == "succeeded":
                first_pass_succeeded += 1
            else:
                first_attempt_failures[first_error_code] = first_attempt_failures.get(first_error_code, 0) + 1
                if generated is not None and not repaired_case:
                    recovered_after_retry += 1
                elif retry_count > 0 and retry_count >= args.max_retries and _recoverable(first_error_code):
                    retry_exhausted += 1

            if generated is not None:
                durations.append(elapsed_ms)
                succeeded += 1
                result = _result_document(
                    dataset=dataset,
                    case=case,
                    run_id=run_id,
                    settings=settings,
                    status="succeeded",
                    elapsed_ms=elapsed_ms,
                    script_duration_ms=elapsed_ms,
                    task_success=True,
                    script_output=generated.content.model_dump(mode="json"),
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    retry_count=retry_count,
                    first_attempt_error_code=first_error_code,
                    recovered_after_retry=generated is not None and retry_count > 0,
                    schema_repair_count=schema_repair_count,
                    repaired_after_schema_error=repaired_case,
                    first_schema_error_code=first_schema_error_code,
                )
            else:
                final_failures[error_code] = final_failures.get(error_code, 0) + 1
                result = _result_document(
                    dataset=dataset,
                    case=case,
                    run_id=run_id,
                    settings=settings,
                    status="failed",
                    elapsed_ms=elapsed_ms,
                    script_duration_ms=elapsed_ms,
                    task_success=False,
                    error_code=error_code,
                    error_message=error_message,
                    recoverable=recoverable,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    retry_count=retry_count,
                    first_attempt_error_code=first_error_code,
                    schema_repair_count=schema_repair_count,
                    repaired_after_schema_error=repaired_case,
                    first_schema_error_code=first_schema_error_code,
                )
            _write_result_files(case_dir, result, input_payload, snapshot, attempt_history, repair_history)
            print(
                json.dumps(
                    {"case_id": case_id, "status": result["status"], "duration_ms": result["system"]["total_duration_ms"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()

    summary = {
        "schema_version": "evaluation-run-summary-v1",
        "run_id": run_id,
        "dataset_version": str(dataset["dataset_version"]),
        "case_count": len(cases),
        "succeeded": succeeded,
        "failed": len(cases) - succeeded,
        "success_rate": round(succeeded / len(cases), 4),
        "first_pass_succeeded": first_pass_succeeded,
        "first_pass_success_rate": round(first_pass_succeeded / len(cases), 4),
        "recovered_after_retry": recovered_after_retry,
        "retry_attempts": retry_attempts,
        "retry_exhausted": retry_exhausted,
        "max_retries": args.max_retries,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "schema_repair_attempts": schema_repair_attempts,
        "repaired_after_schema_error": repaired_after_schema_error,
        "schema_repair_exhausted": schema_repair_exhausted,
        "max_schema_repairs": max_schema_repairs,
        "first_schema_error_codes": first_schema_errors,
        "p50_duration_ms": int(statistics.median(durations)) if durations else 0,
        "p95_duration_ms": _percentile(durations, 0.95),
        "first_attempt_failure_codes": first_attempt_failures,
        "failure_codes": final_failures,
        "final_failure_codes": final_failures,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "prompt_version": _prompt_version(),
        "cost_status": "待供应商账单核对",
        "result_dir": str(run_dir),
    }
    (run_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--case-id", action="append", help="Limit to one or more case IDs")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Maximum retries for transient Provider errors (default: 1)",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Initial exponential backoff between retries (default: 2 seconds)",
    )
    parser.add_argument(
        "--max-schema-repairs",
        type=int,
        default=1,
        help="Maximum structured JSON/schema repair requests per case (default: 1)",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
