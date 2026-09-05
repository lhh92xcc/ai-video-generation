"""Run a fixed, opt-in ASR and subtitle quality evaluation.

The runner creates deterministic test inputs with the configured TTS Provider,
validates each audio file with ffprobe, sends the bytes to the configured ASR
Provider, and evaluates the returned cues locally. It never calls an external
service unless ``AI_VIDEO_RUN_EXTERNAL_ASR_QUALITY_EVAL=1`` is set.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import math
import os
import statistics
import tomllib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from app.config import Settings, load_settings
from app.domain.models import SubtitleASRGenerationRequest, TTSGenerationRequest
from app.media.audio_validation import FFprobeAudioValidator
from app.media.subtitle_quality import evaluate_subtitle_cues
from app.providers.errors_audio import TTSProviderError
from app.providers.factory import create_asr_provider, create_tts_provider
from app.rendering.subtitles import SubtitleCue


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPOSITORY_ROOT / "evaluation" / "asr-quality-evaluation-set.toml"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "evaluation" / "results"
EXTERNAL_EVAL_ENV = "AI_VIDEO_RUN_EXTERNAL_ASR_QUALITY_EVAL"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def _write_toml(path: Path, document: dict[str, Any]) -> None:
    """Write the flat-section result shape used by this evaluator."""

    top_level = {key: value for key, value in document.items() if not isinstance(value, dict)}
    sections = {key: value for key, value in document.items() if isinstance(value, dict)}
    lines = [f"{key} = {_toml_value(value)}" for key, value in top_level.items()]
    for section, values in sections.items():
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, dict):
                raise TypeError(f"Nested TOML sections are not supported: {section}.{key}")
            lines.append(f"{key} = {_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_dataset(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset_path = Path(path).resolve()
    with dataset_path.open("rb") as dataset_file:
        dataset = tomllib.load(dataset_file)
    if not isinstance(dataset, dict) or not str(dataset.get("dataset_version", "")).strip():
        raise ValueError("ASR evaluation dataset must define dataset_version")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("ASR evaluation dataset must contain at least one case")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise ValueError("each ASR evaluation case must be a TOML table")
        case_id = str(raw_case.get("id", "")).strip()
        reference_text = str(raw_case.get("reference_text", "")).strip()
        if not case_id or not reference_text:
            raise ValueError("each ASR evaluation case requires id and reference_text")
        if case_id in seen_ids:
            raise ValueError(f"duplicate ASR evaluation case id: {case_id}")
        if len(reference_text) > 5000:
            raise ValueError(f"ASR evaluation case is too long: {case_id}")
        expected_focus = raw_case.get("expected_focus", [])
        if not isinstance(expected_focus, list) or not all(
            isinstance(item, str) for item in expected_focus
        ):
            raise ValueError(f"expected_focus must be a string array: {case_id}")
        seen_ids.add(case_id)
        normalized.append({**raw_case, "id": case_id, "reference_text": reference_text})
    return dataset, normalized


def _select_cases(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset, all_cases = _load_dataset(args.dataset)
    selected_ids = {str(case_id) for case_id in (getattr(args, "case_id", None) or [])}
    cases = [case for case in all_cases if not selected_ids or case["id"] in selected_ids]
    if selected_ids and len(cases) != len(selected_ids):
        known_ids = {case["id"] for case in all_cases}
        missing = sorted(selected_ids - known_ids)
        raise ValueError(f"unknown ASR evaluation case id(s): {', '.join(missing)}")
    limit = getattr(args, "limit", None)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        cases = cases[:limit]
    if not cases:
        raise ValueError("no ASR evaluation cases selected")
    return dataset, cases


def _safe_settings_snapshot(settings: Settings) -> dict[str, dict[str, Any]]:
    return {
        "asr": {
            "provider": settings.asr_provider,
            "base_url": settings.asr_base_url,
            "model": settings.asr_model,
            "timeout_seconds": settings.asr_timeout_seconds,
            "api_key_set": bool(settings.asr_api_key),
        },
        "tts": {
            "provider": settings.tts_provider,
            "voice": settings.tts_voice,
            "rate": settings.tts_rate,
            "volume": settings.tts_volume,
            "timeout_seconds": settings.tts_timeout_seconds,
        },
    }


def _error_details(exc: Exception) -> tuple[str, str]:
    code = str(getattr(exc, "code", "ASR_QUALITY_EVALUATION_CASE_FAILED"))
    message = str(getattr(exc, "message", str(exc)))[:500]
    return code, message


def _safe_provider_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "response_format",
        "segment_count",
        "word_count",
        "timestamp_source",
        "segment_timestamps",
        "word_timestamps",
        "audio_inspected",
        "reference_text_used",
        "transcript_characters",
    }
    return {key: value for key, value in metadata.items() if key in allowed}


def _audio_suffix_for_mime(content_type: str) -> str:
    return {
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/webm": ".webm",
    }.get(content_type.split(";", 1)[0].strip().lower(), ".audio")


async def _close_provider(provider: object | None) -> None:
    if provider is None:
        return
    close = getattr(provider, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _result_document(
    *,
    dataset: dict[str, Any],
    case: dict[str, Any],
    run_id: str,
    settings: Settings,
    tts_voice: str,
    tts_rate: str,
    tts_volume: str,
    status: str,
    total_duration_ms: int,
    tts_duration_ms: int = 0,
    ffprobe_duration_ms: int = 0,
    provider_duration_ms: int = 0,
    audio_probe: Any | None = None,
    content_type: str = "",
    audio_path: str = "",
    quality: dict[str, Any] | None = None,
    provider_metadata: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    quality = quality or {
        "normalized_reference_characters": 0,
        "normalized_transcript_characters": 0,
        "edit_distance": None,
        "character_error_rate": None,
        "exact_text_match": None,
        "transcript_text": "",
        "cue_count": 0,
        "structural_gate_passed": False,
        "production_ready": False,
        "recommendation": "failed",
        "warnings": [],
    }
    audio = {
        "content_type": content_type,
        "retained": False,
        "path": "",
        "duration_seconds": 0,
        "codec_name": "",
        "format_name": "",
        "sample_rate": 0,
        "channel_count": 0,
    }
    if audio_probe is not None:
        audio = {
            "content_type": content_type,
            "retained": bool(audio_path),
            "path": audio_path,
            "duration_seconds": audio_probe.duration_seconds,
            "codec_name": audio_probe.codec_name,
            "format_name": audio_probe.format_name,
            "sample_rate": audio_probe.sample_rate,
            "channel_count": audio_probe.channel_count,
        }
    return {
        "schema_version": "asr-quality-result-v1",
        "dataset_version": str(dataset["dataset_version"]),
        "case_id": case["id"],
        "run_id": run_id,
        "status": status,
        "created_at": _now(),
        "input": {
            "language": str(dataset.get("language", "zh-CN")),
            "reference_character_count": len(case["reference_text"]),
            "tts_provider": settings.tts_provider,
            "tts_voice": tts_voice,
            "tts_rate": tts_rate,
            "tts_volume": tts_volume,
        },
        "provider": {
            "asr_provider": settings.asr_provider,
            "asr_model": settings.asr_model,
            "alignment_precision": quality.get("alignment_precision", ""),
            "metadata": provider_metadata or {},
        },
        "audio": audio,
        "quality": quality,
        "system": {
            "tts_duration_ms": tts_duration_ms,
            "ffprobe_duration_ms": ffprobe_duration_ms,
            "provider_duration_ms": provider_duration_ms,
            "total_duration_ms": total_duration_ms,
            "cost_status": (
                "本地 Mock，不产生外部 ASR 账单"
                if settings.asr_provider == "mock"
                else "待供应商账单核对"
            ),
        },
        "failure": {"error_code": error_code, "message": error_message},
    }


async def _evaluate_case(
    *,
    dataset: dict[str, Any],
    case: dict[str, Any],
    run_id: str,
    settings: Settings,
    tts_provider: object,
    asr_provider: object,
    probe: FFprobeAudioValidator,
    audio_output_dir: Path | None = None,
) -> dict[str, Any]:
    started = monotonic()
    language = str(dataset.get("language", "zh-CN"))
    voice = str(case.get("tts_voice", dataset.get("tts_voice", settings.tts_voice)))
    rate = str(case.get("tts_rate", dataset.get("tts_rate", settings.tts_rate)))
    volume = str(case.get("tts_volume", dataset.get("tts_volume", settings.tts_volume)))
    try:
        tts_result = await tts_provider.generate_speech(
            TTSGenerationRequest(
                text=case["reference_text"],
                voice=voice,
                rate=rate,
                volume=volume,
            )
        )
        try:
            audio_bytes = base64.b64decode(tts_result.audio_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise TTSProviderError(
                "TTS_PROVIDER_INVALID_RESPONSE", "TTS returned invalid base64 audio"
            ) from exc
        content_type = str(tts_result.mime_type)
        ffprobe_started = monotonic()
        audio_probe = await probe.validate_bytes(audio_bytes, content_type)
        ffprobe_duration_ms = max(1, round((monotonic() - ffprobe_started) * 1000))
        audio_path = ""
        if audio_output_dir is not None:
            audio_output_dir.mkdir(parents=True, exist_ok=True)
            audio_file = audio_output_dir / f"audio{_audio_suffix_for_mime(content_type)}"
            audio_file.write_bytes(audio_bytes)
            audio_path = audio_file.name
        asr_started = monotonic()
        asr_result = await asr_provider.transcribe(
            SubtitleASRGenerationRequest(
                audio_bytes=audio_bytes,
                mime_type=content_type,
                language=language,
                audio_duration_seconds=audio_probe.duration_seconds,
                reference_text=case["reference_text"],
            )
        )
        provider_duration_ms = max(1, round((monotonic() - asr_started) * 1000))
        quality = evaluate_subtitle_cues(
            tuple(
                SubtitleCue(
                    start_seconds=cue.start_seconds,
                    end_seconds=cue.end_seconds,
                    text=cue.text,
                )
                for cue in asr_result.cues
            ),
            audio_duration_seconds=audio_probe.duration_seconds,
            reference_text=case["reference_text"],
            alignment_precision=asr_result.precision,
            max_character_error_rate=float(dataset.get("max_character_error_rate", 0.05)),
        ).as_dict()
        quality["alignment_precision"] = asr_result.precision
        quality["transcript_text"] = "".join(cue.text for cue in asr_result.cues)
        return _result_document(
            dataset=dataset,
            case=case,
            run_id=run_id,
            settings=settings,
            tts_voice=voice,
            tts_rate=rate,
            tts_volume=volume,
            status="succeeded",
            total_duration_ms=max(1, round((monotonic() - started) * 1000)),
            tts_duration_ms=int(getattr(tts_result, "duration_ms", 0)),
            ffprobe_duration_ms=ffprobe_duration_ms,
            provider_duration_ms=max(
                provider_duration_ms, int(getattr(asr_result, "duration_ms", 0))
            ),
            audio_probe=audio_probe,
            content_type=content_type,
            audio_path=audio_path,
            quality=quality,
            provider_metadata=_safe_provider_metadata(asr_result.metadata),
        )
    except Exception as exc:
        error_code, error_message = _error_details(exc)
        failed_audio_probe = locals().get("audio_probe")
        failed_content_type = str(locals().get("content_type", ""))
        failed_audio_path = str(locals().get("audio_path", ""))
        return _result_document(
            dataset=dataset,
            case=case,
            run_id=run_id,
            settings=settings,
            tts_voice=voice,
            tts_rate=rate,
            tts_volume=volume,
            status="failed",
            total_duration_ms=max(1, round((monotonic() - started) * 1000)),
            audio_probe=failed_audio_probe,
            content_type=failed_content_type,
            audio_path=failed_audio_path,
            error_code=error_code,
            error_message=error_message,
        )


def _write_case_files(case_dir: Path, result: dict[str, Any], case: dict[str, Any]) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "input.json").write_text(
        json.dumps({"case": case}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (case_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    toml_result = {
        key: value
        for key, value in result.items()
        if key in {"schema_version", "dataset_version", "case_id", "run_id", "status", "created_at"}
        or isinstance(value, dict)
    }
    toml_result["provider"].pop("metadata", None)
    _write_toml(case_dir / "result.toml", toml_result)
    if result.get("audio", {}).get("retained"):
        review_document = {
            "schema_version": "asr-human-review-v1",
            "dataset_version": result["dataset_version"],
            "case_id": result["case_id"],
            "run_id": result["run_id"],
            "audio_path": result["audio"].get("path", ""),
            "reference_text": case["reference_text"],
            "transcript_text": result["quality"].get("transcript_text", ""),
            "review": {
                "reviewer": "",
                "reviewed_at": "",
                "speech_clarity": 0,
                "speech_naturalness": 0,
                "transcript_matches_audio": 0,
                "timing_confidence": 0,
                "decision": "pending",
                "notes": "",
            },
        }
        _write_toml(case_dir / "human-review.toml", review_document)


def _summary_document(
    *,
    dataset: dict[str, Any],
    run_id: str,
    results: list[dict[str, Any]],
    total_duration_ms: int,
    settings: Settings,
) -> dict[str, Any]:
    successful = [result for result in results if result["status"] == "succeeded"]
    reports = [result["quality"] for result in successful]
    cer_values = [
        float(report["character_error_rate"])
        for report in reports
        if report.get("character_error_rate") is not None
    ]
    provider_durations = [
        int(result["system"]["provider_duration_ms"]) for result in successful
    ]
    recommendations = {"pass": 0, "needs_review": 0, "failed": 0}
    for report in reports:
        recommendation = str(report.get("recommendation", "failed"))
        recommendations[recommendation] = recommendations.get(recommendation, 0) + 1
    return {
        "schema_version": "asr-quality-summary-v1",
        "dataset_version": str(dataset["dataset_version"]),
        "run_id": run_id,
        "created_at": _now(),
        "status": "completed",
        "cases": {
            "total": len(results),
            "succeeded": len(successful),
            "failed": len(results) - len(successful),
            "production_ready": sum(bool(report.get("production_ready")) for report in reports),
        },
        "quality": {
            "recommendation_pass": recommendations.get("pass", 0),
            "recommendation_needs_review": recommendations.get("needs_review", 0),
            "recommendation_failed": recommendations.get("failed", 0),
            "cer_mean": round(statistics.mean(cer_values), 6) if cer_values else None,
            "cer_max": round(max(cer_values), 6) if cer_values else None,
        },
        "system": {
            "total_duration_ms": total_duration_ms,
            "provider_duration_mean_ms": (
                round(statistics.mean(provider_durations)) if provider_durations else 0
            ),
            "provider_duration_p95_ms": (
                sorted(provider_durations)[max(0, math.ceil(len(provider_durations) * 0.95) - 1)]
                if provider_durations
                else 0
            ),
            "cost_status": (
                "本地 Mock，不产生外部 ASR 账单"
                if settings.asr_provider == "mock"
                else "待供应商账单核对"
            ),
        },
        "failure_codes": {
            code: sum(
                1 for result in results if result["failure"].get("error_code") == code
            )
            for code in sorted(
                {
                    str(result["failure"].get("error_code"))
                    for result in results
                    if result["failure"].get("error_code")
                }
            )
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    dataset, cases = _select_cases(args)
    if os.getenv(EXTERNAL_EVAL_ENV) != "1":
        return {
            "schema_version": "asr-quality-summary-v1",
            "dataset_version": str(dataset["dataset_version"]),
            "status": "skipped",
            "reason": f"set {EXTERNAL_EVAL_ENV}=1 to call external ASR/TTS services",
            "cases": {"selected": len(cases), "succeeded": 0, "failed": 0},
        }

    settings = load_settings(getattr(args, "config", None))
    effective_api_key = settings.asr_api_key or os.getenv("AI_VIDEO_LLM_API_KEY")
    if effective_api_key != settings.asr_api_key:
        settings = replace(settings, asr_api_key=effective_api_key)
    if settings.asr_provider == "mock":
        raise ValueError(
            "external ASR quality evaluation requires AI_VIDEO_ASR_PROVIDER to be "
            "siliconflow, openai_compatible or aliyun_dashscope"
        )

    run_id = getattr(args, "run_id", None) or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    output_root = Path(getattr(args, "output_dir", DEFAULT_OUTPUT_ROOT)).resolve()
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_toml(run_dir / "config-snapshot.toml", _safe_settings_snapshot(settings))

    tts_provider: object | None = None
    asr_provider: object | None = None
    results: list[dict[str, Any]] = []
    started = monotonic()
    try:
        tts_provider = create_tts_provider(settings)
        asr_provider = create_asr_provider(settings)
        probe = FFprobeAudioValidator(timeout_seconds=settings.tts_probe_timeout_seconds)
        for case in cases:
            case_dir = run_dir / str(case["id"])
            result = await _evaluate_case(
                dataset=dataset,
                case=case,
                run_id=run_id,
                settings=settings,
                tts_provider=tts_provider,
                asr_provider=asr_provider,
                probe=probe,
                audio_output_dir=case_dir if bool(getattr(args, "retain_audio", False)) else None,
            )
            results.append(result)
            _write_case_files(case_dir, result, case)
    finally:
        await _close_provider(asr_provider)
        await _close_provider(tts_provider)

    summary = _summary_document(
        dataset=dataset,
        run_id=run_id,
        results=results,
        total_duration_ms=max(1, round((monotonic() - started) * 1000)),
        settings=settings,
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_toml(run_dir / "summary.toml", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--retain-audio",
        action="store_true",
        help="save generated audio and create a per-case human-review.toml template",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
