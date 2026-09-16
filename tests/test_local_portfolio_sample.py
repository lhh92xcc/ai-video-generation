from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "run-local-portfolio-sample.py"
_SPEC = importlib.util.spec_from_file_location("local_portfolio_sample", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_narration_scenes = _MODULE._narration_scenes
_recent_reference_files = _MODULE._recent_reference_files
_reference_prompt = _MODULE._reference_prompt
_load_or_create_checkpoint = _MODULE._load_or_create_checkpoint
_read_checkpointed_clip = _MODULE._read_checkpointed_clip
_set_shot_checkpoint_entry = _MODULE._set_shot_checkpoint_entry
_stable_shot_path = _MODULE._stable_shot_path
_write_checkpoint = _MODULE._write_checkpoint
_total_video_duration_seconds = _MODULE._total_video_duration_seconds
_build_narration_timeline = _MODULE._build_narration_timeline
_estimate_continuous_scene_durations = _MODULE._estimate_continuous_scene_durations
_balanced_visual_scene_durations = _MODULE._balanced_visual_scene_durations
_build_continuous_narration_text = _MODULE._build_continuous_narration_text
_continuous_scene_boundary_character_offsets = _MODULE._continuous_scene_boundary_character_offsets
_build_continuous_audio_scene_timings = _MODULE._build_continuous_audio_scene_timings
_summarize_scene_boundary_pauses = _MODULE._summarize_scene_boundary_pauses
_summarize_scene_effective_speech_rates = _MODULE._summarize_scene_effective_speech_rates
_frame_aligned_duration = _MODULE._frame_aligned_duration
_frame_aligned_scene_durations = _MODULE._frame_aligned_scene_durations
_build_continuous_narration_timeline = _MODULE._build_continuous_narration_timeline
_fit_narration_artifact_to_shot = _MODULE._fit_narration_artifact_to_shot
_portfolio_readiness_report = _MODULE._portfolio_readiness_report
_formal_portfolio_eligible = _MODULE._formal_portfolio_eligible
_portfolio_sample_mode = _MODULE._portfolio_sample_mode
_ensure_portfolio_video_provider = _MODULE._ensure_portfolio_video_provider
_assert_real_portfolio_video_artifact = _MODULE._assert_real_portfolio_video_artifact
_resolve_sample_config = _MODULE._resolve_sample_config
_apply_visual_quality_profile = _MODULE._apply_visual_quality_profile


def _clip_snapshots(count: int, identity_status: str = "passed"):
    from app.domain.models import ArtifactSummary, GenerationTaskKind, GenerationTaskRecord, TaskStatus

    return [
        GenerationTaskRecord(
            project_id=uuid4(),
            kind=GenerationTaskKind.VIDEO_CLIP,
            status=TaskStatus.SUCCEEDED,
            input_data={"episode_id": str(uuid4()), "shot_index": index},
            artifacts=[
                ArtifactSummary(
                    type="video_clip",
                    provider="comfyui_wan_i2v",
                    metadata={
                        "identity_audit": {"status": identity_status},
                        "motion_evidence": {"status": "motion_detected"},
                    },
                )
            ],
        )
        for index in range(1, count + 1)
    ]


def test_portfolio_sample_has_twelve_short_drama_scenes_for_the_8_to_12_target() -> None:
    scenes = _narration_scenes()
    assert len(scenes) == _MODULE.PORTFOLIO_MAX_SHOTS == 12
    assert all(title and voiceover and visual_prompt for title, voiceover, visual_prompt in scenes)
    # Keep the free, natural-rate Edge TTS demo within the 45–60 second target;
    # do not compensate for an overlong script by speeding up the waveform.
    assert len("".join(scene[1] for scene in scenes)) < 300
    assert all(_MODULE.PORTFOLIO_STYLE_LOCK in scene[2] for scene in scenes)
    assert all("photorealistic" in scene[2] for scene in scenes)


def test_portfolio_scene_prompts_lock_illustrated_style_and_vary_composition() -> None:
    prompts = [prompt for _title, _voiceover, prompt in _narration_scenes()]

    assert all("single coherent 9:16 illustrated frame" in prompt for prompt in prompts)
    assert all("flat 2D manhwa" in prompt for prompt in prompts)
    assert all("cinematic vertical" not in prompt for prompt in prompts)
    assert len(set(prompts)) == len(prompts)
    assert any("over-the-shoulder" in prompt for prompt in prompts)
    assert any("mirror" in prompt for prompt in prompts)
    assert any("high-angle" in prompt for prompt in prompts)


def test_sample_config_follows_environment_instead_of_assuming_mac_private_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AI_VIDEO_CONFIG", raising=False)
    monkeypatch.delenv("AI_VIDEO_PROFILE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _resolve_sample_config(None) == "config/config.example.toml"

    monkeypatch.setenv("AI_VIDEO_PROFILE", "windows_gpu")
    assert _resolve_sample_config(None) is None

    assert _resolve_sample_config("config/config.windows_gpu.toml") == "config/config.windows_gpu.toml"


def test_portfolio_readiness_report_separates_machine_gates_from_human_review() -> None:
    from app.domain.models import ArtifactSummary, GenerationTaskKind, GenerationTaskRecord, TaskStatus

    rendered = ArtifactSummary(
        type="rendered_video",
        provider="ffmpeg",
        metadata={
            "ffprobe": {
                "width": 576,
                "height": 1024,
                "duration_seconds": 50.0,
            },
            "duration_ms": 50000,
        },
    )
    narration = ArtifactSummary(
        type="audio_narration",
        provider="edge_tts",
        metadata={"duration_seconds": 49.8, "content_type": "audio/wav"},
    )
    subtitles = ArtifactSummary(
        type="subtitle_srt",
        provider="word_boundary",
        metadata={"cue_count": 8, "alignment_precision": "word_boundary"},
    )
    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=False),
        shot_count=8,
        reference_image_count=4,
        clip_count=8,
        narration_artifact=narration,
        subtitle_artifact=subtitles,
        subtitle_metadata=subtitles.metadata,
        rendered_artifact=rendered,
        clip_task_snapshots=_clip_snapshots(8),
    )

    readiness = report["readiness"]
    assert readiness["status"] == "ready_for_human_review"
    assert readiness["ready_for_portfolio"] is False
    assert readiness["machine_checks_passed"] == 10
    assert readiness["machine_checks_total"] == 10
    assert readiness["human_checks_completed"] == 0
    assert len(report["human_review"]["items"]) == 6
    assert {item["status"] for item in report["machine_checks"]} == {"passed"}
    assert report["real_motion_provider"]["real_motion_provider"] == "comfyui_wan_i2v"


def test_real_portfolio_rejects_non_model_motion_provider() -> None:
    with pytest.raises(RuntimeError, match="PORTFOLIO_REAL_VIDEO_REQUIRED"):
        _ensure_portfolio_video_provider("ffmpeg_motion", mock_media=False)


def test_real_portfolio_accepts_registered_model_motion_provider() -> None:
    _ensure_portfolio_video_provider("comfyui_wan_i2v", mock_media=False)


def test_formal_portfolio_eligibility_requires_identity_keyframes() -> None:
    assert _formal_portfolio_eligible(
        shot_count=8,
        mock_media=False,
        preview_only=False,
        video_provider="comfyui_wan_i2v",
        shot_keyframe_mode="auto",
    ) is True
    assert _formal_portfolio_eligible(
        shot_count=8,
        mock_media=False,
        preview_only=False,
        video_provider="comfyui_wan_i2v",
        shot_keyframe_mode="off",
    ) is False
    assert _formal_portfolio_eligible(
        shot_count=1,
        mock_media=False,
        preview_only=False,
        video_provider="comfyui_wan_i2v",
        shot_keyframe_mode="auto",
    ) is False
    assert _formal_portfolio_eligible(
        shot_count=12,
        mock_media=False,
        preview_only=False,
        video_provider="comfyui_wan_i2v",
        shot_keyframe_mode="always",
    ) is True


def test_portfolio_sample_mode_distinguishes_smoke_from_formal_runs() -> None:
    assert _portfolio_sample_mode(
        shot_count=1,
        mock_media=False,
        preview_only=False,
    ) == "smoke"
    assert _portfolio_sample_mode(
        shot_count=7,
        mock_media=False,
        preview_only=False,
    ) == "smoke"
    assert _portfolio_sample_mode(
        shot_count=8,
        mock_media=False,
        preview_only=False,
    ) == "formal"
    assert _portfolio_sample_mode(
        shot_count=12,
        mock_media=False,
        preview_only=False,
    ) == "formal"
    assert _portfolio_sample_mode(
        shot_count=1,
        mock_media=False,
        preview_only=True,
    ) == "preview_only"


def test_preview_only_report_cannot_be_ready_for_portfolio() -> None:
    from app.domain.models import ArtifactSummary

    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=False, preview_only=True),
        shot_count=10,
        reference_image_count=4,
        clip_count=10,
        narration_artifact=ArtifactSummary(
            type="audio_narration",
            provider="edge_tts",
            metadata={"duration_seconds": 50.0},
        ),
        subtitle_artifact=ArtifactSummary(
            type="subtitle_srt",
            provider="provided_cues",
            metadata={"cue_count": 10},
        ),
        subtitle_metadata={"cue_count": 10},
        rendered_artifact=ArtifactSummary(
            type="rendered_video",
            provider="ffmpeg",
            metadata={
                "width": 576,
                "height": 1024,
                "duration_seconds": 50.0,
            },
        ),
        clip_task_snapshots=[],
        video_provider="ffmpeg_motion",
    )

    assert report["sample_mode"] == "preview_only"
    assert report["readiness"]["status"] == "preview_only"
    assert report["readiness"]["ready_for_portfolio"] is False
    assert report["real_motion_provider"]["status"] == "failed"


def test_real_portfolio_rejects_static_motion_artifact() -> None:
    from app.domain.models import ArtifactSummary

    artifact = ArtifactSummary(
        type="video_clip",
        provider="ffmpeg_motion",
        metadata={"motion": "ken_burns", "source": "reviewed_reference_image"},
    )
    with pytest.raises(RuntimeError, match="PORTFOLIO_REAL_VIDEO_REQUIRED"):
        _assert_real_portfolio_video_artifact(
            artifact,
            configured_provider="comfyui_wan_i2v",
        )


def test_mock_portfolio_readiness_report_is_not_blocked_by_real_media_checks() -> None:
    from app.domain.models import ArtifactSummary

    rendered = ArtifactSummary(
        type="rendered_video",
        provider="ffmpeg",
        metadata={"width": 576, "height": 1024, "duration_seconds": 50.0},
    )
    narration = ArtifactSummary(
        type="audio_narration",
        provider="mock",
        metadata={"duration_seconds": 50.0},
    )
    subtitles = ArtifactSummary(
        type="subtitle_srt",
        provider="mock",
        metadata={"cue_count": 8},
    )

    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=True),
        shot_count=8,
        reference_image_count=0,
        clip_count=8,
        narration_artifact=narration,
        subtitle_artifact=subtitles,
        subtitle_metadata=subtitles.metadata,
        rendered_artifact=rendered,
        clip_task_snapshots=[],
    )

    checks = {item["id"]: item for item in report["machine_checks"]}
    assert checks["reference_images"]["status"] == "not_applicable"
    assert checks["identity_audit"]["status"] == "not_applicable"
    assert report["readiness"]["status"] == "engineering_fixture"
    assert report["sample_mode"] == "mock"
    assert report["readiness"]["ready_for_portfolio"] is False


def test_real_portfolio_readiness_blocks_until_identity_audit_exists() -> None:
    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=False),
        shot_count=10,
        reference_image_count=2,
        clip_count=10,
        narration_artifact=None,
        subtitle_artifact=None,
        subtitle_metadata={},
        rendered_artifact=None,
        clip_task_snapshots=[],
    )

    checks = {item["id"]: item for item in report["machine_checks"]}
    assert checks["identity_audit"]["status"] == "pending"
    assert checks["identity_audit"]["blocking"] is True
    assert report["readiness"]["status"] == "incomplete"


def test_real_portfolio_readiness_blocks_unavailable_identity_audit() -> None:
    from app.domain.models import ArtifactSummary, GenerationTaskKind, GenerationTaskRecord, TaskStatus

    clip_task = GenerationTaskRecord(
        project_id=uuid4(),
        kind=GenerationTaskKind.VIDEO_CLIP,
        status=TaskStatus.SUCCEEDED,
        input_data={"episode_id": str(uuid4()), "shot_index": 1},
        artifacts=[
            ArtifactSummary(
                type="video_clip",
                provider="comfyui_wan_i2v",
                metadata={"identity_audit": {"status": "unavailable"}},
            )
        ],
    )
    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=False),
        shot_count=8,
        reference_image_count=1,
        clip_count=8,
        narration_artifact=None,
        subtitle_artifact=None,
        subtitle_metadata={},
        rendered_artifact=None,
        clip_task_snapshots=[clip_task],
        video_provider="comfyui_wan_i2v",
    )

    identity_check = next(item for item in report["machine_checks"] if item["id"] == "identity_audit")
    assert identity_check["status"] == "failed"
    assert identity_check["blocking"] is True
    assert report["readiness"]["status"] == "incomplete"


def test_real_portfolio_readiness_ignores_not_applicable_identity_audits() -> None:
    from app.domain.models import ArtifactSummary, GenerationTaskKind, GenerationTaskRecord, TaskStatus

    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=False),
        shot_count=8,
        reference_image_count=1,
        clip_count=8,
        narration_artifact=ArtifactSummary(
            type="audio_narration",
            provider="edge_tts",
            metadata={"duration_seconds": 50.0},
        ),
        subtitle_artifact=ArtifactSummary(
            type="subtitle_srt",
            provider="provided_cues",
            metadata={"cue_count": 8},
        ),
        subtitle_metadata={"cue_count": 8},
        rendered_artifact=ArtifactSummary(
            type="rendered_video",
            provider="ffmpeg",
            metadata={"width": 576, "height": 1024, "duration_seconds": 50.0},
        ),
        clip_task_snapshots=_clip_snapshots(8, identity_status="not_applicable"),
        video_provider="comfyui_wan_i2v",
    )

    identity_check = next(item for item in report["machine_checks"] if item["id"] == "identity_audit")
    assert identity_check["status"] == "not_applicable"
    assert identity_check["blocking"] is False
    assert report["readiness"]["status"] == "ready_for_human_review"


def test_partial_portfolio_readiness_report_marks_unfinished_stages_pending() -> None:
    report = _portfolio_readiness_report(
        args=argparse.Namespace(mock_media=False),
        shot_count=10,
        reference_image_count=2,
        clip_count=3,
        narration_artifact=None,
        subtitle_artifact=None,
        subtitle_metadata={},
        rendered_artifact=None,
        clip_task_snapshots=[],
    )

    assert report["schema_version"] == 2
    assert report["readiness"]["status"] == "incomplete"
    checks = {item["id"]: item for item in report["machine_checks"]}
    assert checks["reference_images"]["status"] == "passed"
    assert checks["video_clips"]["status"] == "pending"
    assert checks["narration"]["status"] == "pending"
    assert checks["subtitles"]["status"] == "pending"
    assert checks["vertical_output"]["status"] == "pending"
    assert checks["duration_target"]["status"] == "pending"
    assert report["human_review"]["status"] == "pending"


def test_portfolio_shot_fixture_uses_supported_tokens_for_all_twelve_shots() -> None:
    from app.domain.models import ShotContent

    assert len(_MODULE._PORTFOLIO_SHOT_SIZES) == _MODULE.PORTFOLIO_MAX_SHOTS
    assert len(_MODULE._PORTFOLIO_CAMERA_MOVEMENTS) == _MODULE.PORTFOLIO_MAX_SHOTS
    assert len(_MODULE._PORTFOLIO_SHOT_ASSETS) == _MODULE.PORTFOLIO_MAX_SHOTS
    assert len(_MODULE._PORTFOLIO_REFERENCE_NAMES) == _MODULE.PORTFOLIO_MAX_SHOTS
    for index, (shot_size, camera_movement) in enumerate(
        zip(
            _MODULE._PORTFOLIO_SHOT_SIZES,
            _MODULE._PORTFOLIO_CAMERA_MOVEMENTS,
            strict=True,
        ),
        start=1,
    ):
        shot = ShotContent(
            shot_index=index,
            scene_index=index,
            duration_seconds=3,
            shot_size=shot_size,
            camera_movement=camera_movement,
            characters=[],
            location="测试场景",
            visual_prompt="测试镜头",
            audio_requirements=["环境声"],
            asset_requirements=["location:测试场景"],
        )
        assert shot.shot_size == shot_size
        assert shot.camera_movement == camera_movement


def test_portfolio_runner_accepts_the_full_twelve_shot_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-local-portfolio-sample.py",
            "--shots",
            "12",
            "--stop-after-shot",
            "12",
        ],
    )

    args = _MODULE.parse_args()

    assert args.shots == _MODULE.PORTFOLIO_MAX_SHOTS == 12
    assert args.stop_after_shot == _MODULE.PORTFOLIO_MAX_SHOTS


def test_runner_quality_profile_is_applied_to_runtime_settings() -> None:
    from app.config import load_settings

    settings, _registry, snapshot = _apply_visual_quality_profile(
        load_settings("config/config.example.toml"),
        "local_safe",
    )

    assert snapshot["profile_id"] == "local_safe"
    assert settings.visual_quality_profile == "local_safe"
    assert (settings.image_width, settings.image_height) == (432, 768)
    assert (settings.video_output_width, settings.video_output_height) == (288, 512)
    assert settings.video_fps == 12
    assert settings.video_steps == 6


def test_runner_quality_profile_can_override_windows_default() -> None:
    from app.config import load_settings

    settings, _registry, snapshot = _apply_visual_quality_profile(
        load_settings("config/config.windows_gpu.toml"),
        "local_safe",
    )

    assert snapshot["profile_id"] == "local_safe"
    assert (settings.image_width, settings.image_height) == (432, 768)
    assert (settings.video_output_width, settings.video_output_height) == (288, 512)
    assert settings.video_fps == 12


def test_reference_prompts_are_single_subject_or_scene() -> None:
    prompts = {name: _reference_prompt(name) for name in ("林默", "黑伞女孩", "旧城区钟表店", "铜色怀表")}

    assert "head-and-shoulders portrait of one young Chinese man" in prompts["林默"]
    assert "both shoulders face directly toward the viewer" in prompts["林默"]
    assert "ending just below the shoulders" in prompts["林默"]
    assert "leather gloves" not in prompts["林默"]
    assert "dark long coat" not in prompts["林默"]
    assert "short black hair, slim face" in prompts["林默"].lower()
    assert "portrait of one young Chinese woman" in prompts["黑伞女孩"]
    assert "both shoulders face directly toward the viewer" in prompts["黑伞女孩"]
    assert "hair falls visibly over both shoulders" in prompts["黑伞女孩"]
    assert "lips are gently closed" in prompts["黑伞女孩"]
    assert "black long coat" not in prompts["黑伞女孩"]
    assert "no people" in prompts["旧城区钟表店"]
    assert "one antique bronze mechanical pocket watch" in prompts["铜色怀表"]
    assert all(len(prompt) <= 1500 for prompt in prompts.values())
    assert all("flat 2d manhwa" in prompt.lower() for prompt in prompts.values())
    # Environment/prop prompts retain their existing style constraints.
    # Visually checked portraits use direct positive framing.
    for name in ("旧城区钟表店", "铜色怀表"):
        prompt = prompts[name]
        assert "Reference quality guardrails:" in prompt
        assert "no depth-of-field blur" in prompt
        assert f"Avoid: {_MODULE.PORTFOLIO_STYLE_NEGATIVE_LOCK}." in prompt
    assert "long loose straight black hair" in prompts["黑伞女孩"].lower()
    assert "rainy street visible through one window" in prompts["旧城区钟表店"]
    assert "hands stopped at twelve o'clock" in prompts["铜色怀表"]


def test_portfolio_shots_use_semantically_matching_reference_assets() -> None:
    assert _MODULE._PORTFOLIO_REFERENCE_NAMES[:7] == (
        "旧城区钟表店",
        "旧城区钟表店",
        "黑伞女孩",
        "黑伞女孩",
        "铜色怀表",
        "铜色怀表",
        "林默",
    )
    assert _MODULE._PORTFOLIO_REFERENCE_NAMES[7:] == (
        "黑伞女孩",
        "林默",
        "林默",
        "林默",
        "林默",
    )


def test_stop_after_shot_is_a_pause_boundary_even_when_it_is_the_last_requested_shot() -> None:
    source = Path(_MODULE.__file__).read_text(encoding="utf-8")

    assert "stop_after_shot_reached = False" in source
    assert "if len(clip_tasks) < len(shots) or stop_after_shot_reached:" in source


def test_recent_reference_files_are_sorted_newest_first(tmp_path: Path) -> None:
    files: list[Path] = []
    for index in range(4):
        path = tmp_path / "reference-images" / f"asset-{index}" / "v1" / f"{index}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        files.append(path)
    selected = _recent_reference_files(tmp_path, count=4)
    assert {path.name for path in selected} == {path.name for path in files}


def test_checkpoint_round_trip_reuses_completed_shot(tmp_path: Path) -> None:
    initial_args = argparse.Namespace(
        shots=2,
        shot_duration=3,
        mock_media=True,
        resume=False,
    )
    checkpoint = _load_or_create_checkpoint(tmp_path, initial_args)
    clip = b"verified-mp4-bytes"
    clip_path = _stable_shot_path(tmp_path, 1)
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(clip)
    _set_shot_checkpoint_entry(
        checkpoint,
        1,
        {
            "status": "succeeded",
            "output_path": str(clip_path.resolve()),
            "sha256": hashlib.sha256(clip).hexdigest(),
        },
    )
    _write_checkpoint(tmp_path / "run-checkpoint.json", checkpoint)

    resume_args = argparse.Namespace(
        shots=2,
        shot_duration=3,
        mock_media=True,
        resume=True,
    )
    _load_or_create_checkpoint(tmp_path, resume_args)

    assert _read_checkpointed_clip(tmp_path, 1, checkpoint["shots"]["1"]) == clip


def test_checkpoint_can_extend_to_more_shots_without_discarding_progress(tmp_path: Path) -> None:
    initial_args = argparse.Namespace(
        shots=2,
        shot_duration=3,
        mock_media=True,
        resume=False,
    )
    checkpoint = _load_or_create_checkpoint(tmp_path, initial_args)
    _set_shot_checkpoint_entry(
        checkpoint,
        1,
        {"status": "succeeded", "shot_index": 1},
    )
    _write_checkpoint(tmp_path / "run-checkpoint.json", checkpoint)

    extended_args = argparse.Namespace(
        shots=3,
        shot_duration=3,
        mock_media=True,
        resume=True,
    )
    resumed = _load_or_create_checkpoint(tmp_path, extended_args)

    assert resumed["shots_requested"] == 3
    assert resumed["extended_from_shots"] == 2
    assert resumed["shots"]["1"]["status"] == "succeeded"


def test_checkpoint_rejects_mixing_quality_profiles(tmp_path: Path) -> None:
    initial_args = argparse.Namespace(
        shots=1,
        shot_duration=3,
        mock_media=True,
        resume=False,
        quality_profile="local_safe",
    )
    _load_or_create_checkpoint(
        tmp_path,
        initial_args,
        quality_profile_id="local_safe",
    )

    resume_args = argparse.Namespace(
        shots=1,
        shot_duration=3,
        mock_media=True,
        resume=True,
        quality_profile="local_balanced",
    )
    with pytest.raises(RuntimeError, match="quality-profile"):
        _load_or_create_checkpoint(
            tmp_path,
            resume_args,
            quality_profile_id="local_balanced",
        )


def test_checkpoint_rejects_mixing_shot_keyframe_modes(tmp_path: Path) -> None:
    initial_args = argparse.Namespace(
        shots=1,
        shot_duration=3,
        mock_media=True,
        resume=False,
        shot_keyframe_mode="off",
    )
    _load_or_create_checkpoint(tmp_path, initial_args)

    resume_args = argparse.Namespace(
        shots=1,
        shot_duration=3,
        mock_media=True,
        resume=True,
        shot_keyframe_mode="auto",
    )
    with pytest.raises(RuntimeError, match="shot-keyframe-mode"):
        _load_or_create_checkpoint(tmp_path, resume_args)


def test_total_video_duration_reads_ffprobe_metadata() -> None:
    from app.domain.models import ArtifactSummary, GenerationTaskKind, GenerationTaskRecord, TaskStatus
    from app.repositories.in_memory import InMemoryStore

    async def run() -> float:
        store = InMemoryStore()
        first = GenerationTaskRecord(
            project_id=uuid4(),
            kind=GenerationTaskKind.VIDEO_CLIP,
            status=TaskStatus.SUCCEEDED,
            artifacts=[
                ArtifactSummary(
                    type="video_clip",
                    provider="comfyui_wan_i2v",
                    metadata={"ffprobe": {"duration_seconds": 3.125}},
                )
            ],
        )
        second = GenerationTaskRecord(
            project_id=uuid4(),
            kind=GenerationTaskKind.VIDEO_CLIP,
            status=TaskStatus.SUCCEEDED,
            artifacts=[
                ArtifactSummary(
                    type="video_clip",
                    provider="comfyui_wan_i2v",
                    metadata={"duration_seconds": 3.125},
                )
            ],
        )
        await store.create_task(first)
        await store.create_task(second)
        return await _total_video_duration_seconds(store, [first.id, second.id])

    import asyncio

    assert asyncio.run(run()) == 6.25


def test_narration_timeline_uses_measured_audio_without_overlap() -> None:
    scenes = _narration_scenes()[:3]

    timeline = _build_narration_timeline(
        scenes,
        [5.125, 5.125, 5.125],
        [2.1, 6.0, 1.8],
        transition_overlap_seconds=0.0,
    )

    assert [segment.start_seconds for segment in timeline] == [0.0, 5.125, 11.125]
    assert [segment.end_seconds for segment in timeline] == [2.1, 11.125, 12.925]
    assert timeline[0].crosses_shot_boundary is False
    assert timeline[1].crosses_shot_boundary is True
    assert timeline[1].starts_before_shot_boundary is False
    assert all(
        left.end_seconds <= right.start_seconds
        for left, right in zip(timeline[:-1], timeline[1:], strict=True)
    )


def test_narration_timeline_rejects_audio_that_cannot_fit_video() -> None:
    scenes = _narration_scenes()[:2]

    with pytest.raises(ValueError, match="after the 2.000s video timeline"):
        _build_narration_timeline(scenes, [1.0, 1.0], [2.0, 1.0])


def test_narration_timeline_can_bridge_frame_rounding_without_audio_gaps() -> None:
    scenes = _narration_scenes()[:2]

    timeline = _build_narration_timeline(
        scenes,
        [5.125, 5.125],
        [5.05, 1.0],
        transition_overlap_seconds=0.12,
    )

    assert timeline[1].start_seconds == timeline[0].end_seconds
    assert timeline[1].starts_before_shot_boundary is True


def test_continuous_narration_timeline_has_contiguous_scene_cues() -> None:
    scenes = _narration_scenes()[:3]
    durations = _estimate_continuous_scene_durations(scenes, 12.0)
    timeline = _build_continuous_narration_timeline(
        scenes,
        [4.0, 4.0, 4.0],
        12.0,
    )

    assert sum(durations) == pytest.approx(12.0)
    assert timeline[0].start_seconds == 0.0
    assert timeline[-1].end_seconds == 12.0
    assert all(
        left.end_seconds == right.start_seconds
        for left, right in zip(timeline[:-1], timeline[1:], strict=True)
    )
    assert all(segment.audio_duration_seconds > 0 for segment in timeline)


def test_continuous_narration_preserves_semantic_boundaries_for_tts() -> None:
    scenes = _narration_scenes()

    text = _build_continuous_narration_text(scenes)

    # Visual boundaries are empty separators. Only editorial punctuation from
    # the story text reaches the provider, so sentence groups remain coherent
    # without independently synthesized scene fragments.
    assert "三下钟声，今晚第三声" in text
    assert "指针同时停住，雨夜里" in text
    assert "放上柜台，她只说" in text
    assert "回不到今天；怀表" in text
    assert "灰尘归位，雨声突然消失" in text
    assert "暂停的照片，女孩" in text
    assert "一扇禁门；林默" in text
    assert "走进黑暗，暗门尽头" in text
    assert "第四声正在黑暗里回响，最后一声" in text
    assert text.count("。") == 1
    assert text.endswith("作出选择。")


def test_continuous_scene_pause_compaction_targets_only_visual_boundaries() -> None:
    scenes = _narration_scenes()[:3]

    assert _continuous_scene_boundary_character_offsets(scenes) == {24, 42}


def test_scene_boundary_pause_summary_is_observability_only() -> None:
    timings = [
        _MODULE.ContinuousSceneAudioTiming(0.0, 1.0, 0.1, 0.8),
        _MODULE.ContinuousSceneAudioTiming(1.3, 2.0, 1.3, 1.9),
        _MODULE.ContinuousSceneAudioTiming(2.2, 3.0, 2.2, 2.8),
    ]

    report = _summarize_scene_boundary_pauses(timings)

    assert report["boundary_count"] == 2
    assert report["pause_seconds"] == [0.5, 0.3]
    assert report["max_pause_seconds"] == 0.5
    assert report["long_pause_count"] == 0


def test_scene_effective_speech_rate_summary_exposes_variation_without_editing_audio() -> None:
    scenes = _narration_scenes()[:2]
    report = _summarize_scene_effective_speech_rates(
        scenes,
        [
            _MODULE.ContinuousSceneAudioTiming(0.0, 2.0, 0.1, 1.6),
            _MODULE.ContinuousSceneAudioTiming(2.0, 4.0, 2.0, 2.9),
        ],
    )

    assert report["measurement"] == "normalized_scene_characters_divided_by_provider_speech_span"
    assert len(report["scene_effective_characters_per_second"]) == 2
    assert report["max_characters_per_second"] > report["min_characters_per_second"]
    assert report["spread_characters_per_second"] > 0


def test_continuous_narration_can_map_provider_word_boundaries() -> None:
    scenes = _narration_scenes()[:3]
    raw_boundaries = [
        {
            "start_seconds": 0.1,
            "end_seconds": 1.0,
            "text": "旧城区的钟表店里林默每晚都能听见墙后传来三下钟声",
        },
        {
            "start_seconds": 1.25,
            "end_seconds": 1.9,
            "text": "今晚第三声落下店里的钟表指针同时停住",
        },
        {
            "start_seconds": 2.2,
            "end_seconds": 2.95,
            "text": "雨夜里黑伞女孩把十二点的怀表放上柜台",
        },
    ]

    timings = _build_continuous_audio_scene_timings(scenes, 3.2, raw_boundaries)

    assert timings is not None
    assert timings[0].start_seconds == 0.0
    assert timings[0].end_seconds == 1.25
    assert timings[0].speech_start_seconds == 0.1
    assert timings[1].start_seconds == 1.25
    assert timings[1].end_seconds == 2.2
    assert timings[-1].end_seconds == 3.2


def test_continuous_narration_interpolates_a_word_crossing_a_scene_boundary() -> None:
    scenes = [
        ("第一段", "甲", "测试画面一"),
        ("第二段", "乙", "测试画面二"),
    ]
    timings = _build_continuous_audio_scene_timings(
        scenes,
        1.2,
        [
            {
                "start_seconds": 0.2,
                "end_seconds": 1.0,
                "text": "甲乙",
            }
        ],
    )

    assert timings is not None
    assert timings[0].end_seconds == 0.6
    assert timings[1].start_seconds == 0.6
    assert timings[0].speech_end_seconds == 0.6
    assert timings[1].speech_start_seconds == 0.6
    assert timings[-1].end_seconds == 1.2


def test_visual_duration_budget_rounds_up_to_a_complete_frame() -> None:
    assert _frame_aligned_duration(5.536125, 8) == 5.625
    assert _frame_aligned_duration(5.0, 8) == 5.0


def test_visual_scene_rounding_uses_cumulative_boundaries() -> None:
    aligned = _frame_aligned_scene_durations([5.536125, 4.186083, 5.47225], 8)

    assert aligned == [5.625, 4.125, 5.5]
    assert sum(aligned) == 15.25


def test_balanced_visual_scene_durations_distribute_extra_frames_evenly() -> None:
    durations = _balanced_visual_scene_durations([5.125] * 10, 57.096, 8)

    assert durations == [5.75] * 7 + [5.625] * 3
    assert sum(durations) == pytest.approx(57.125)


def test_visual_scene_rounding_prefers_the_pause_before_the_next_word() -> None:
    scenes = _narration_scenes()[:3]
    raw_boundaries = [
        {
            "start_seconds": 0.1,
            "end_seconds": 1.0,
            "text": "旧城区的钟表店里林默每晚都能听见墙后传来三下钟声",
        },
        {
            "start_seconds": 1.25,
            "end_seconds": 1.9,
            "text": "今晚第三声落下店里的钟表指针同时停住",
        },
        {
            "start_seconds": 2.2,
            "end_seconds": 2.95,
            "text": "雨夜里黑伞女孩把十二点的怀表放上柜台",
        },
    ]
    timings = _build_continuous_audio_scene_timings(scenes, 3.2, raw_boundaries)
    assert timings is not None

    aligned = _frame_aligned_scene_durations(
        [timing.end_seconds - timing.start_seconds for timing in timings],
        8,
        audio_timings=timings,
    )

    assert aligned == [1.125, 1.0, 1.125]


def test_portfolio_registers_fitted_narration_as_derived_artifact(tmp_path: Path) -> None:
    import asyncio
    import io
    import shutil
    import wave

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for narration duration fitting")

    async def exercise() -> None:
        from app.domain.models import (
            ArtifactSummary,
            GenerationTaskKind,
            GenerationTaskRecord,
            TaskStatus,
        )
        from app.media.audio_duration import FFmpegAudioDurationFitter
        from app.media.audio_validation import FFprobeAudioValidator
        from app.repositories.in_memory import InMemoryStore
        from app.storage.local import LocalFileArtifactStorage

        store = InMemoryStore()
        storage = LocalFileArtifactStorage(tmp_path)
        episode_id = uuid4()
        project_id = uuid4()
        source_buffer = io.BytesIO()
        with wave.open(source_buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * round(8000 * 1.5))
        stored = await storage.put_bytes(
            f"audio/{episode_id}/source.wav",
            source_buffer.getvalue(),
            "audio/wav",
        )
        source_artifact = ArtifactSummary(
            type="audio_narration",
            provider="mock",
            metadata={
                "storage_key": stored.storage_key,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "sha256": stored.sha256,
                "duration_seconds": 1.5,
            },
        )
        task = GenerationTaskRecord(
            project_id=project_id,
            kind=GenerationTaskKind.AUDIO_NARRATION,
            status=TaskStatus.SUCCEEDED,
            artifacts=[source_artifact],
        )
        await store.create_task(task)

        derived, metadata = await _fit_narration_artifact_to_shot(
            store,
            storage,
            task,
            source_artifact,
            episode_id,
            1,
            1.4,
            FFmpegAudioDurationFitter(timeout_seconds=30),
            FFprobeAudioValidator(timeout_seconds=30),
        )

        assert derived.provider == "ffmpeg_atempo"
        assert derived.metadata["source_artifact_id"] == str(source_artifact.id)
        assert metadata["derived_artifact"] is True
        saved = await store.get_task(task.id)
        assert saved is not None
        assert len(saved.artifacts) == 2
        fitted_bytes = await storage.get_bytes(str(derived.metadata["storage_key"]))
        assert fitted_bytes
        await storage.close()

    asyncio.run(exercise())


def test_portfolio_assembly_accepts_one_narration_track_per_ten_shots() -> None:
    from app.domain.models import AudioTrackRequest, VideoAssemblyCreateRequest

    request = VideoAssemblyCreateRequest(
        clip_task_ids=[uuid4() for _ in range(10)],
        audio_tracks=[
            AudioTrackRequest(artifact_id=uuid4(), start_seconds=float(index))
            for index in range(10)
        ],
    )

    assert len(request.audio_tracks) == 10


def test_portfolio_runner_exposes_references_only_mode() -> None:
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "--references-only" in source
    assert '"sample_mode": "references_only"' in source
    assert '"clip_count": 0' in source
    assert "--references-only cannot be combined with --resume" in source


def test_references_only_runs_with_empty_environment_scene(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    monkeypatch.setenv("AI_VIDEO_STORAGE_BASE_PATH", str(tmp_path / "artifacts"))
    output = tmp_path / "sample"
    args = argparse.Namespace(
        config="config/config.example.toml", output_dir=str(output), shots=1,
        shot_duration=3, quality_profile="local_safe", mock_media=True,
        preview_only=False, reuse_recent_references=False, references_only=True,
        reference_manifest=None, shot_keyframe_mode="auto", resume=False,
        stop_after_shot=None,
    )
    report = asyncio.run(_MODULE.run_sample(args))
    assert report["sample_mode"] == "references_only"
    assert report["clip_count"] == 0
    assert report["reference_image_count"] == 4
    assert report["formal_portfolio_eligible"] is False
    checkpoint = json.loads((output / "run-checkpoint.json").read_text())
    assert checkpoint["completed_shots"] == []
    assert Path(report["reference_manifest"]).is_file()
    import shlex
    command = shlex.split(report["next_command"])
    assert "--mock-media" in command
    assert command[command.index("--shot-keyframe-mode") + 1] == "auto"
    assert command[command.index("--output-dir") + 1] == str(output)
    assert "--references-only" not in command
    manifest = json.loads(Path(report["reference_manifest"]).read_text())
    assert all(item["placeholder"] and item["source_path"] is None
               for item in manifest["assets"].values())


@pytest.mark.parametrize("preview", [False, True])
def test_resume_command_keeps_reference_binding_and_keyframe_mode(preview) -> None:
    import shlex

    args = argparse.Namespace(shots=1, shot_duration=3, quality_profile="local_safe",
                              shot_keyframe_mode="off", mock_media=False, preview_only=preview)
    command = shlex.split(_MODULE._resume_command(
        args, "config/my config.toml", Path("output with spaces/manifest.json"),
        Path("output with spaces"),
    ))
    assert command[command.index("--config") + 1] == "config/my config.toml"
    assert command[command.index("--shot-keyframe-mode") + 1] == "off"
    assert command[command.index("--reference-manifest") + 1] == str(Path("output with spaces/manifest.json"))
    assert command[command.index("--output-dir") + 1] == "output with spaces"
    assert "--reuse-recent-references" in command
    assert ("--preview-only" in command) is preview
    assert "--mock-media" not in command
