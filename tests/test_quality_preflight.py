from __future__ import annotations

import json
from pathlib import Path

import evaluation.run_quality_preflight as quality_preflight


def _script(*, scene_durations: list[int], captions: list[str] | None = None) -> dict:
    captions = captions or ["第一段", "第二段", "第三段"]
    return {
        "title": "测试脚本",
        "hook": "先看关键方法",
        "summary": "用三步说明方法",
        "duration_seconds": 60,
        "scenes": [
            {
                "scene_index": index,
                "duration_seconds": duration,
                "voiceover": f"这是第{index}段旁白。",
                "caption": captions[index - 1],
                "visual_keywords": ["test", f"scene-{index}"],
                "transition": "cut",
                "risk_notes": [],
            }
            for index, duration in enumerate(scene_durations, start=1)
        ],
        "cta": "收藏后再复习",
        "risk_notes": [],
    }


def _write_run(tmp_path: Path, script: dict) -> Path:
    run_dir = tmp_path / "run"
    case_dir = run_dir / "science-01"
    case_dir.mkdir(parents=True)
    (case_dir / "script-output.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir


def test_quality_preflight_passes_exact_script_and_writes_review_artifacts(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, _script(scene_durations=[20, 20, 20]))

    summary = quality_preflight.run_preflight(
        run_dir,
        dataset_path=quality_preflight.DEFAULT_DATASET,
        case_ids={"science-01"},
    )

    assert summary["hard_gate_passed"] == 1
    assert summary["hard_gate_failed"] == 0
    case_result = json.loads(
        (run_dir / "science-01" / "quality-preflight.json").read_text(encoding="utf-8")
    )
    assert case_result["hard_gate_passed"] is True
    assert case_result["metrics"]["scene_duration_total_seconds"] == 60
    assert case_result["manual_review"]["scores"]["script_structure"] == 0
    assert (run_dir / "quality-preflight-summary.json").exists()


def test_quality_preflight_fails_non_exact_scene_duration_and_warns_long_caption(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path,
        _script(scene_durations=[20, 20, 15], captions=["这是一条超过二十二个字符的字幕文本需要人工复核", "第二段", "第三段"]),
    )

    summary = quality_preflight.run_preflight(
        run_dir,
        dataset_path=quality_preflight.DEFAULT_DATASET,
        case_ids={"science-01"},
    )

    assert summary["hard_gate_passed"] == 0
    assert summary["failed_checks"]["SCENE_DURATION_EXACT"] == 1
    assert summary["warning_checks"]["CAPTION_LENGTH_REVIEW"] == 1
    case_result = json.loads(
        (run_dir / "science-01" / "quality-preflight.json").read_text(encoding="utf-8")
    )
    assert case_result["checks"]["SCENE_DURATION_EXACT"]["status"] == "fail"
    assert case_result["metrics"]["captions_over_review_limit"] == 1
