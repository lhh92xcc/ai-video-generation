#!/usr/bin/env python3
"""Run the reproducible Mac 16GB local target Demo.

The sample intentionally uses the project's real task services and Artifact
contracts, while keeping the media stack local and small:

    Ollama story fixture -> Flux Schnell reference images -> optional PuLID shot
    keyframes -> Wan2.1 I2V clips -> one continuous episode narration
    -> WordBoundary scene subtitles -> FFmpeg assembly

It is an execution/demo script, not a second application entry point. The
in-memory store keeps the run dependency-free; generated media is persisted to
the configured local Artifact directory and copied to a stable output path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple
from uuid import UUID, uuid4

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import Settings, load_settings
from app.domain.models import (
    AssetRecord,
    AssetStatus,
    AssetType,
    ArtifactSummary,
    AudioNarrationCreateRequest,
    CharacterAssetContent,
    EpisodeOutlineContent,
    EpisodeRecord,
    EpisodeScriptContent,
    EpisodeScriptRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    LocationAssetContent,
    PropAssetContent,
    ReferenceImageCreateRequest,
    ReferenceImageRecord,
    ReferenceImageStatus,
    ShotAssetReference,
    ShotContent,
    ShotListRecord,
    StageName,
    StageRun,
    StoryBibleContent,
    StoryBibleRecord,
    SubtitleCreateRequest,
    SubtitleCueRequest,
    TaskStatus,
    VideoAssemblyCreateRequest,
    VideoClipCreateRequest,
    AudioTrackRequest,
    utc_now,
)
from app.media.audio_duration import FFmpegAudioDurationFitter
from app.media.audio_pause_compaction import FFmpegNarrationPauseCompactor
from app.media.narration_text import join_narration_units_with_boundaries
from app.media.pronunciation import load_pronunciation_dictionary
from app.media.identity_audit import IdentityConsistencyAuditor
from app.media.audio_validation import FFprobeAudioValidator
from app.media.video_duration import FFmpegVideoTailExtender, FFmpegVideoTrimmer
from app.media.video_validation import FFprobeVideoValidator
from app.media.visual_prompts import (
    DEFAULT_REFERENCE_NEGATIVE_PROMPT,
    DEFAULT_REFERENCE_STYLE,
    DEFAULT_VIDEO_NEGATIVE_PROMPT,
)
from app.providers.factory import (
    create_image_generation_provider,
    create_identity_image_generation_provider,
    create_tts_provider,
    create_video_generation_provider,
)
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.reference_image_service import ReferenceImageTaskService
from app.services.subtitle_service import SubtitleTaskService
from app.services.tts_service import TTSTaskService
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.video_clip_service import VideoClipTaskService
from app.storage.local import LocalFileArtifactStorage
from app.rendering.ffmpeg_renderer import FFmpegVideoRenderer


NarrationScene = tuple[str, str, str]


PORTFOLIO_REPORT_SCHEMA_VERSION = 2
PORTFOLIO_TARGET = {
    "profile_id": "portfolio-demo-v1",
    "duration_seconds": {"min": 45, "max": 60},
    "aspect_ratio": "9:16",
    "shot_count": {"min": 8, "max": 12},
    "orientation": "vertical",
    "style": "2D manhwa / dynamic comic",
}
PORTFOLIO_HUMAN_REVIEW_ITEMS = (
    {
        "id": "story_fidelity",
        "label": "改编忠实度",
        "description": "剧本和分镜没有偏离原文核心人物、冲突与结局。",
    },
    {
        "id": "character_identity",
        "label": "角色一致性",
        "description": "参考图与各镜头中的脸型、发型、服装和色彩保持一致。",
    },
    {
        "id": "motion_continuity",
        "label": "动作与镜头",
        "description": "没有明显变脸、肢体崩坏、跳切或图片拼接感。",
    },
    {
        "id": "voice_naturalness",
        "label": "声音自然度",
        "description": "语速、停顿、发音和场景之间的衔接可以正常听清。",
    },
    {
        "id": "subtitle_alignment",
        "label": "字幕同步",
        "description": "字幕文本正确，出现和消失时间与声音基本一致。",
    },
    {
        "id": "final_story_flow",
        "label": "成片观感",
        "description": "完整观看后节奏、信息密度和竖屏构图适合作品集展示。",
    },
)


def _report_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _report_probe(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, dict):
        return {}
    nested = metadata.get("ffprobe")
    return nested if isinstance(nested, dict) else metadata


def _portfolio_readiness_report(
    *,
    args: argparse.Namespace,
    shot_count: int,
    reference_image_count: int,
    clip_count: int,
    narration_artifact: ArtifactSummary | None,
    subtitle_artifact: ArtifactSummary | None,
    subtitle_metadata: dict[str, object] | None,
    rendered_artifact: ArtifactSummary | None,
    clip_task_snapshots: list[GenerationTaskRecord],
) -> dict[str, object]:
    """Build an honest, machine-readable portfolio acceptance report.

    The report intentionally keeps human review separate from deterministic
    checks.  A successful Provider call, FFprobe result or identity helper
    result cannot prove that a finished sample is pleasant to watch.
    """

    rendered_metadata = rendered_artifact.metadata if rendered_artifact is not None else {}
    probe = _report_probe(rendered_metadata)
    width = int(_report_number(probe.get("width"), 0))
    height = int(_report_number(probe.get("height"), 0))
    duration = _report_number(
        probe.get("duration_seconds"),
        _report_number(rendered_metadata.get("duration_ms"), 0.0) / 1000,
    )
    ratio = width / height if width > 0 and height > 0 else 0.0
    expected_ratio = 9 / 16
    vertical = ratio > 0 and abs(ratio - expected_ratio) <= 0.04
    narration_metadata = narration_artifact.metadata if narration_artifact is not None else {}
    subtitle_metadata = subtitle_metadata or {}
    audio_duration = _report_number(narration_metadata.get("duration_seconds"))
    cue_count = int(_report_number(subtitle_metadata.get("cue_count"), 0))

    def count_status(count: int, target: int) -> str:
        if count == target:
            return "passed"
        return "pending" if count < target else "failed"

    checks: list[dict[str, object]] = [
        {
            "id": "reference_images",
            "label": "真实参考图",
            "status": "not_applicable" if args.mock_media else "passed" if reference_image_count > 0 else "pending",
            "blocking": not args.mock_media,
            "evidence": f"{reference_image_count} 个参考图任务/复用引用",
        },
        {
            "id": "video_clips",
            "label": "逐镜头视频片段",
            "status": count_status(clip_count, shot_count),
            "blocking": True,
            "evidence": f"{clip_count}/{shot_count} 个镜头已生成并通过 Artifact 校验",
        },
        {
            "id": "shot_budget",
            "label": "镜头规模",
            "status": "passed" if 8 <= shot_count <= 12 else "failed",
            "blocking": True,
            "evidence": f"{shot_count} 个镜头 · 目标 8～12 个",
        },
        {
            "id": "narration",
            "label": "连续旁白",
            "status": "passed" if audio_duration > 0 else "pending",
            "blocking": True,
            "evidence": (
                f"{audio_duration:.3f} 秒 · {narration_metadata.get('content_type', 'unknown')}"
                if narration_artifact is not None
                else "尚未生成连续旁白 Artifact"
            ),
        },
        {
            "id": "subtitles",
            "label": "字幕 Artifact",
            "status": "passed" if cue_count > 0 else "pending",
            "blocking": True,
            "evidence": (
                f"{cue_count} 条 cue · {subtitle_metadata.get('alignment_precision', 'unknown')}"
                if subtitle_artifact is not None
                else "尚未生成字幕 Artifact"
            ),
        },
        {
            "id": "vertical_output",
            "label": "竖屏输出",
            "status": "passed" if vertical else "pending" if rendered_artifact is None else "failed",
            "blocking": True,
            "evidence": f"{width}×{height} · 目标 9:16",
        },
        {
            "id": "duration_target",
            "label": "目标时长",
            "status": "passed" if 45 <= duration <= 60 else "pending" if rendered_artifact is None else "failed",
            "blocking": True,
            "evidence": f"{duration:.3f} 秒 · 目标 45～60 秒",
        },
    ]

    identity_reports: list[dict[str, object]] = []
    for task in clip_task_snapshots:
        for artifact in task.artifacts:
            report = artifact.metadata.get("identity_audit")
            if isinstance(report, dict):
                identity_reports.append(report)
    identity_statuses = [str(report.get("status", "")) for report in identity_reports]
    if args.mock_media:
        identity_status = "not_applicable"
        identity_evidence = "Mock 媒体不会执行真实身份审核"
    elif not identity_statuses:
        identity_status = "pending"
        identity_evidence = "尚未登记身份审核结果；必须人工抽查参考图和视频"
    elif any(status in {"failed", "no_face", "reference_no_face", "error"} for status in identity_statuses):
        identity_status = "failed"
        identity_evidence = f"{sum(status in {'failed', 'no_face', 'reference_no_face', 'error'} for status in identity_statuses)}/{len(identity_statuses)} 个身份审核结果异常"
    else:
        identity_status = "passed"
        identity_evidence = f"{sum(status == 'passed' for status in identity_statuses)}/{len(identity_statuses)} 个身份审核通过"
    checks.append(
        {
            "id": "identity_audit",
            "label": "身份自动初审",
            "status": identity_status,
            "blocking": identity_status == "failed",
            "evidence": identity_evidence,
        }
    )

    blocking_checks = [item for item in checks if item["blocking"]]
    blocking_failures = [
        item for item in blocking_checks if item["status"] != "passed"
    ]
    machine_passed = sum(item["status"] == "passed" for item in blocking_checks)
    human_review = [
        {
            **item,
            "required": True,
            "status": "pending",
            "reviewer": "",
            "reviewed_at": "",
            "score": None,
            "notes": "",
        }
        for item in PORTFOLIO_HUMAN_REVIEW_ITEMS
    ]
    return {
        "schema_version": PORTFOLIO_REPORT_SCHEMA_VERSION,
        "target": PORTFOLIO_TARGET,
        "readiness": {
            "status": "ready_for_human_review" if not blocking_failures else "incomplete",
            "ready_for_portfolio": False,
            "machine_checks_passed": machine_passed,
            "machine_checks_total": len(blocking_checks),
            "human_checks_completed": 0,
            "human_checks_total": len(human_review),
            "blocked_reasons": [str(item["evidence"]) for item in blocking_failures],
            "next_action": (
                "先处理机器门禁失败项，再进行人工画面、声音和字幕验收。"
                if blocking_failures
                else "请完成全部人工验收清单；自动检查不能替代完整观看和听审。"
            ),
        },
        "machine_checks": checks,
        "human_review": {
            "required": True,
            "status": "pending",
            "items": human_review,
            "note": "请在复制的审核模板或作品集记录中填写；不要把 pending 写成通过。",
        },
    }


class NarrationTimelineSegment(NamedTuple):
    """One scene narration placed on the measured video timeline."""

    scene_index: int
    text: str
    start_seconds: float
    end_seconds: float
    audio_duration_seconds: float
    shot_start_seconds: float
    shot_end_seconds: float
    crosses_shot_boundary: bool
    starts_before_shot_boundary: bool
    speech_start_seconds: float
    speech_end_seconds: float


class ContinuousSceneAudioTiming(NamedTuple):
    """Provider-measured timing for one scene inside a continuous waveform."""

    start_seconds: float
    end_seconds: float
    speech_start_seconds: float
    speech_end_seconds: float

# Keep the fixture's ten-shot plan aligned with the ShotContent protocol.  The
# ninth entry used to put the camera movement token ``dolly`` in ``shot_size``;
# that stayed hidden while the Wan smoke stopped at eight shots.
_PORTFOLIO_SHOT_SIZES: tuple[str, ...] = (
    "wide",
    "wide",
    "medium",
    "close_up",
    "close_up",
    "medium",
    "medium",
    "over_the_shoulder",
    "close_up",
    "medium",
)
_PORTFOLIO_CAMERA_MOVEMENTS: tuple[str, ...] = (
    "dolly",
    "pan",
    "tracking",
    "zoom",
    "zoom",
    "pan",
    "tracking",
    "dolly",
    "zoom",
    "zoom",
)

# Visual shots are deliberately not speech units.  The fixture carries a
# small number of editorial sentence boundaries in the narration text itself;
# the runner uses empty separators between visual units so a shot cut cannot
# manufacture a new pause or restart the voice's cadence.

# Legacy duration-fit ceiling retained for helper/test compatibility. The
# portfolio path does not retime the narration waveform; if the audio needs
# more visual time, it extends the clip with a traceable tail-frame hold.
_PORTFOLIO_MAX_NARRATION_TEMPO = 1.08
# Wan output is quantized to its frame duration. Keep this legacy constant for
# helper/test compatibility; the current continuous-track path does not create
# a following narration track at each visual cut.
_PORTFOLIO_MAX_NARRATION_TRANSITION_OVERLAP = 0.25
# Visual cuts are more frequent than spoken sentence boundaries. The narration
# builder below keeps only editorial punctuation from the story script, while
# the visual timeline is balanced independently from those spoken boundaries.

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.local.toml")
    parser.add_argument("--output-dir", default=".tmp/portfolio-sample")
    parser.add_argument("--shots", type=int, choices=tuple(range(1, 11)), default=10)
    parser.add_argument(
        "--shot-duration",
        type=int,
        choices=(3, 4, 5),
        default=5,
        help="Seconds per Wan I2V shot; use 3 for the first hardware smoke.",
    )
    parser.add_argument(
        "--mock-media",
        action="store_true",
        help="Use Mock image/TTS and local fixture video for orchestration tests.",
    )
    parser.add_argument(
        "--reuse-recent-references",
        action="store_true",
        help="Reuse the four newest local PNG Artifacts instead of generating Flux references.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume completed video shots from output-dir/run-checkpoint.json.",
    )
    parser.add_argument(
        "--stop-after-shot",
        type=int,
        choices=tuple(range(1, 11)),
        default=None,
        help="Pause after this shot; useful for validating checkpoint recovery.",
    )
    return parser.parse_args()


CHECKPOINT_VERSION = 1


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "run-checkpoint.json"


def _new_checkpoint(args: argparse.Namespace) -> dict[str, object]:
    return {
        "schema_version": CHECKPOINT_VERSION,
        "status": "running",
        "created_at": _utc_timestamp(),
        "updated_at": _utc_timestamp(),
        "shots_requested": args.shots,
        "shot_duration_seconds": args.shot_duration,
        "mock_media": bool(args.mock_media),
        "shots": {},
    }


def _load_or_create_checkpoint(
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    path = _checkpoint_path(output_dir)
    if not args.resume:
        if path.exists():
            raise RuntimeError(
                f"Checkpoint already exists: {path}. Use --resume to continue or choose a new --output-dir."
            )
        checkpoint = _new_checkpoint(args)
        _write_checkpoint(path, checkpoint)
        return checkpoint

    if not path.is_file():
        raise RuntimeError(f"--resume requires an existing checkpoint: {path}")
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Checkpoint is not valid JSON: {path}") from exc
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != CHECKPOINT_VERSION:
        raise RuntimeError(f"Unsupported checkpoint schema: {path}")
    original_shots = checkpoint.get("shots_requested")
    if not isinstance(original_shots, int):
        raise RuntimeError("Checkpoint is missing its original shots value")
    if args.shots < original_shots:
        raise RuntimeError("--resume cannot reduce the original --shots value")
    if checkpoint.get("shot_duration_seconds") != args.shot_duration:
        raise RuntimeError("--resume must use the same --shot-duration value as the original run")
    if bool(checkpoint.get("mock_media")) != bool(args.mock_media):
        raise RuntimeError("--resume must use the same --mock-media mode as the original run")
    if not isinstance(checkpoint.get("shots"), dict):
        raise RuntimeError("Checkpoint is missing its shots map")
    if args.shots > original_shots:
        checkpoint["extended_from_shots"] = original_shots
        checkpoint["shots_requested"] = args.shots
    checkpoint["status"] = "running"
    checkpoint["updated_at"] = _utc_timestamp()
    _write_checkpoint(path, checkpoint)
    return checkpoint


def _write_checkpoint(path: Path, checkpoint: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint["updated_at"] = _utc_timestamp()
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _shot_checkpoint_entry(
    checkpoint: dict[str, object],
    shot_index: int,
) -> dict[str, object] | None:
    shots = checkpoint.get("shots")
    if not isinstance(shots, dict):
        return None
    entry = shots.get(str(shot_index))
    return entry if isinstance(entry, dict) else None


def _set_shot_checkpoint_entry(
    checkpoint: dict[str, object],
    shot_index: int,
    entry: dict[str, object],
) -> None:
    shots = checkpoint.setdefault("shots", {})
    if not isinstance(shots, dict):
        raise RuntimeError("Checkpoint shots map is invalid")
    shots[str(shot_index)] = entry


def _stable_shot_path(output_dir: Path, shot_index: int) -> Path:
    return output_dir / "shots" / f"shot-{shot_index:02d}.mp4"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_checkpointed_clip(
    output_dir: Path,
    shot_index: int,
    entry: dict[str, object] | None,
) -> bytes | None:
    if not entry or entry.get("status") != "succeeded":
        return None
    path_value = entry.get("output_path")
    path = Path(str(path_value)) if isinstance(path_value, str) else _stable_shot_path(output_dir, shot_index)
    if not path.is_file():
        return None
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"Cannot read checkpointed shot {shot_index} from {path}: {exc}"
        ) from exc
    expected_sha = entry.get("sha256")
    if isinstance(expected_sha, str) and expected_sha and _sha256_bytes(content) != expected_sha:
        return None
    return content


async def _register_checkpointed_clip_task(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    episode: EpisodeRecord,
    shot_list: ShotListRecord,
    shot: ShotContent,
    content: bytes,
    checkpoint_entry: dict[str, object],
) -> UUID:
    """Rehydrate a completed clip into the current in-memory task graph."""

    storage_key = (
        f"video-clips/{episode.id}/shot-{shot.shot_index}/"
        f"resume-{uuid4()}.mp4"
    )
    stored = await storage.put_bytes(storage_key, content, "video/mp4")
    checkpoint_metadata = checkpoint_entry.get("metadata", {})
    metadata = (
        dict(checkpoint_metadata)
        if isinstance(checkpoint_metadata, dict)
        else {}
    )
    metadata.update(
        {
            "storage_key": stored.storage_key,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "resumed_from_checkpoint": True,
            "motion": "wan2.1_i2v" if checkpoint_entry.get("provider") == "comfyui_wan_i2v" else "local_fixture",
        }
    )
    task = GenerationTaskRecord(
        id=uuid4(),
        project_id=episode.project_id,
        kind=GenerationTaskKind.VIDEO_CLIP,
        input_data={
            "episode_id": str(episode.id),
            "shot_list_id": str(shot_list.id),
            "shot_index": shot.shot_index,
            "duration_seconds": shot.duration_seconds,
            "prompt": shot.visual_prompt,
            "negative_prompt": DEFAULT_VIDEO_NEGATIVE_PROMPT,
            "asset_refs": [reference.model_dump(mode="json") for reference in shot.asset_refs],
        },
        status=TaskStatus.SUCCEEDED,
        current_stage=None,
        progress=100,
        stages=[
            StageRun(
                stage=StageName.VIDEO_CLIP,
                status=TaskStatus.SUCCEEDED,
                progress=100,
                finished_at=utc_now(),
            )
        ],
        artifacts=[
            ArtifactSummary(
                type="video_clip",
                provider=str(checkpoint_entry.get("provider", "checkpoint")),
                metadata=metadata,
                preview={"shot_index": shot.shot_index, "output_uri": stored.uri},
            )
        ],
    )
    await store.create_task(task)
    return task.id


def _narration_scenes() -> list[NarrationScene]:
    return [
        (
            "旧城区的钟表店",
            "旧城区的钟表店里，林默每晚都能听见墙后传来三下钟声，",
            "cinematic vertical establishing shot of an old clock shop in a rainy Chinese city at night, warm window light, mysterious atmosphere",
        ),
        (
            "墙后的三下钟声",
            "今晚第三声落下，店里的钟表指针同时停住，",
            "cinematic vertical interior shot of an old Chinese clock shop at midnight, dozens of mechanical clocks frozen at different times, rain on the window, warm amber light",
        ),
        (
            "停在十二点的怀表",
            "雨夜里，黑伞女孩把十二点的怀表放上柜台，",
            "cinematic vertical medium shot inside a vintage clock shop, a mysterious young Chinese woman with a black umbrella places an antique bronze pocket watch on the counter",
        ),
        (
            "来访者的警告",
            "她只说：听见第四声，你就回不到今天；",
            "cinematic vertical close-up of a mysterious young Chinese woman in a black coat inside a vintage clock shop, holding a bronze pocket watch, tense eye contact, rainy night",
        ),
        (
            "一分钟的秘密",
            "怀表重新走动，整条街倒退一分钟，",
            "cinematic vertical close-up of an antique bronze pocket watch at twelve o'clock, clock gears and dust in warm light, suspenseful fantasy drama",
        ),
        (
            "少了一格的时间",
            "秒针逆行，灰尘归位，雨声突然消失，",
            "cinematic vertical close-up of antique wall clocks in a Chinese clock shop, second hands moving backward, dust reversing in the air, supernatural warm and blue lighting",
        ),
        (
            "凝固的雨声",
            "门外人群凝固，像一张被按下暂停的照片，",
            "cinematic vertical dramatic shot of a young Chinese clockmaker in a dark coat inside a clock shop, frozen rainy street visible through the window, supernatural stillness",
        ),
        (
            "不该打开的门",
            "女孩指向旧地图：钟声来自一扇禁门；",
            "cinematic vertical over-the-shoulder shot of a mysterious woman pointing at an old city map behind a clock shop wall, hidden doorway, blue and amber lighting",
        ),
        (
            "暗缝里的台阶",
            "林默推开木板，沿地下台阶走进黑暗，",
            "cinematic vertical shot looking down into a hidden basement staircase behind an old map in a clock shop, bronze pocket watch glowing in a young man's hand, blue darkness and amber rim light",
        ),
        (
            "明天的自己",
            "最后一声钟响，他看见明天的自己摇头。",
            "cinematic vertical fantasy finale in a hidden underground clock room, young Chinese clockmaker holding a bronze pocket watch, a future version of himself reflected in darkness",
        ),
    ]


async def _video_clip_durations(
    store: InMemoryStore,
    clip_task_ids: list[UUID],
) -> list[float]:
    """Read the playable duration of every completed clip Artifact."""

    durations: list[float] = []
    for task_id in clip_task_ids:
        task = await store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"video clip task {task_id} is missing from the run graph")
        artifact = next(
            (item for item in task.artifacts if item.type == "video_clip"),
            None,
        )
        if artifact is None:
            raise RuntimeError(f"video clip task {task_id} has no video_clip Artifact")
        ffprobe = artifact.metadata.get("ffprobe")
        duration = ffprobe.get("duration_seconds") if isinstance(ffprobe, dict) else None
        if not isinstance(duration, (int, float)):
            duration = artifact.metadata.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise RuntimeError(f"video clip task {task_id} has no valid duration metadata")
        durations.append(float(duration))
    return durations


async def _total_video_duration_seconds(
    store: InMemoryStore,
    clip_task_ids: list[UUID],
) -> float:
    """Read and sum the playable durations of completed clip Artifacts."""

    return sum(await _video_clip_durations(store, clip_task_ids))


def _safe_clip_duration_for_narration(
    clip_duration_seconds: float,
    narration_duration_seconds: float,
    *,
    max_tempo_factor: float = _PORTFOLIO_MAX_NARRATION_TEMPO,
    padding_seconds: float = 0.08,
) -> float:
    """Return the visual budget needed without unsafe speech acceleration."""

    if clip_duration_seconds <= 0 or narration_duration_seconds <= 0:
        raise ValueError("clip and narration durations must be positive")
    if max_tempo_factor <= 1:
        raise ValueError("max_tempo_factor must be greater than 1")
    if padding_seconds < 0:
        raise ValueError("padding_seconds must not be negative")
    if narration_duration_seconds <= clip_duration_seconds:
        return round(clip_duration_seconds, 6)
    return round(
        max(
            clip_duration_seconds,
            narration_duration_seconds / max_tempo_factor + padding_seconds,
        ),
        6,
    )


def _frame_aligned_duration(duration_seconds: float, fps: int) -> float:
    """Round a visual budget up so FFmpeg cannot end before the speech cue."""

    if duration_seconds <= 0 or fps <= 0:
        raise ValueError("duration_seconds and fps must be positive")
    return round(math.ceil(duration_seconds * fps - 1e-9) / fps, 6)


def _frame_aligned_scene_durations(
    scene_durations: list[float],
    fps: int,
    audio_timings: list[ContinuousSceneAudioTiming] | None = None,
) -> list[float]:
    """Round cumulative scene boundaries without cutting through a speech beat."""

    if not scene_durations or any(duration <= 0 for duration in scene_durations):
        raise ValueError("scene_durations must contain only positive values")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if audio_timings is not None and len(audio_timings) != len(scene_durations):
        raise ValueError("audio_timings must have one item per scene")
    aligned: list[float] = []
    previous_boundary = 0.0
    audio_boundary = 0.0
    for index, duration in enumerate(scene_durations):
        audio_boundary += duration
        if audio_timings is not None and index < len(scene_durations) - 1:
            # If the provider left a real pause, put the cut in that pause.
            # When two scene ranges are contiguous (the preferred narration
            # mode), there is no good pause to target; use the nearest frame
            # to the character boundary so the visual cut does not drift an
            # entire frame toward either scene.
            speech_end = audio_timings[index].speech_end_seconds
            next_speech_start = audio_timings[index + 1].speech_start_seconds
            pause_seconds = next_speech_start - speech_end
            if pause_seconds >= 1 / fps:
                boundary = math.floor(next_speech_start * fps - 1e-9) / fps
                earliest_safe_frame = _frame_aligned_duration(speech_end, fps)
                if boundary < earliest_safe_frame:
                    boundary = earliest_safe_frame
            else:
                boundary = round(audio_boundary * fps) / fps
                if boundary < speech_end:
                    boundary = _frame_aligned_duration(speech_end, fps)
        else:
            # Keep the final frame at or after the complete audio waveform so
            # its trailing provider padding is never truncated.
            boundary = _frame_aligned_duration(audio_boundary, fps)
        if boundary <= previous_boundary:
            boundary = previous_boundary + 1 / fps
        aligned.append(round(boundary - previous_boundary, 6))
        previous_boundary = boundary
    return aligned


def _estimate_continuous_scene_durations(
    scenes: list[NarrationScene],
    total_audio_duration: float,
) -> list[float]:
    """Allocate one continuous narration across scenes for visual timing.

    The audio itself is one provider waveform. Scene boundaries are only used
    for subtitle cues and for distributing tail-frame holds across shots; they
    are deliberately a text-weighted estimate until a forced-alignment
    provider is selected.
    """

    if not scenes or total_audio_duration <= 0:
        raise ValueError("scenes and total_audio_duration must be positive")
    weights = [max(1, len(text.strip())) for _title, text, _prompt in scenes]
    total_weight = sum(weights)
    durations: list[float] = []
    allocated = 0.0
    for index, weight in enumerate(weights, start=1):
        if index == len(weights):
            duration = total_audio_duration - allocated
        else:
            duration = total_audio_duration * weight / total_weight
        if duration <= 0:
            raise ValueError("continuous narration scene durations must be positive")
        durations.append(round(duration, 6))
        allocated += durations[-1]
    durations[-1] = round(total_audio_duration - sum(durations[:-1]), 6)
    return durations


def _balanced_visual_scene_durations(
    clip_durations: list[float],
    total_audio_duration: float,
    fps: int,
) -> list[float]:
    """Keep visual cuts even while making room for one continuous narration.

    The old portfolio path made every Wan clip end at the corresponding
    narration scene boundary.  That turned punctuation pauses into apparent
    audio seams.  This policy preserves each source clip's minimum frame
    budget, distributes any required extra frames evenly, and leaves the
    narration/subtitle timeline independent from visual cuts.
    """

    if not clip_durations or any(duration <= 0 for duration in clip_durations):
        raise ValueError("clip_durations must contain only positive values")
    if total_audio_duration <= 0 or fps <= 0:
        raise ValueError("total_audio_duration and fps must be positive")

    minimum_frames = [
        math.ceil(duration * fps - 1e-9) for duration in clip_durations
    ]
    minimum_total_frames = sum(minimum_frames)
    target_total_frames = max(
        minimum_total_frames,
        math.ceil(total_audio_duration * fps - 1e-9),
    )
    extra_frames = target_total_frames - minimum_total_frames
    base_extra, remainder = divmod(extra_frames, len(minimum_frames))
    target_frames = [
        frames + base_extra + (1 if index < remainder else 0)
        for index, frames in enumerate(minimum_frames)
    ]
    return [round(frames / fps, 6) for frames in target_frames]


def _build_continuous_narration_text(scenes: list[NarrationScene]) -> str:
    """Build one continuous narration stream from connected story beats.

    Explicit editorial punctuation is important here.  The fixture's scene
    strings already contain the punctuation that belongs to the spoken story;
    visual boundaries therefore use empty separators.  The provider still
    receives one request and returns one waveform, while visual cuts remain a
    separate editing concern.
    """

    if not scenes:
        raise ValueError("scenes must not be empty")
    continuous_text = join_narration_units_with_boundaries(
        [text for _title, text, _prompt in scenes],
        boundary_separators=[""] * max(0, len(scenes) - 1),
        final_separator="",
        preserve_unit_terminal_punctuation=True,
    )
    if not continuous_text:
        raise ValueError("scenes must contain narration text")
    return continuous_text


def _timing_text(text: str) -> str:
    """Remove punctuation so provider word events can be matched to scenes."""

    return "".join(character for character in text if character.isalnum())


def _continuous_scene_boundary_character_offsets(
    scenes: list[NarrationScene],
) -> set[int]:
    """Return normalized character offsets immediately after each visual scene."""

    offsets: set[int] = set()
    cursor = 0
    for _index, (_title, text, _prompt) in enumerate(scenes[:-1]):
        cursor += len(_timing_text(text))
        offsets.add(cursor)
    return offsets


def _build_continuous_audio_scene_timings(
    scenes: list[NarrationScene],
    total_audio_duration: float,
    raw_word_boundaries: object,
) -> list[ContinuousSceneAudioTiming] | None:
    """Map one continuous Edge TTS waveform back to scene character ranges.

    Chinese Edge TTS word events can group characters from both sides of a
    visual cut (for example ``门林默`` when no artificial punctuation is
    inserted). Treat each event as a timed character span and interpolate only
    inside that event instead of rejecting the entire mapping. This preserves
    a continuous audio track while keeping scene cues and frame cuts ordered.
    """

    if not scenes or total_audio_duration <= 0 or not isinstance(raw_word_boundaries, list):
        return None

    # Store both the provider time range and the character range represented by
    # that event. The character range lets us map a scene boundary that falls
    # in the middle of a provider token without inserting punctuation into the
    # TTS request merely to make bookkeeping easier.
    boundaries: list[tuple[int, int, float, float, str]] = []
    source_character_cursor = 0
    for item in raw_word_boundaries:
        if not isinstance(item, dict):
            return None
        text = item.get("text")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        if not isinstance(text, str) or not _timing_text(text):
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        start_seconds = float(start)
        end_seconds = float(end)
        if start_seconds < 0 or end_seconds <= start_seconds:
            return None
        normalized_text = _timing_text(text)
        token_start = source_character_cursor
        source_character_cursor += len(normalized_text)
        boundaries.append(
            (
                token_start,
                source_character_cursor,
                start_seconds,
                end_seconds,
                normalized_text,
            )
        )
    if not boundaries:
        return None

    target_lengths = [
        len(_timing_text(text))
        for _title, text, _prompt in scenes
    ]
    if any(length <= 0 for length in target_lengths):
        return None
    total_target_characters = sum(target_lengths)
    if source_character_cursor != total_target_characters:
        return None

    def _interpolate_time(
        token_start: int,
        token_end: int,
        start_seconds: float,
        end_seconds: float,
        character_offset: int,
    ) -> float:
        if character_offset <= token_start:
            return start_seconds
        if character_offset >= token_end:
            return end_seconds
        fraction = (character_offset - token_start) / (token_end - token_start)
        return start_seconds + (end_seconds - start_seconds) * fraction

    def _time_at_character(character_offset: int) -> float:
        if character_offset <= 0:
            return boundaries[0][2]
        if character_offset >= total_target_characters:
            return boundaries[-1][3]
        for token_index, (
            token_start,
            token_end,
            start_seconds,
            end_seconds,
            _token_text,
        ) in enumerate(boundaries):
            if character_offset < token_end:
                return _interpolate_time(
                    token_start,
                    token_end,
                    start_seconds,
                    end_seconds,
                    character_offset,
                )
            if character_offset == token_end:
                # When the scene ends exactly at a provider token boundary,
                # put the visual cut in the provider's inter-token gap. This
                # preserves the provider's own pause around the soft comma.
                if token_index + 1 < len(boundaries):
                    return boundaries[token_index + 1][2]
                return end_seconds
        return boundaries[-1][3]

    def _speech_start_at_character(character_offset: int) -> float:
        for token_start, token_end, start_seconds, end_seconds, _token_text in boundaries:
            if character_offset < token_end:
                return _interpolate_time(
                    token_start,
                    token_end,
                    start_seconds,
                    end_seconds,
                    character_offset,
                )
        return boundaries[-1][3]

    def _speech_end_at_character(character_offset: int) -> float:
        previous_end = boundaries[0][2]
        for token_start, token_end, start_seconds, end_seconds, _token_text in boundaries:
            if character_offset <= token_start:
                return previous_end
            if character_offset < token_end:
                return _interpolate_time(
                    token_start,
                    token_end,
                    start_seconds,
                    end_seconds,
                    character_offset,
                )
            previous_end = end_seconds
        return boundaries[-1][3]

    timings: list[ContinuousSceneAudioTiming] = []
    scene_character_cursor = 0
    for scene_index, target_length in enumerate(target_lengths):
        scene_start_character = scene_character_cursor
        scene_character_cursor += target_length
        scene_end_character = scene_character_cursor
        scene_start = (
            0.0
            if scene_index == 0
            else _time_at_character(scene_start_character)
        )
        scene_end = (
            total_audio_duration
            if scene_index == len(target_lengths) - 1
            else _time_at_character(scene_end_character)
        )
        speech_start = (
            boundaries[0][2]
            if scene_index == 0
            else _speech_start_at_character(scene_start_character)
        )
        speech_end = (
            boundaries[-1][3]
            if scene_index == len(target_lengths) - 1
            else _speech_end_at_character(scene_end_character)
        )
        scene_end = min(total_audio_duration, scene_end)
        speech_start = min(total_audio_duration, speech_start)
        speech_end = min(total_audio_duration, speech_end)
        if scene_end <= scene_start or speech_end <= speech_start:
            return None
        if speech_start < scene_start - 0.05 or speech_end > scene_end + 0.05:
            return None
        timings.append(
            ContinuousSceneAudioTiming(
                start_seconds=round(scene_start, 6),
                end_seconds=round(scene_end, 6),
                speech_start_seconds=round(speech_start, 6),
                speech_end_seconds=round(speech_end, 6),
            )
        )

    if scene_character_cursor != total_target_characters:
        return None
    if abs(timings[-1].end_seconds - total_audio_duration) > 0.05:
        return None
    return timings


def _summarize_scene_boundary_pauses(
    audio_timings: list[ContinuousSceneAudioTiming] | None,
) -> dict[str, object]:
    """Report provider pauses at visual boundaries without editing audio.

    This is an observability metric, not a claim that a voice is professionally
    acted. A continuous waveform can still sound fragmented when its scene
    boundaries contain repeated long pauses, so the report exposes those gaps
    separately from waveform stitching or FFmpeg processing.
    """

    if not audio_timings or len(audio_timings) < 2:
        return {
            "boundary_count": 0,
            "pause_seconds": [],
            "max_pause_seconds": 0.0,
            "mean_pause_seconds": 0.0,
            "long_pause_threshold_seconds": 0.6,
            "long_pause_count": 0,
        }

    pauses = [
        round(
            max(0.0, next_timing.speech_start_seconds - timing.speech_end_seconds),
            6,
        )
        for timing, next_timing in zip(audio_timings[:-1], audio_timings[1:], strict=True)
    ]
    return {
        "boundary_count": len(pauses),
        "pause_seconds": pauses,
        "max_pause_seconds": max(pauses),
        "mean_pause_seconds": round(sum(pauses) / len(pauses), 6),
        "long_pause_threshold_seconds": 0.6,
        "long_pause_count": sum(pause > 0.6 for pause in pauses),
    }


def _summarize_scene_effective_speech_rates(
    scenes: list[NarrationScene],
    audio_timings: list[ContinuousSceneAudioTiming] | None,
) -> dict[str, object]:
    """Report effective characters-per-second for each continuous scene range.

    This is an observability metric rather than an acoustic voice-quality
    score. It uses the provider's mapped speech span, so reviewers can see
    whether one scene is materially more rushed than the others without
    modifying the waveform.
    """

    if not audio_timings or len(audio_timings) != len(scenes):
        return {
            "measurement": "unavailable",
            "scene_effective_characters_per_second": [],
            "min_characters_per_second": None,
            "max_characters_per_second": None,
            "mean_characters_per_second": None,
            "spread_characters_per_second": None,
        }

    rates: list[float] = []
    for (_title, text, _prompt), timing in zip(scenes, audio_timings, strict=True):
        speech_duration = timing.speech_end_seconds - timing.speech_start_seconds
        if speech_duration <= 0:
            return {
                "measurement": "invalid",
                "scene_effective_characters_per_second": [],
                "min_characters_per_second": None,
                "max_characters_per_second": None,
                "mean_characters_per_second": None,
                "spread_characters_per_second": None,
            }
        rates.append(len(_timing_text(text)) / speech_duration)

    minimum = min(rates)
    maximum = max(rates)
    return {
        "measurement": "normalized_scene_characters_divided_by_provider_speech_span",
        "scene_effective_characters_per_second": [round(rate, 4) for rate in rates],
        "min_characters_per_second": round(minimum, 4),
        "max_characters_per_second": round(maximum, 4),
        "mean_characters_per_second": round(sum(rates) / len(rates), 4),
        "spread_characters_per_second": round(maximum - minimum, 4),
    }


def _build_continuous_narration_timeline(
    scenes: list[NarrationScene],
    clip_durations: list[float],
    total_audio_duration: float,
    audio_timings: list[ContinuousSceneAudioTiming] | None = None,
) -> list[NarrationTimelineSegment]:
    """Place one continuous narration using measured or fallback boundaries."""

    if not scenes or len(scenes) != len(clip_durations):
        raise ValueError("scenes and clip_durations must have the same non-zero length")
    if total_audio_duration <= 0:
        raise ValueError("total_audio_duration must be positive")
    video_duration = sum(clip_durations)
    if video_duration + 0.05 < total_audio_duration:
        raise ValueError(
            f"continuous narration ends at {total_audio_duration:.3f}s, "
            f"after the {video_duration:.3f}s video timeline"
        )

    if audio_timings is not None and len(audio_timings) != len(scenes):
        raise ValueError("audio_timings must have one item per scene")
    if audio_timings is None:
        scene_durations = _estimate_continuous_scene_durations(scenes, total_audio_duration)
        audio_timings = [
            ContinuousSceneAudioTiming(
                start_seconds=round(sum(scene_durations[:index]), 6),
                end_seconds=round(sum(scene_durations[: index + 1]), 6),
                speech_start_seconds=round(sum(scene_durations[:index]), 6),
                speech_end_seconds=round(sum(scene_durations[: index + 1]), 6),
            )
            for index in range(len(scene_durations))
        ]
    timeline: list[NarrationTimelineSegment] = []
    shot_cursor = 0.0
    for scene_index, ((_, text, _), clip_duration, timing) in enumerate(
        zip(scenes, clip_durations, audio_timings, strict=True),
        start=1,
    ):
        if clip_duration <= 0:
            raise ValueError("clip durations must be positive")
        shot_start = shot_cursor
        shot_end = shot_start + clip_duration
        start = timing.start_seconds
        end = timing.end_seconds
        timeline.append(
            NarrationTimelineSegment(
                scene_index=scene_index,
                text=text,
                start_seconds=round(start, 6),
                end_seconds=round(end, 6),
                audio_duration_seconds=round(end - start, 6),
                shot_start_seconds=round(shot_start, 6),
                shot_end_seconds=round(shot_end, 6),
                crosses_shot_boundary=timing.speech_end_seconds > shot_end + 0.01,
                starts_before_shot_boundary=timing.speech_start_seconds < shot_start - 0.01,
                speech_start_seconds=timing.speech_start_seconds,
                speech_end_seconds=timing.speech_end_seconds,
            )
        )
        shot_cursor = shot_end
    return timeline


def _video_clip_artifact(task: GenerationTaskRecord) -> ArtifactSummary:
    artifact = next((item for item in task.artifacts if item.type == "video_clip"), None)
    if artifact is None:
        raise RuntimeError(f"video clip task {task.id} has no video_clip Artifact")
    return artifact


async def _extend_video_artifact_to_duration(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    task: GenerationTaskRecord,
    artifact: ArtifactSummary,
    episode_id: UUID,
    shot_index: int,
    target_duration_seconds: float,
    extender: FFmpegVideoTailExtender,
    validator: FFprobeVideoValidator,
) -> tuple[UUID, float, dict[str, object]]:
    """Create a derived video-clip task with a held final frame if needed."""

    source_duration = artifact.metadata.get("duration_seconds")
    if not isinstance(source_duration, (int, float)) or source_duration <= 0:
        ffprobe = artifact.metadata.get("ffprobe")
        source_duration = ffprobe.get("duration_seconds") if isinstance(ffprobe, dict) else None
    storage_key = artifact.metadata.get("storage_key")
    content_type = artifact.metadata.get("content_type")
    if not isinstance(source_duration, (int, float)) or source_duration <= 0:
        raise RuntimeError(f"video shot {shot_index} has no valid source duration metadata")
    if not isinstance(storage_key, str) or not storage_key:
        raise RuntimeError(f"video shot {shot_index} has no storage key")
    if not isinstance(content_type, str) or not content_type:
        raise RuntimeError(f"video shot {shot_index} has no content type")

    source_content = await storage.get_bytes(storage_key)
    fitted = await extender.extend(
        source_content,
        content_type,
        float(source_duration),
        target_duration_seconds,
    )
    output_probe = await validator.validate_bytes(fitted.content, fitted.content_type)
    if output_probe.duration_seconds + extender.tolerance_seconds < target_duration_seconds:
        raise RuntimeError(
            f"video shot {shot_index} ended at {output_probe.duration_seconds:.3f}s, "
            f"short of the requested {target_duration_seconds:.3f}s budget"
        )
    fit_metadata = {
        **fitted.metadata,
        "shot_index": shot_index,
        "output_duration_seconds": round(output_probe.duration_seconds, 6),
        "derived_artifact": fitted.changed,
    }
    if not fitted.changed:
        return task.id, output_probe.duration_seconds, fit_metadata
    stored = await storage.put_bytes(
        f"video/{episode_id}/{task.id}/duration-fit-shot-{shot_index}.mp4",
        fitted.content,
        fitted.content_type,
    )
    derived_metadata = {
        **artifact.metadata,
        **fitted.metadata,
        "duration_seconds": output_probe.duration_seconds,
        "duration_ms": round(output_probe.duration_seconds * 1000),
        "ffprobe": output_probe.as_metadata(),
        "source_artifact_id": str(artifact.id),
        "source_task_id": str(task.id),
        "derived": True,
        "storage_key": stored.storage_key,
        "content_type": stored.content_type,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
        "output_uri": stored.uri,
    }
    derived_artifact = ArtifactSummary(
        type="video_clip",
        provider="ffmpeg_tail_hold",
        metadata=derived_metadata,
        preview={
            "episode_id": str(episode_id),
            "output_uri": stored.uri,
            "shot_index": shot_index,
            "derived_from_task_id": str(task.id),
        },
    )
    derived_task = GenerationTaskRecord(
        project_id=task.project_id,
        kind=GenerationTaskKind.VIDEO_CLIP,
        input_data={
            **task.input_data,
            "derived_from_task_id": str(task.id),
            "duration_fit": "tail_hold",
        },
        status=TaskStatus.SUCCEEDED,
        current_stage=None,
        progress=100,
        stages=[
            StageRun(
                stage=StageName.VIDEO_CLIP,
                status=TaskStatus.SUCCEEDED,
                progress=100,
                finished_at=utc_now(),
            )
        ],
        artifacts=[derived_artifact],
    )
    saved_task, _ = await store.create_task(derived_task)
    return saved_task.id, output_probe.duration_seconds, {
        **fit_metadata,
        "derived_task_id": str(saved_task.id),
        "derived_artifact_id": str(derived_artifact.id),
    }


async def _trim_video_artifact_to_duration(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    task: GenerationTaskRecord,
    artifact: ArtifactSummary,
    episode_id: UUID,
    shot_index: int,
    target_duration_seconds: float,
    trimmer: FFmpegVideoTrimmer,
    validator: FFprobeVideoValidator,
) -> tuple[UUID, float, dict[str, object]]:
    """Create a derived video clip when continuous audio ends sooner."""

    source_duration = artifact.metadata.get("duration_seconds")
    if not isinstance(source_duration, (int, float)) or source_duration <= 0:
        ffprobe = artifact.metadata.get("ffprobe")
        source_duration = ffprobe.get("duration_seconds") if isinstance(ffprobe, dict) else None
    storage_key = artifact.metadata.get("storage_key")
    content_type = artifact.metadata.get("content_type")
    if not isinstance(source_duration, (int, float)) or source_duration <= 0:
        raise RuntimeError(f"video shot {shot_index} has no valid source duration metadata")
    if not isinstance(storage_key, str) or not storage_key:
        raise RuntimeError(f"video shot {shot_index} has no storage key")
    if not isinstance(content_type, str) or not content_type:
        raise RuntimeError(f"video shot {shot_index} has no content type")

    source_content = await storage.get_bytes(storage_key)
    fitted = await trimmer.trim(
        source_content,
        content_type,
        float(source_duration),
        target_duration_seconds,
    )
    output_probe = await validator.validate_bytes(fitted.content, fitted.content_type)
    if output_probe.duration_seconds + trimmer.tolerance_seconds < target_duration_seconds:
        raise RuntimeError(
            f"video shot {shot_index} ended at {output_probe.duration_seconds:.3f}s, "
            f"short of the requested {target_duration_seconds:.3f}s narration budget"
        )
    fit_metadata = {
        **fitted.metadata,
        "shot_index": shot_index,
        "output_duration_seconds": round(output_probe.duration_seconds, 6),
        "derived_artifact": fitted.changed,
    }
    if not fitted.changed:
        return task.id, output_probe.duration_seconds, fit_metadata
    stored = await storage.put_bytes(
        f"video/{episode_id}/{task.id}/duration-trim-shot-{shot_index}.mp4",
        fitted.content,
        fitted.content_type,
    )
    derived_metadata = {
        **artifact.metadata,
        **fitted.metadata,
        "duration_seconds": output_probe.duration_seconds,
        "duration_ms": round(output_probe.duration_seconds * 1000),
        "ffprobe": output_probe.as_metadata(),
        "source_artifact_id": str(artifact.id),
        "source_task_id": str(task.id),
        "derived": True,
        "storage_key": stored.storage_key,
        "content_type": stored.content_type,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
        "output_uri": stored.uri,
    }
    derived_artifact = ArtifactSummary(
        type="video_clip",
        provider="ffmpeg_trim",
        metadata=derived_metadata,
        preview={
            "episode_id": str(episode_id),
            "output_uri": stored.uri,
            "shot_index": shot_index,
            "derived_from_task_id": str(task.id),
        },
    )
    derived_task = GenerationTaskRecord(
        project_id=task.project_id,
        kind=GenerationTaskKind.VIDEO_CLIP,
        input_data={
            **task.input_data,
            "derived_from_task_id": str(task.id),
            "duration_fit": "trim",
        },
        status=TaskStatus.SUCCEEDED,
        current_stage=None,
        progress=100,
        stages=[
            StageRun(
                stage=StageName.VIDEO_CLIP,
                status=TaskStatus.SUCCEEDED,
                progress=100,
                finished_at=utc_now(),
            )
        ],
        artifacts=[derived_artifact],
    )
    saved_task, _ = await store.create_task(derived_task)
    return saved_task.id, output_probe.duration_seconds, {
        **fit_metadata,
        "derived_task_id": str(saved_task.id),
        "derived_artifact_id": str(derived_artifact.id),
    }


def _build_narration_timeline(
    scenes: list[NarrationScene],
    clip_durations: list[float],
    audio_durations: list[float],
    *,
    transition_overlap_seconds: float = 0.0,
) -> list[NarrationTimelineSegment]:
    """Place measured narration segments without overlap or guessed timing.

    A scene normally starts at its corresponding clip boundary. A caller may
    allow a small bounded transition overlap so frame-quantized video clips do
    not create repeated silence gaps between adjacent narration tracks. Audio
    tracks never overlap each other: if the previous voice is still running,
    the next scene starts after it. Visual-boundary drift is explicit in the
    returned flags and report.
    """

    if not scenes or len(scenes) != len(clip_durations) or len(scenes) != len(audio_durations):
        raise ValueError("scenes, clip_durations and audio_durations must have the same non-zero length")
    if transition_overlap_seconds < 0:
        raise ValueError("transition_overlap_seconds must not be negative")

    video_duration = sum(clip_durations)
    if video_duration <= 0:
        raise ValueError("clip_durations must sum to a positive video duration")

    segments: list[NarrationTimelineSegment] = []
    shot_cursor = 0.0
    audio_cursor = 0.0
    for scene_index, ((_, text, _), clip_duration, audio_duration) in enumerate(
        zip(scenes, clip_durations, audio_durations, strict=True),
        start=1,
    ):
        if clip_duration <= 0 or audio_duration <= 0:
            raise ValueError("clip and narration durations must be positive")

        shot_start = shot_cursor
        shot_end = shot_start + clip_duration
        if scene_index == 1:
            start_seconds = 0.0
        else:
            start_seconds = max(
                shot_start - transition_overlap_seconds,
                audio_cursor,
            )
        end_seconds = start_seconds + audio_duration
        if end_seconds > video_duration + 0.05:
            raise ValueError(
                f"scene {scene_index} narration ends at {end_seconds:.3f}s, "
                f"after the {video_duration:.3f}s video timeline"
            )
        if end_seconds > video_duration:
            end_seconds = video_duration

        segments.append(
            NarrationTimelineSegment(
                scene_index=scene_index,
                text=text,
                start_seconds=round(start_seconds, 6),
                end_seconds=round(end_seconds, 6),
                audio_duration_seconds=round(audio_duration, 6),
                shot_start_seconds=round(shot_start, 6),
                shot_end_seconds=round(shot_end, 6),
                crosses_shot_boundary=end_seconds > shot_end + 0.01,
                starts_before_shot_boundary=start_seconds < shot_start - 0.01,
                speech_start_seconds=round(start_seconds, 6),
                speech_end_seconds=round(end_seconds, 6),
            )
        )
        shot_cursor = shot_end
        audio_cursor = start_seconds + audio_duration

    return segments


def _build_narration_subtitle_cues(
    timeline: list[NarrationTimelineSegment],
    narration_artifacts: list[ArtifactSummary],
) -> list[tuple[float, float, str]]:
    """Build scene cues from measured audio without fabricating phrase timing.

    Continuous ChatTTS artifacts intentionally do not expose independently
    synthesized phrase timings. A legacy artifact with exactly one measured
    segment is still accepted; multiple legacy phrase segments fall back to a
    single scene cue rather than presenting stitched timing as exact.
    """

    if len(timeline) != len(narration_artifacts):
        raise ValueError("timeline and narration_artifacts must have the same length")
    cues: list[tuple[float, float, str]] = []
    for scene, artifact in zip(timeline, narration_artifacts, strict=True):
        raw_segments = artifact.metadata.get("segments")
        if not isinstance(raw_segments, list) or len(raw_segments) != 1:
            cues.append((scene.speech_start_seconds, scene.speech_end_seconds, scene.text))
            continue
        parsed: list[tuple[float, float, str]] = []
        for item in raw_segments:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                parsed = []
                break
            try:
                start = float(item["start_seconds"])
                end = float(item["end_seconds"])
            except (TypeError, ValueError):
                parsed = []
                break
            text = str(item["text"]).strip()
            if start < 0 or end <= start or not text:
                parsed = []
                break
            parsed.append((start, end, text))
        if not parsed:
            cues.append((scene.speech_start_seconds, scene.speech_end_seconds, scene.text))
            continue
        for relative_start, relative_end, text in parsed:
            start = scene.start_seconds + relative_start
            end = min(scene.end_seconds, scene.start_seconds + relative_end)
            if end > start + 0.01:
                cues.append((round(start, 6), round(end, 6), text))
    if not cues:
        raise ValueError("narration must produce at least one subtitle cue")
    previous_end = -1.0
    for start, end, _text in cues:
        if start < previous_end - 0.01:
            raise ValueError("narration subtitle cues must be ordered and must not overlap")
        previous_end = end
    return cues


def _rescale_narration_segments(
    segments: object,
    tempo_factor: float,
) -> list[dict[str, object]] | None:
    """Keep ChatTTS cue boundaries correct after a bounded atempo operation."""

    if not isinstance(segments, list) or tempo_factor <= 0:
        return None
    scaled: list[dict[str, object]] = []
    for item in segments:
        if not isinstance(item, dict):
            return None
        try:
            start = float(item["start_seconds"]) / tempo_factor
            end = float(item["end_seconds"]) / tempo_factor
        except (KeyError, TypeError, ValueError):
            return None
        if start < 0 or end <= start:
            return None
        updated = dict(item)
        updated["start_seconds"] = round(start, 6)
        updated["end_seconds"] = round(end, 6)
        if isinstance(item.get("speech_duration_seconds"), (int, float)):
            updated["speech_duration_seconds"] = round(
                float(item["speech_duration_seconds"]) / tempo_factor,
                6,
            )
        if isinstance(item.get("pause_after_seconds"), (int, float)):
            updated["pause_after_seconds"] = round(
                float(item["pause_after_seconds"]) / tempo_factor,
                6,
            )
        scaled.append(updated)
    return scaled


async def _fit_narration_artifact_to_shot(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    task: GenerationTaskRecord,
    artifact: ArtifactSummary,
    episode_id: UUID,
    scene_index: int,
    target_duration_seconds: float,
    fitter: FFmpegAudioDurationFitter,
    validator: FFprobeAudioValidator,
) -> tuple[ArtifactSummary, dict[str, object]]:
    """Derive a bounded-time narration Artifact when a scene needs it."""

    source_duration = artifact.metadata.get("duration_seconds")
    if not isinstance(source_duration, (int, float)) or source_duration <= 0:
        raise RuntimeError(
            f"narration scene {scene_index} has no valid source duration metadata"
        )
    storage_key = artifact.metadata.get("storage_key")
    content_type = artifact.metadata.get("content_type")
    if not isinstance(storage_key, str) or not storage_key:
        raise RuntimeError(f"narration scene {scene_index} has no storage key")
    if not isinstance(content_type, str) or not content_type:
        raise RuntimeError(f"narration scene {scene_index} has no content type")

    source_content = await storage.get_bytes(storage_key)
    fitted = await fitter.fit(
        source_content,
        content_type,
        float(source_duration),
        target_duration_seconds,
    )
    output_probe = await validator.validate_bytes(fitted.content, fitted.content_type)
    if output_probe.duration_seconds > target_duration_seconds + fitter.tolerance_seconds:
        raise RuntimeError(
            f"narration scene {scene_index} remains {output_probe.duration_seconds:.3f}s "
            f"after fitting to the {target_duration_seconds:.3f}s shot"
        )

    fit_metadata: dict[str, object] = {
        **fitted.metadata,
        "scene_index": scene_index,
        "output_duration_seconds": round(output_probe.duration_seconds, 6),
        "derived_artifact": fitted.changed,
    }
    if not fitted.changed:
        return artifact, fit_metadata

    stored = await storage.put_bytes(
        f"audio/{episode_id}/{task.id}/duration-fit-scene-{scene_index}.wav",
        fitted.content,
        fitted.content_type,
    )
    derived_metadata = {
        **artifact.metadata,
        **fitted.metadata,
        "duration_seconds": output_probe.duration_seconds,
        "duration_ms": round(output_probe.duration_seconds * 1000),
        "audio_duration_ms": round(output_probe.duration_seconds * 1000),
        "ffprobe": output_probe.as_metadata(),
        "source_artifact_id": str(artifact.id),
        "source_provider": artifact.provider,
        "derived": True,
        "storage_key": stored.storage_key,
        "content_type": stored.content_type,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
        "output_uri": stored.uri,
    }
    scaled_segments = _rescale_narration_segments(
        artifact.metadata.get("segments"),
        fitted.tempo_factor,
    )
    if scaled_segments is not None:
        derived_metadata["segments"] = scaled_segments
    derived_artifact = ArtifactSummary(
        type="audio_narration",
        provider="ffmpeg_atempo",
        metadata=derived_metadata,
        preview={
            "episode_id": str(episode_id),
            "output_uri": stored.uri,
            "scene_index": scene_index,
            "derived_from_artifact_id": str(artifact.id),
        },
    )
    task_snapshot = await store.get_task(task.id)
    if task_snapshot is None:
        raise RuntimeError(f"narration task {task.id} disappeared before duration fit registration")
    task_snapshot.artifacts.append(derived_artifact)
    await store.update_task(task_snapshot)
    return derived_artifact, fit_metadata


async def _pace_narration_artifact(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    task: GenerationTaskRecord,
    artifact: ArtifactSummary,
    episode_id: UUID,
    scenes: list[NarrationScene],
    pacer: FFmpegAudioPacer,
    validator: FFprobeAudioValidator,
) -> tuple[ArtifactSummary, list[ContinuousSceneAudioTiming] | None, dict[str, object]]:
    """Smooth fast phrase averages while preserving one continuous waveform."""

    source_duration = artifact.metadata.get("duration_seconds")
    storage_key = artifact.metadata.get("storage_key")
    content_type = artifact.metadata.get("content_type")
    raw_boundaries = artifact.metadata.get("word_boundaries")
    if (
        not isinstance(source_duration, (int, float))
        or source_duration <= 0
        or not isinstance(storage_key, str)
        or not storage_key
        or not isinstance(content_type, str)
        or not content_type
    ):
        return artifact, None, {"pacing": "skipped", "reason": "source_metadata_missing"}

    plan = build_narration_pacing_plan(
        scenes,
        raw_boundaries,
        float(source_duration),
        target_characters_per_second=pacer.target_characters_per_second,
        min_tempo_factor=pacer.min_tempo_factor,
        max_tempo_factor=pacer.max_tempo_factor,
    )
    if plan is None:
        return artifact, None, {"pacing": "skipped", "reason": "word_boundaries_unavailable"}

    source_content = await storage.get_bytes(storage_key)
    paced = await pacer.pace(
        source_content,
        content_type,
        float(source_duration),
        list(plan.segments),
    )
    output_probe = await validator.validate_bytes(paced.content, paced.content_type)
    paced_timings = build_paced_scene_timings(
        plan,
        output_probe.duration_seconds,
        paced.crossfade_seconds,
    )
    word_boundaries = build_paced_word_boundaries(
        plan,
        raw_boundaries,
        output_probe.duration_seconds,
        paced.crossfade_seconds,
    )
    if word_boundaries is None:
        return artifact, None, {"pacing": "skipped", "reason": "paced_word_boundary_mapping_failed"}

    timing_records = [
        ContinuousSceneAudioTiming(
            start_seconds=timing.start_seconds,
            end_seconds=timing.end_seconds,
            speech_start_seconds=timing.speech_start_seconds,
            speech_end_seconds=timing.speech_end_seconds,
        )
        for timing in paced_timings
    ]
    stored = await storage.put_bytes(
        f"audio/{episode_id}/{task.id}/paced-continuous-narration.wav",
        paced.content,
        paced.content_type,
    )
    tempo_factors = [phrase.tempo_factor for phrase in plan.phrases]
    derived_metadata = {
        **artifact.metadata,
        **paced.metadata,
        "duration_seconds": output_probe.duration_seconds,
        "duration_ms": round(output_probe.duration_seconds * 1000),
        "audio_duration_ms": round(output_probe.duration_seconds * 1000),
        "ffprobe": output_probe.as_metadata(),
        "source_artifact_id": str(artifact.id),
        "source_provider": artifact.provider,
        "derived": True,
        "synthesis_mode": "paced_continuous_waveform",
        "segmentation": "phrase_timing_slices_same_source_waveform",
        "timing_source": "edge_tts_word_boundary_paced",
        "word_boundary_count": len(word_boundaries),
        "word_boundaries": word_boundaries,
        "pacing_target_characters_per_second": plan.target_characters_per_second,
        "pacing_phrase_count": len(plan.phrases),
        "pacing_tempo_factor_min": round(min(tempo_factors), 6),
        "pacing_tempo_factor_max": round(max(tempo_factors), 6),
        "pacing_tempo_factors": [round(value, 6) for value in tempo_factors],
        "pacing_phrases": [
            {
                "scene_index": phrase.scene_index,
                "text": phrase.text,
                "character_count": phrase.character_count,
                "source_start_seconds": phrase.source_start_seconds,
                "source_end_seconds": phrase.source_end_seconds,
                "speech_start_seconds": phrase.speech_start_seconds,
                "speech_end_seconds": phrase.speech_end_seconds,
                "tempo_factor": phrase.tempo_factor,
            }
            for phrase in plan.phrases
        ],
        "storage_key": stored.storage_key,
        "content_type": stored.content_type,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
        "output_uri": stored.uri,
    }
    derived_artifact = ArtifactSummary(
        type="audio_narration",
        provider="ffmpeg_atempo_acrossfade",
        metadata=derived_metadata,
        preview={
            "episode_id": str(episode_id),
            "output_uri": stored.uri,
            "derived_from_artifact_id": str(artifact.id),
        },
    )
    task_snapshot = await store.get_task(task.id)
    if task_snapshot is None:
        raise RuntimeError(f"narration task {task.id} disappeared before pacing registration")
    task_snapshot.artifacts.append(derived_artifact)
    await store.update_task(task_snapshot)
    return (
        derived_artifact,
        timing_records,
        {
            "pacing": "applied",
            "source_duration_seconds": round(float(source_duration), 6),
            "output_duration_seconds": round(output_probe.duration_seconds, 6),
            "phrase_count": len(plan.phrases),
            "crossfade_ms": round(paced.crossfade_seconds * 1000, 3),
            "target_characters_per_second": plan.target_characters_per_second,
            "min_tempo_factor": min(tempo_factors),
            "max_tempo_factor": max(tempo_factors),
            "derived_artifact": True,
            "derived_artifact_id": str(derived_artifact.id),
        },
    )


async def _compact_narration_pauses(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    task: GenerationTaskRecord,
    artifact: ArtifactSummary,
    episode_id: UUID,
    text: str,
    compactor: FFmpegNarrationPauseCompactor,
    validator: FFprobeAudioValidator,
    scene_boundary_character_offsets: set[int] | None = None,
    scene_boundary_keep_seconds: float | None = None,
) -> tuple[ArtifactSummary, dict[str, object]]:
    """Keep one waveform while shortening selected excessive silent gaps."""

    source_duration = artifact.metadata.get("duration_seconds")
    storage_key = artifact.metadata.get("storage_key")
    content_type = artifact.metadata.get("content_type")
    raw_boundaries = artifact.metadata.get("word_boundaries")
    if (
        not isinstance(source_duration, (int, float))
        or source_duration <= 0
        or not isinstance(storage_key, str)
        or not storage_key
        or not isinstance(content_type, str)
        or not content_type
        or not isinstance(raw_boundaries, list)
        or not raw_boundaries
    ):
        return artifact, {
            "pause_compaction": "skipped",
            "reason": "word_boundary_metadata_missing",
            "compaction_scope": "scene_boundaries_only",
            "scene_boundary_count": (
                len(scene_boundary_character_offsets)
                if scene_boundary_character_offsets is not None
                else 0
            ),
        }

    source_content = await storage.get_bytes(storage_key)
    compacted = await compactor.compact(
        source_content,
        content_type,
        float(source_duration),
        text,
        raw_boundaries,
        allowed_gap_after_character_offsets=scene_boundary_character_offsets,
    )
    if not compacted.changed:
        report = dict(compacted.metadata)
        report.update(
            {
                "scene_boundary_keep_seconds": (
                    round(scene_boundary_keep_seconds, 6)
                    if scene_boundary_keep_seconds is not None
                    else round(compactor.soft_keep_seconds, 6)
                ),
                "scene_boundary_count": (
                    len(scene_boundary_character_offsets)
                    if scene_boundary_character_offsets is not None
                    else 0
                ),
            }
        )
        return artifact, report

    output_probe = await validator.validate_bytes(compacted.content, compacted.content_type)
    stored = await storage.put_bytes(
        f"audio/{episode_id}/{task.id}/pause-compacted-continuous-narration.wav",
        compacted.content,
        compacted.content_type,
    )
    derived_metadata = {
        **artifact.metadata,
        **compacted.metadata,
        "duration_seconds": output_probe.duration_seconds,
        "duration_ms": round(output_probe.duration_seconds * 1000),
        "audio_duration_ms": round(output_probe.duration_seconds * 1000),
        "ffprobe": output_probe.as_metadata(),
        "source_artifact_id": str(artifact.id),
        "source_provider": artifact.provider,
        "derived": True,
        "synthesis_mode": "single_waveform_pause_compacted",
        "segmentation": "same_source_waveform_gap_trim",
        "timing_source": "edge_tts_word_boundary_scene_pause_compacted",
        "word_boundary_count": len(compacted.word_boundaries),
        "word_boundaries": compacted.word_boundaries,
        "scene_boundary_keep_seconds": (
            round(scene_boundary_keep_seconds, 6)
            if scene_boundary_keep_seconds is not None
            else None
        ),
        "storage_key": stored.storage_key,
        "content_type": stored.content_type,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
        "output_uri": stored.uri,
    }
    derived_artifact = ArtifactSummary(
        type="audio_narration",
        provider="ffmpeg_pause_compaction",
        metadata=derived_metadata,
        preview={
            "episode_id": str(episode_id),
            "output_uri": stored.uri,
            "derived_from_artifact_id": str(artifact.id),
        },
    )
    task_snapshot = await store.get_task(task.id)
    if task_snapshot is None:
        raise RuntimeError(f"narration task {task.id} disappeared before pause compaction registration")
    task_snapshot.artifacts.append(derived_artifact)
    await store.update_task(task_snapshot)
    return derived_artifact, {
        "pause_compaction": "applied",
        "source_duration_seconds": round(float(source_duration), 6),
        "output_duration_seconds": round(output_probe.duration_seconds, 6),
        "removed_pause_seconds": compacted.removed_seconds,
        "pause_cut_count": compacted.metadata.get("pause_cut_count", 0),
        "compaction_scope": compacted.metadata.get(
            "compaction_scope",
            "selected_character_boundaries_only",
        ),
        "scene_boundary_count": compacted.metadata.get(
            "allowed_gap_after_character_count",
            0,
        ),
        "voiced_audio_time_stretched": False,
        "crossfade_ms": 0,
        "derived_artifact": True,
        "derived_artifact_id": str(derived_artifact.id),
    }


def _asset_definitions(project_id: UUID, story_bible_id: UUID) -> list[AssetRecord]:
    common = {
        "project_id": project_id,
        "story_bible_id": story_bible_id,
        "status": AssetStatus.READY,
        "provider": "portfolio-fixture",
        "model": "human-reviewed-v1",
        "duration_ms": 0,
        "source_chapter_numbers": [1],
    }
    return [
        AssetRecord(
            **common,
            asset_type=AssetType.CHARACTER,
            name="林默",
            aliases=["钟表匠", "男主"],
            content=CharacterAssetContent(
                age_range="28-35岁",
                role="旧城区钟表匠",
                traits=["克制", "敏感", "好奇"],
                appearance="黑色短发，清瘦，深色长外套，旧皮手套，气质安静",
                voice_notes="成年男性，低沉、克制，遇到异常时逐渐紧张",
            ),
        ),
        AssetRecord(
            **common,
            asset_type=AssetType.CHARACTER,
            name="黑伞女孩",
            aliases=["神秘女孩", "女孩"],
            content=CharacterAssetContent(
                age_range="20-28岁",
                role="带来怀表的神秘访客",
                traits=["冷静", "神秘", "果断"],
                appearance="黑色长发，浅色面孔，黑色长风衣，手持黑伞，眼神坚定",
                voice_notes="年轻女性，轻声、清晰，像知道未来",
            ),
        ),
        AssetRecord(
            **common,
            asset_type=AssetType.LOCATION,
            name="旧城区钟表店",
            aliases=["钟表店", "店内"],
            content=LocationAssetContent(
                description="狭长的老钟表店，木质柜台和墙面挂满机械钟，窗外常有雨夜街景",
                time_period="当代都市旧城区",
                atmosphere="温暖灯光包围着潮湿、神秘和被时间遗忘的感觉",
                visual_keywords=["机械钟", "木质柜台", "雨夜", "暖黄灯"],
            ),
        ),
        AssetRecord(
            **common,
            asset_type=AssetType.PROP,
            name="铜色怀表",
            aliases=["怀表", "古老怀表"],
            content=PropAssetContent(
                purpose="打开时间异常和地下暗门的关键道具",
                description="一枚有细密划痕的铜色机械怀表，表盘停在十二点，边缘刻着看不懂的环形符号",
                visual_keywords=["铜色", "机械齿轮", "十二点", "神秘刻痕"],
                continuity_notes="所有镜头保持铜色、停在十二点、表面有细微划痕",
            ),
        ),
    ]


def _asset_ref(asset: AssetRecord) -> ShotAssetReference:
    return ShotAssetReference(
        asset_key=asset.asset_key,
        asset_type=asset.asset_type,
        name=asset.name,
        version=asset.version,
        status=AssetStatus.READY,
        match_kind="name",
        matched_text=asset.name,
    )


def _reference_prompt(asset_name: str) -> str:
    single_frame = (
        "one full-frame image, one coherent composition, one camera view, "
        "no split screen, no split frame, "
        "no diptych, no triptych, no collage, no comic panels, no character sheet, "
        "no inset image, no repeated face, no duplicate subject, no second view, "
        "no text, no watermark"
    )
    prompts = {
        "林默": (
            "single subject head-and-shoulders portrait, exactly one young Chinese male clockmaker, late 20s, "
            "short black hair, slim face, dark long coat and old leather gloves, "
            "quiet serious expression, cinematic rainy-night lighting, neutral clock-shop background, "
            "centered face, clean portrait crop, vertical composition, " + single_frame
        ),
        "黑伞女孩": (
            "single subject head-and-shoulders portrait, exactly one young Chinese woman with long black hair, "
            "black long coat, umbrella canopy only at the top edge of the frame, calm mysterious expression, "
            "cinematic rainy-night lighting, softly blurred neutral background, centered face, "
            "clean portrait photograph, " + single_frame
        ),
        "旧城区钟表店": (
            "single empty continuous interior scene, one coherent old Chinese urban clock shop at night, "
            "wooden counter, many mechanical clocks on the wall, warm amber lamps, "
            "rainy street visible through one window, cinematic vertical composition, "
            "no people, no duplicate windows, no duplicate room, " + single_frame
        ),
        "铜色怀表": (
            "single object product shot, exactly one antique bronze mechanical pocket watch, "
            "front-facing circular watch, hands stopped at twelve o'clock, "
            "on a dark wooden clockmaker counter with a black seamless background, "
            "dramatic cinematic light, centered object, product photograph, " + single_frame
        ),
    }
    return f"{DEFAULT_REFERENCE_STYLE}. {prompts[asset_name]}"


async def _wait_for_task(
    store: InMemoryStore,
    queue: InProcessTaskQueue,
    task_id: UUID,
    label: str,
) -> object:
    await queue.close()
    task = await store.get_task(task_id)
    if task is None:
        raise RuntimeError(f"{label} task {task_id} disappeared")
    if task.status != TaskStatus.SUCCEEDED:
        error = task.error.model_dump(mode="json") if task.error else None
        raise RuntimeError(f"{label} failed: {json.dumps(error, ensure_ascii=False)}")
    return task


def _recent_reference_files(artifact_root: Path, count: int = 4) -> list[Path]:
    files = sorted(
        artifact_root.glob("reference-images/*/*/*.png"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if len(files) < count:
        raise RuntimeError(
            f"--reuse-recent-references needs {count} PNG files under {artifact_root}/reference-images"
        )
    return files[:count]


async def run_sample(args: argparse.Namespace) -> dict[str, object]:
    settings = load_settings(args.config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = _load_or_create_checkpoint(output_dir, args)
    artifact_root = Path(settings.storage_base_path)
    store = InMemoryStore()
    storage = LocalFileArtifactStorage(artifact_root)

    image_provider = create_image_generation_provider(settings)
    identity_provider = None
    identity_service = None
    if not args.mock_media:
        identity_provider = create_identity_image_generation_provider(settings)
    video_provider = create_video_generation_provider(settings)
    tts_provider = create_tts_provider(settings)

    if args.mock_media:
        from app.providers.local_fixture_video import LocalFixtureVideoGenerationProvider
        from app.providers.mock_image import MockImageGenerationProvider
        from app.providers.mock_tts import MockTTSProvider

        await _close_provider(image_provider)
        await _close_provider(video_provider)
        await _close_provider(tts_provider)
        image_provider = MockImageGenerationProvider()
        video_provider = LocalFixtureVideoGenerationProvider()
        tts_provider = MockTTSProvider()

    queue = InProcessTaskQueue()
    image_service = ReferenceImageTaskService(
        store,
        queue,
        image_provider,
        storage,
        default_width=settings.image_width,
        default_height=settings.image_height,
        identity_provider=identity_provider,
    )
    if identity_provider is not None:
        identity_service = ReferenceImageTaskService(
            store,
            queue,
            identity_provider,
            storage,
            default_width=settings.image_width,
            default_height=settings.image_height,
        )
    video_service = VideoClipTaskService(
        store,
        queue,
        video_provider,
        storage,
        video_validator=FFprobeVideoValidator(timeout_seconds=settings.video_probe_timeout_seconds),
        identity_auditor=(
            IdentityConsistencyAuditor(
                python_path=settings.identity_audit_python_path,
                script_path=settings.identity_audit_script_path,
                model_root=settings.identity_audit_model_root,
                threshold=settings.identity_audit_threshold,
                frame_count=settings.identity_audit_frame_count,
                timeout_seconds=settings.identity_audit_timeout_seconds,
            )
            if settings.identity_audit_enabled and not args.mock_media
            else None
        ),
    )
    tts_service = TTSTaskService(
        store,
        queue,
        tts_provider,
        storage,
        audio_validator=FFprobeAudioValidator(timeout_seconds=settings.tts_probe_timeout_seconds),
        default_voice=settings.tts_voice,
        default_rate=settings.tts_rate,
        default_volume=settings.tts_volume,
        max_text_characters=settings.tts_max_text_characters,
        pronunciation_dictionary=load_pronunciation_dictionary(
            settings.tts_pronunciation_dictionary_path
        ),
    )
    subtitle_service = SubtitleTaskService(
        store,
        queue,
        storage,
    )
    assembly_service = VideoAssemblyTaskService(
        store,
        queue,
        storage,
        renderer=FFmpegVideoRenderer(
            binary=settings.video_binary,
            timeout_seconds=settings.task_timeout_seconds,
        ),
        audio_validator=FFprobeAudioValidator(timeout_seconds=settings.tts_probe_timeout_seconds),
    )

    runners: dict[GenerationTaskKind, Callable[[UUID], Awaitable[None]]] = {
        GenerationTaskKind.ASSET_REFERENCE_IMAGE: image_service.run_task,
        GenerationTaskKind.VIDEO_CLIP: video_service.run_task,
        GenerationTaskKind.AUDIO_NARRATION: tts_service.run_task,
        GenerationTaskKind.SUBTITLE_SRT: subtitle_service.run_task,
        GenerationTaskKind.SUBTITLE_ALIGN: subtitle_service.run_task,
        GenerationTaskKind.SUBTITLE_ASR: subtitle_service.run_task,
        GenerationTaskKind.VIDEO_ASSEMBLY: assembly_service.run_task,
    }

    async def dispatch(task_id: UUID) -> None:
        task = await store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Queued task {task_id} disappeared before dispatch")
        if (
            task.kind == GenerationTaskKind.ASSET_REFERENCE_IMAGE
            and identity_service is not None
            and task.input_data.get("identity_reference_image_id")
        ):
            await identity_service.run_task(task_id)
        else:
            await runners[task.kind](task_id)

    queue.set_handler(dispatch)

    project_id = uuid4()
    story_bible_id = uuid4()
    source_id = uuid4()
    episode_id = uuid4()
    scenes = _narration_scenes()[: args.shots]
    shot_duration_seconds = args.shot_duration
    # The domain model keeps a 30-second minimum for a production episode;
    # a one-shot hardware smoke may still render a shorter physical clip.
    # EpisodeScriptContent intentionally requires a production episode of at
    # least 30 seconds. A one-shot hardware smoke is shorter physically, so we
    # keep its render duration in ShotContent and distribute the schema-level
    # episode duration across the selected script scenes.
    total_duration = max(30, len(scenes) * shot_duration_seconds)
    base_scene_duration, remainder = divmod(total_duration, len(scenes))
    script_scene_durations = [
        base_scene_duration + (1 if index < remainder else 0)
        for index in range(len(scenes))
    ]

    story_bible = StoryBibleRecord(
        id=story_bible_id,
        project_id=project_id,
        source_id=source_id,
        content=StoryBibleContent(
            title="十二点之后",
            logline="一枚停在十二点的怀表，把旧城区钟表匠林默带进了被隐藏的未来。",
            genre=["都市奇幻", "悬疑", "动态漫剧"],
            setting="当代中国城市旧城区，一家濒临倒闭的钟表店和它背后的地下暗门。",
            themes=["选择", "时间", "未知的真相"],
            characters=[
                {
                    "name": "林默",
                    "role": "旧城区钟表匠",
                    "traits": ["克制", "敏感"],
                    "appearance": "黑色短发，深色长外套，清瘦",
                    "voice_notes": "低沉克制",
                },
                {
                    "name": "黑伞女孩",
                    "role": "神秘访客",
                    "traits": ["冷静", "神秘"],
                    "appearance": "黑色长发，黑色长风衣，手持黑伞",
                    "voice_notes": "轻声清晰",
                },
            ],
            locations=[
                {
                    "name": "旧城区钟表店",
                    "description": "挂满机械钟的老店，窗外是雨夜街景",
                    "visual_keywords": ["机械钟", "雨夜", "暖黄灯"],
                }
            ],
            props=[
                {
                    "name": "铜色怀表",
                    "purpose": "时间异常的关键道具",
                    "description": "停在十二点、有神秘刻痕的铜色怀表",
                    "visual_keywords": ["铜色", "十二点", "齿轮"],
                    "continuity_notes": "保持停在十二点",
                }
            ],
            timeline=["雨夜来客", "怀表停摆", "时间凝固", "暗门开启", "看见未来"],
            conflicts=["林默是否相信女孩", "怀表是否应该重新启动", "未来是否可以改变"],
            source_chapter_numbers=[1],
        ),
        provider="portfolio-fixture",
        model="human-reviewed-v1",
        duration_ms=0,
    )
    await store.save_story_bible(story_bible)
    episode = (await store.save_episodes([
        EpisodeRecord(
            id=episode_id,
            project_id=project_id,
            story_bible_id=story_bible_id,
            episode_number=1,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="十二点之后",
                logline="停摆的怀表打开了旧钟表店背后的时间暗门。",
                objective="让林默接受时间异常并进入暗门",
                conflict="他不知道女孩是否在利用自己",
                turning_point="整条街的时间突然凝固",
                ending_hook="林默看见了明天的自己",
                source_chapter_numbers=[1],
                target_duration_seconds=total_duration,
            ),
            provider="portfolio-fixture",
            model="human-reviewed-v1",
            duration_ms=0,
        )
    ]))[0]

    assets = _asset_definitions(project_id, story_bible_id)
    saved_assets = [await store.save_asset_version(asset) for asset in assets]
    by_name = {asset.name: asset for asset in saved_assets}

    script = EpisodeScriptRecord(
        project_id=project_id,
        episode_id=episode.id,
        content=EpisodeScriptContent(
            episode_number=1,
            title="十二点之后",
            logline="停摆的怀表打开了旧钟表店背后的时间暗门。",
            opening_hook="每天夜里，墙后都会传来三下钟声。",
            ending_hook="林默在暗门后看见了明天的自己。",
            total_duration_seconds=total_duration,
            scenes=[
                {
                    "scene_index": index,
                    "title": title,
                    "location": "旧城区钟表店",
                    "time": "雨夜",
                    "characters": ["林默"] if index == 1 else ["林默", "黑伞女孩"],
                    "duration_seconds": script_scene_durations[index - 1],
                    "action": visual_prompt,
                    "narration": voiceover,
                    "dialogues": [],
                    "emotion": "悬疑、克制、逐渐紧张",
                    "source_chapter_numbers": [1],
                }
                for index, (title, voiceover, visual_prompt) in enumerate(scenes, start=1)
            ],
            risk_notes=["参考图只保证人设/道具方向一致，FFmpeg Motion 不生成新动作"],
        ),
        provider="portfolio-fixture",
        model="human-reviewed-v1",
        duration_ms=0,
    )
    script = await store.save_episode_script(script)

    shot_assets = [
        ["林默", "旧城区钟表店"],
        ["林默", "旧城区钟表店"],
        ["黑伞女孩", "旧城区钟表店", "铜色怀表"],
        ["黑伞女孩", "旧城区钟表店", "铜色怀表"],
        ["铜色怀表", "旧城区钟表店"],
        ["林默", "铜色怀表", "旧城区钟表店"],
        ["林默", "黑伞女孩", "旧城区钟表店"],
        ["黑伞女孩", "旧城区钟表店"],
        ["林默", "铜色怀表", "旧城区钟表店"],
        ["林默", "铜色怀表", "旧城区钟表店"],
    ][: args.shots]
    shots = [
        ShotContent(
            shot_index=index,
            scene_index=index,
            duration_seconds=shot_duration_seconds,
            shot_size=_PORTFOLIO_SHOT_SIZES[index - 1],
            camera_movement=_PORTFOLIO_CAMERA_MOVEMENTS[index - 1],
            characters=[name for name in required if by_name[name].asset_type == AssetType.CHARACTER],
            location="旧城区钟表店",
            visual_prompt=visual_prompt,
            dialogue_refs=[],
            audio_requirements=["旁白", "雨夜环境氛围"],
            asset_requirements=required,
            asset_refs=[_asset_ref(by_name[name]) for name in required],
            continuity_notes="沿用已审核参考图；本地方案使用 Ken Burns 动态化，不生成新的角色动作。",
        )
        for index, ((_, _, visual_prompt), required) in enumerate(zip(scenes, shot_assets, strict=True), start=1)
    ]
    shot_list = await store.save_shot_list(
        ShotListRecord(
            project_id=project_id,
            episode_id=episode.id,
            script_id=script.id,
            shots=shots,
            provider="portfolio-fixture",
            model="human-reviewed-v1",
            duration_ms=0,
        )
    )

    reference_image_ids: dict[str, UUID] = {}
    reference_tasks: list[UUID] = []
    image_assets = ["林默", "黑伞女孩", "旧城区钟表店", "铜色怀表"]
    if args.reuse_recent_references and not args.mock_media:
        for asset_name, path in zip(
            image_assets,
            _recent_reference_files(artifact_root),
            strict=True,
        ):
            reference_image_id = uuid4()
            reference_task_id = uuid4()
            storage_key = str(path.relative_to(artifact_root))
            await store.save_reference_image(
                ReferenceImageRecord(
                    id=reference_image_id,
                    task_id=reference_task_id,
                    project_id=project_id,
                    asset_id=by_name[asset_name].id,
                    asset_key=by_name[asset_name].asset_key,
                    asset_type=by_name[asset_name].asset_type,
                    asset_version=by_name[asset_name].version,
                    prompt=_reference_prompt(asset_name),
                    negative_prompt=DEFAULT_REFERENCE_NEGATIVE_PROMPT,
                    provider="comfyui",
                    model=settings.image_model,
                    status=ReferenceImageStatus.SUCCEEDED,
                    output_uri=f"local://{storage_key}",
                    width=settings.image_width,
                    height=settings.image_height,
                    duration_ms=0,
                    metadata={
                        "storage_key": storage_key,
                        "content_type": "image/png",
                        "reused_from_previous_run": True,
                        "source_path": str(path),
                    },
                )
            )
            reference_image_ids[asset_name] = reference_image_id
            reference_tasks.append(reference_task_id)
    else:
        for asset_name in image_assets:
            task, _ = await image_service.create_task(
                by_name[asset_name].id,
                ReferenceImageCreateRequest(
                    style="cinematic vertical portfolio keyframe",
                    prompt_override=_reference_prompt(asset_name),
                    width=settings.image_width,
                    height=settings.image_height,
                ),
                idempotency_key=f"portfolio-reference-{asset_name}",
            )
            reference_tasks.append(task.id)
            await _wait_for_task(store, queue, task.id, f"reference image {asset_name}")
            completed = await store.get_task(task.id)
            assert completed is not None
            reference_image_ids[asset_name] = UUID(str(completed.input_data["reference_image_id"]))

    clip_tasks: list[UUID] = []
    shot_reference_names = [
        "林默", "林默", "黑伞女孩", "黑伞女孩", "铜色怀表",
        "林默", "林默", "黑伞女孩", "林默", "林默",
    ][: args.shots]
    shot_reference_ids: dict[int, UUID] = {}
    for shot, reference_name in zip(shots, shot_reference_names, strict=True):
        checkpoint_entry = _shot_checkpoint_entry(checkpoint, shot.shot_index)
        checkpointed_content = (
            _read_checkpointed_clip(output_dir, shot.shot_index, checkpoint_entry)
            if args.resume
            else None
        )
        if checkpointed_content is not None and checkpoint_entry is not None:
            resumed_task_id = await _register_checkpointed_clip_task(
                store,
                storage,
                episode,
                shot_list,
                shot,
                checkpointed_content,
                checkpoint_entry,
            )
            clip_tasks.append(resumed_task_id)
            checkpoint_entry = {
                **checkpoint_entry,
                "status": "succeeded",
                "resumed_count": int(checkpoint_entry.get("resumed_count", 0)) + 1,
                "last_resumed_at": _utc_timestamp(),
            }
            _set_shot_checkpoint_entry(checkpoint, shot.shot_index, checkpoint_entry)
            _write_checkpoint(_checkpoint_path(output_dir), checkpoint)
            if args.stop_after_shot == shot.shot_index:
                break
            continue

        attempt = int(checkpoint_entry.get("attempt", 0)) + 1 if checkpoint_entry else 1
        stable_path = _stable_shot_path(output_dir, shot.shot_index)
        _set_shot_checkpoint_entry(
            checkpoint,
            shot.shot_index,
            {
                "status": "running",
                "attempt": attempt,
                "shot_index": shot.shot_index,
                "output_path": str(stable_path.resolve()),
                "started_at": _utc_timestamp(),
                "provider": "local_fixture" if args.mock_media else settings.video_provider,
                "model": "local-fixture" if args.mock_media else settings.video_model,
            },
        )
        _write_checkpoint(_checkpoint_path(output_dir), checkpoint)
        generation_started = time.monotonic()
        selected_reference_id = reference_image_ids[reference_name]
        try:
            if (
                identity_service is not None
                and reference_name in {"林默", "黑伞女孩"}
                and not args.reuse_recent_references
            ):
                identity_task, _ = await identity_service.create_task(
                    by_name[reference_name].id,
                    ReferenceImageCreateRequest(
                        style="Flux PuLID identity-locked cinematic shot keyframe",
                        prompt_override=shot.visual_prompt,
                        width=settings.image_width,
                        height=settings.image_height,
                        identity_reference_image_id=reference_image_ids[reference_name],
                    ),
                    idempotency_key=f"portfolio-identity-shot-{shot.shot_index}",
                )
                await _wait_for_task(store, queue, identity_task.id, f"identity keyframe {shot.shot_index}")
                identity_completed = await store.get_task(identity_task.id)
                assert identity_completed is not None
                selected_reference_id = UUID(str(identity_completed.input_data["reference_image_id"]))
            shot_reference_ids[shot.shot_index] = selected_reference_id
            task, _ = await video_service.create_task(
                episode.id,
                shot.shot_index,
                VideoClipCreateRequest(
                    prompt_override=shot.visual_prompt,
                    reference_image_id=(
                        None if args.mock_media else shot_reference_ids[shot.shot_index]
                    ),
                ),
                idempotency_key=f"portfolio-clip-{shot.shot_index}",
            )
            await _wait_for_task(store, queue, task.id, f"video clip {shot.shot_index}")
            completed_clip_task = await store.get_task(task.id)
            assert completed_clip_task is not None
            clip_artifact = next(
                artifact for artifact in completed_clip_task.artifacts if artifact.type == "video_clip"
            )
            storage_key = clip_artifact.metadata.get("storage_key")
            if not isinstance(storage_key, str) or not storage_key:
                raise RuntimeError(f"video clip {shot.shot_index} did not produce a local Artifact")
            clip_content = await storage.get_bytes(storage_key)
            stable_path.parent.mkdir(parents=True, exist_ok=True)
            stable_path.write_bytes(clip_content)
            _set_shot_checkpoint_entry(
                checkpoint,
                shot.shot_index,
                {
                    "status": "succeeded",
                    "attempt": attempt,
                    "shot_index": shot.shot_index,
                    "output_path": str(stable_path.resolve()),
                    "sha256": _sha256_bytes(clip_content),
                    "size_bytes": len(clip_content),
                    "provider": clip_artifact.provider,
                    "model": clip_artifact.metadata.get("model", settings.video_model),
                    "metadata": clip_artifact.metadata,
                    "task_id": str(task.id),
                    "generation_seconds": round(time.monotonic() - generation_started, 3),
                    "finished_at": _utc_timestamp(),
                },
            )
            _write_checkpoint(_checkpoint_path(output_dir), checkpoint)
            clip_tasks.append(task.id)
        except Exception as exc:
            failed_entry = _shot_checkpoint_entry(checkpoint, shot.shot_index) or {}
            _set_shot_checkpoint_entry(
                checkpoint,
                shot.shot_index,
                {
                    **failed_entry,
                    "status": "failed",
                    "error": str(exc),
                    "finished_at": _utc_timestamp(),
                    "generation_seconds": round(time.monotonic() - generation_started, 3),
                },
            )
            checkpoint["status"] = "failed"
            _write_checkpoint(_checkpoint_path(output_dir), checkpoint)
            raise
        if args.stop_after_shot == shot.shot_index:
            break

    if len(clip_tasks) < len(shots):
        checkpoint["status"] = "paused"
        checkpoint["completed_shots"] = [
            int(shot_index)
            for shot_index, entry in (checkpoint.get("shots", {}) or {}).items()
            if isinstance(entry, dict) and entry.get("status") == "succeeded"
        ]
        _write_checkpoint(_checkpoint_path(output_dir), checkpoint)
        clip_task_snapshots: list[GenerationTaskRecord] = []
        for task_id in clip_tasks:
            snapshot = await store.get_task(task_id)
            if snapshot is not None:
                clip_task_snapshots.append(snapshot)
        portfolio_readiness = _portfolio_readiness_report(
            args=args,
            shot_count=len(shots),
            reference_image_count=len(reference_tasks),
            clip_count=len(clip_tasks),
            narration_artifact=None,
            subtitle_artifact=None,
            subtitle_metadata={},
            rendered_artifact=None,
            clip_task_snapshots=clip_task_snapshots,
        )
        partial_report = {
            "report_schema_version": PORTFOLIO_REPORT_SCHEMA_VERSION,
            "status": "paused",
            "project_id": str(project_id),
            "episode_id": str(episode.id),
            "script_id": str(script.id),
            "shot_list_id": str(shot_list.id),
            "shots_requested": len(shots),
            "shots_completed": len(clip_tasks),
            "reference_image_count": len(reference_tasks),
            "checkpoint_path": str(_checkpoint_path(output_dir)),
            "output_dir": str(output_dir),
            "artifact_ids": {
                "rendered_video": None,
                "source_video_clips": [str(task_id) for task_id in clip_tasks],
                "narration": [],
                "subtitles": None,
            },
            "portfolio_readiness": portfolio_readiness,
            "next_command": (
                f"AI_VIDEO_PROFILE=local_mac_16gb AI_VIDEO_CONFIG={args.config} "
                f"python scripts/run-local-portfolio-sample.py --shots {args.shots} "
                f"--shot-duration {args.shot_duration} --resume --output-dir {output_dir}"
            ),
        }
        (output_dir / "report.json").write_text(
            json.dumps(partial_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await _close_provider(image_provider)
        await _close_provider(identity_provider)
        await _close_provider(video_provider)
        await _close_provider(tts_provider)
        await storage.close()
        return partial_report

    checkpoint["status"] = "videos_completed"
    checkpoint["completed_shots"] = [shot.shot_index for shot in shots]
    _write_checkpoint(_checkpoint_path(output_dir), checkpoint)

    clip_durations = await _video_clip_durations(store, clip_tasks)
    assembly_clip_tasks = list(clip_tasks)
    narration_tasks: list[GenerationTaskRecord] = []
    narration_artifacts: list[ArtifactSummary] = []
    raw_audio_durations: list[float] = []
    narration_fit_reports: list[dict[str, object]] = []
    narration_pacing_report: dict[str, object] = {
        "pacing": "disabled",
        "reason": "portfolio_continuous_waveform_policy",
        "waveform_editing": "none",
        "crossfade_ms": 0,
    }
    video_duration_fit_reports: list[dict[str, object]] = []
    # The portfolio path deliberately performs no audio time-warp.  Keeping
    # the source waveform intact is more important than forcing every visual
    # shot to a fixed duration; a short shot is extended with a held final
    # frame below instead.
    effective_max_tempo = 1.0
    video_duration_extender = FFmpegVideoTailExtender(
        binary=settings.tts_ffmpeg_binary or "ffmpeg",
        timeout_seconds=settings.tts_timeout_seconds,
    )
    video_duration_trimmer = FFmpegVideoTrimmer(
        binary=settings.tts_ffmpeg_binary or "ffmpeg",
        timeout_seconds=settings.tts_timeout_seconds,
    )
    video_duration_validator = FFprobeVideoValidator(
        timeout_seconds=settings.video_probe_timeout_seconds,
    )
    narration_text = _build_continuous_narration_text(scenes)
    narration_task, _ = await tts_service.create_task(
        episode.id,
        AudioNarrationCreateRequest(text=narration_text),
        idempotency_key="portfolio-narration-episode-v26-balanced2-yunyang-m35pct",
    )
    await _wait_for_task(store, queue, narration_task.id, "continuous episode narration")
    completed_narration_task = await store.get_task(narration_task.id)
    assert completed_narration_task is not None
    source_narration_artifact = next(
        artifact
        for artifact in completed_narration_task.artifacts
        if artifact.type == "audio_narration"
    )
    narration_tasks.append(completed_narration_task)
    raw_duration = source_narration_artifact.metadata.get("duration_seconds")
    if not isinstance(raw_duration, (int, float)) or raw_duration <= 0:
        raise RuntimeError("continuous episode narration has no valid duration")
    source_audio_duration = float(raw_duration)
    narration_artifact = source_narration_artifact
    # Keep the provider output as one waveform. If enabled, the only derived
    # audio operation is conservative pause compaction: it removes excess
    # silence between provider word-boundary events, preserves a short breath,
    # and never time-stretches voiced samples or stitches independent TTS
    # waveforms.
    narration_pause_report: dict[str, object] = {
        "pause_compaction": "disabled",
        "reason": "portfolio_continuous_waveform_no_editing",
        "waveform_editing": "none",
        "crossfade_ms": 0,
        "configured_enabled": bool(settings.tts_pause_compaction_enabled),
        "compaction_scope": "disabled_for_portfolio_continuity",
    }
    if settings.tts_pause_compaction_enabled:
        narration_pause_compactor = FFmpegNarrationPauseCompactor(
            binary=settings.tts_ffmpeg_binary or "ffmpeg",
            timeout_seconds=settings.tts_timeout_seconds,
            min_gap_seconds=settings.tts_pause_min_gap_seconds,
            # The portfolio passes scene offsets below, so the uniform keep
            # duration is the only pause policy that can edit the default run.
            soft_keep_seconds=settings.tts_pause_scene_boundary_keep_seconds,
            strong_keep_seconds=settings.tts_pause_strong_keep_seconds,
            neutral_keep_seconds=settings.tts_pause_neutral_keep_seconds,
            trailing_keep_seconds=settings.tts_pause_trailing_keep_seconds,
            allowed_gap_keep_seconds=settings.tts_pause_scene_boundary_keep_seconds,
        )
        narration_artifact, narration_pause_report = await _compact_narration_pauses(
            store,
            storage,
            completed_narration_task,
            narration_artifact,
            episode.id,
            narration_text,
            narration_pause_compactor,
            FFprobeAudioValidator(timeout_seconds=settings.tts_probe_timeout_seconds),
            _continuous_scene_boundary_character_offsets(scenes),
            settings.tts_pause_scene_boundary_keep_seconds,
        )
        narration_pause_report["configured_enabled"] = True
    continuous_audio_duration = narration_artifact.metadata.get("duration_seconds")
    if not isinstance(continuous_audio_duration, (int, float)) or continuous_audio_duration <= 0:
        raise RuntimeError("continuous episode narration has no valid final duration")
    continuous_audio_duration = float(continuous_audio_duration)
    raw_audio_durations.append(source_audio_duration)
    narration_artifacts.append(narration_artifact)
    narration_fit_reports.append(
        {
            "duration_fit": "not_needed",
            "filter": "none",
            "source_duration_seconds": round(source_audio_duration, 6),
            "target_duration_seconds": round(continuous_audio_duration, 6),
            "tempo_factor": 1.0,
            "max_tempo_factor": effective_max_tempo,
            "continuous_track": True,
            "pause_compaction": narration_pause_report.get("pause_compaction", "disabled"),
            "synthesis_mode": narration_artifact.metadata.get(
                "synthesis_mode",
                "provider_default",
            ),
        }
    )

    audio_scene_timings = _build_continuous_audio_scene_timings(
        scenes,
        continuous_audio_duration,
        narration_artifact.metadata.get("word_boundaries"),
    )
    if audio_scene_timings is None:
        scene_timing_source = "text_weighted_fallback"
        narration_boundary_pause_report = _summarize_scene_boundary_pauses(None)
        narration_speech_rate_report = _summarize_scene_effective_speech_rates(
            scenes,
            None,
        )
    else:
        scene_timing_source = str(
            narration_artifact.metadata.get(
                "timing_source",
                "edge_tts_word_boundary_continuous",
            )
        )
        narration_boundary_pause_report = _summarize_scene_boundary_pauses(
            audio_scene_timings
        )
        narration_speech_rate_report = _summarize_scene_effective_speech_rates(
            scenes,
            audio_scene_timings,
        )
    target_scene_durations = (
        [timing.end_seconds - timing.start_seconds for timing in audio_scene_timings]
        if audio_scene_timings is not None
        else _estimate_continuous_scene_durations(scenes, continuous_audio_duration)
    )
    # Keep visual cuts evenly distributed instead of placing every cut at an
    # audio scene boundary. The narration remains one waveform, and subtitles
    # follow measured speech timing independently of the visual edit.
    visual_scene_durations = _balanced_visual_scene_durations(
        clip_durations,
        continuous_audio_duration,
        settings.video_fps,
    )
    for scene_index, (target_scene_duration, visual_scene_duration) in enumerate(
        zip(target_scene_durations, visual_scene_durations, strict=True),
        start=1,
    ):
        frame_aligned_scene_duration = round(visual_scene_duration, 6)
        original_clip_task = await store.get_task(clip_tasks[scene_index - 1])
        if original_clip_task is None:
            raise RuntimeError(f"video clip task for scene {scene_index} disappeared before duration fit")
        original_clip_artifact = _video_clip_artifact(original_clip_task)
        current_clip_duration = clip_durations[scene_index - 1]
        if frame_aligned_scene_duration > current_clip_duration + video_duration_extender.tolerance_seconds:
            derived_clip_task_id, extended_duration, extension_report = await _extend_video_artifact_to_duration(
                store,
                storage,
                original_clip_task,
                original_clip_artifact,
                episode.id,
                scene_index,
                frame_aligned_scene_duration,
                video_duration_extender,
                video_duration_validator,
            )
            assembly_clip_tasks[scene_index - 1] = derived_clip_task_id
            clip_durations[scene_index - 1] = extended_duration
            extension_report["estimated_scene_audio_duration_seconds"] = target_scene_duration
            extension_report["frame_aligned_scene_duration_seconds"] = frame_aligned_scene_duration
            extension_report["continuous_audio_track"] = True
            video_duration_fit_reports.append(extension_report)
        elif frame_aligned_scene_duration < current_clip_duration - video_duration_trimmer.tolerance_seconds:
            derived_clip_task_id, trimmed_duration, trim_report = await _trim_video_artifact_to_duration(
                store,
                storage,
                original_clip_task,
                original_clip_artifact,
                episode.id,
                scene_index,
                frame_aligned_scene_duration,
                video_duration_trimmer,
                video_duration_validator,
            )
            assembly_clip_tasks[scene_index - 1] = derived_clip_task_id
            clip_durations[scene_index - 1] = trimmed_duration
            trim_report["estimated_scene_audio_duration_seconds"] = target_scene_duration
            trim_report["frame_aligned_scene_duration_seconds"] = frame_aligned_scene_duration
            trim_report["continuous_audio_track"] = True
            video_duration_fit_reports.append(trim_report)

    video_duration = sum(clip_durations)
    narration_timeline = _build_continuous_narration_timeline(
        scenes,
        clip_durations,
        continuous_audio_duration,
        audio_timings=audio_scene_timings,
    )
    subtitle_task, _ = await subtitle_service.create_task(
        episode.id,
        SubtitleCreateRequest(
            language="zh-CN",
            cues=[
                SubtitleCueRequest(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    text=text,
                )
                for start_seconds, end_seconds, text in (
                    (
                        segment.speech_start_seconds,
                        segment.speech_end_seconds,
                        segment.text,
                    )
                    for segment in narration_timeline
                )
            ],
        ),
    idempotency_key="portfolio-subtitles-continuous-narration-v11-balanced2-boundaries",
    )
    await _wait_for_task(store, queue, subtitle_task.id, "subtitles")
    subtitle_task = await store.get_task(subtitle_task.id)
    assert subtitle_task is not None
    subtitle_artifact = next(
        artifact for artifact in subtitle_task.artifacts if artifact.type == "subtitle_srt"
    )
    subtitle_metadata = subtitle_artifact.metadata
    subtitle_duration = max(segment.speech_end_seconds for segment in narration_timeline)
    audio_duration = continuous_audio_duration

    assembly_task, _ = await assembly_service.create_task(
        episode.id,
        VideoAssemblyCreateRequest(
            clip_task_ids=assembly_clip_tasks,
            audio_tracks=[
                AudioTrackRequest(
                    artifact_id=narration_artifacts[0].id,
                    track_type="narration",
                    start_seconds=0.0,
                    volume=1.0,
                )
            ],
            subtitle_artifact_id=subtitle_artifact.id,
        ),
        idempotency_key="portfolio-assembly-v7-balanced2-continuous-narration",
    )
    await _wait_for_task(store, queue, assembly_task.id, "video assembly")
    assembly_task = await store.get_task(assembly_task.id)
    assert assembly_task is not None
    rendered_artifact = next(
        artifact for artifact in assembly_task.artifacts if artifact.type == "rendered_video"
    )
    storage_key = str(rendered_artifact.metadata["storage_key"])
    rendered_bytes = await storage.get_bytes(storage_key)
    stable_video = output_dir / "portfolio-sample.mp4"
    stable_video.write_bytes(rendered_bytes)
    narration_storage_key = narration_artifact.metadata.get("storage_key")
    narration_content_type = narration_artifact.metadata.get("content_type", "audio/wav")
    stable_narration: Path | None = None
    if isinstance(narration_storage_key, str) and narration_storage_key:
        narration_bytes = await storage.get_bytes(narration_storage_key)
        narration_suffix = (
            ".wav"
            if str(narration_content_type).split(";", 1)[0].strip().lower()
            in {"audio/wav", "audio/x-wav"}
            else ".mp3"
        )
        stable_narration = output_dir / f"narration{narration_suffix}"
        stable_narration.write_bytes(narration_bytes)
    clip_task_snapshots: list[GenerationTaskRecord] = []
    for task_id in clip_tasks:
        snapshot = await store.get_task(task_id)
        if snapshot is not None:
            clip_task_snapshots.append(snapshot)
    portfolio_readiness = _portfolio_readiness_report(
        args=args,
        shot_count=len(shots),
        reference_image_count=len(reference_tasks),
        clip_count=len(clip_tasks),
        narration_artifact=narration_artifact,
        subtitle_artifact=subtitle_artifact,
        subtitle_metadata=subtitle_metadata,
        rendered_artifact=rendered_artifact,
        clip_task_snapshots=clip_task_snapshots,
    )
    report = {
        "report_schema_version": PORTFOLIO_REPORT_SCHEMA_VERSION,
        "project_id": str(project_id),
        "episode_id": str(episode.id),
        "script_id": str(script.id),
        "shot_list_id": str(shot_list.id),
        "shots": len(clip_tasks),
        "video_duration_seconds": rendered_artifact.metadata.get("duration_ms", 0) / 1000,
        "audio_duration_seconds": round(audio_duration, 6),
        "raw_audio_duration_seconds": round(sum(raw_audio_durations), 6),
        "subtitle_duration_seconds": subtitle_duration,
        "narration_timeline_end_seconds": subtitle_duration,
        "audio_silence_after_narration_seconds": round(
            max(0.0, video_duration - audio_duration),
            6,
        ),
        "trailing_audio_silence_seconds": round(
            max(0.0, audio_duration - subtitle_duration),
            6,
        ),
        "audio_trimmed_to_video": False,
        "video_trimmed_to_audio": any(
            item.get("duration_fit") == "trim" for item in video_duration_fit_reports
        ),
        "audio_duration_fitted_to_shots": bool(narration_fit_reports)
        and any(bool(item.get("derived_artifact")) for item in narration_fit_reports),
        "narration_duration_fit": narration_fit_reports,
        "narration_pacing": narration_pacing_report,
        "narration_pause_compaction": narration_pause_report,
        "video_duration_fit": video_duration_fit_reports,
        "visual_scene_duration_policy": "balanced_even_shot_boundaries",
        "visual_scene_durations": [round(value, 6) for value in clip_durations],
        "narration_visual_cut_alignment": "independent_from_narration_boundaries",
        "max_narration_tempo_factor": effective_max_tempo,
        "max_narration_transition_overlap_seconds": 0.0,
        "narration_scene_count": len(scenes),
        "narration_artifact_count": len(narration_artifacts),
        "narration_track_count": 1,
        "narration_synthesis": narration_artifact.metadata.get(
            "synthesis_mode",
            "episode_text_continuous",
        ),
        "narration_text_boundary_strategy": (
            "continuous_text_balanced2_editorial_sentence_groups_pause_compaction_no_voice_retime"
            if narration_pause_report.get("pause_compaction") == "applied"
            else "continuous_text_balanced2_editorial_sentence_groups_no_voice_retime"
        ),
        "narration_text_profile": "balanced2",
        "narration_text_character_count": len(_timing_text(narration_text)),
        "narration_audio_timing_source": scene_timing_source,
        "narration_scene_boundary_pauses": narration_boundary_pause_report,
        "narration_scene_effective_speech_rates": narration_speech_rate_report,
        "narration_voice": narration_artifact.metadata.get("voice", settings.tts_voice),
        "narration_rate": narration_artifact.metadata.get("rate", settings.tts_rate),
        "narration_word_boundary_count": narration_artifact.metadata.get(
            "word_boundary_count",
            0,
        ),
        "narration_phrase_waveforms_stitched": False,
        "narration_context_chunks_crossfaded": "none",
        "narration_independent_waveform_count": narration_artifact.metadata.get(
            "independent_waveform_count",
            1,
        ),
        "subtitle_cue_count": subtitle_metadata.get("cue_count", 0),
        "narration_timeline": [
            {
                "scene_index": segment.scene_index,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "audio_duration_seconds": segment.audio_duration_seconds,
                "speech_start_seconds": segment.speech_start_seconds,
                "speech_end_seconds": segment.speech_end_seconds,
                "shot_start_seconds": segment.shot_start_seconds,
                "shot_end_seconds": segment.shot_end_seconds,
                "crosses_shot_boundary": segment.crosses_shot_boundary,
                "starts_before_shot_boundary": segment.starts_before_shot_boundary,
            }
            for segment in narration_timeline
        ],
        "reference_image_count": len(reference_tasks),
        "providers": {
            "image": "mock" if args.mock_media else settings.image_provider,
            "video": "local_fixture" if args.mock_media else settings.video_provider,
            "tts": "mock" if args.mock_media else settings.tts_provider,
            "subtitles": subtitle_metadata.get(
                "alignment_provider", settings.subtitle_alignment_provider
            ),
            "assembly": "ffmpeg",
        },
        "artifact_ids": {
            "rendered_video": str(rendered_artifact.id),
            "source_video_clips": [str(task_id) for task_id in clip_tasks],
            "assembly_video_clips": [str(task_id) for task_id in assembly_clip_tasks],
            "narration": [str(artifact.id) for artifact in narration_artifacts],
            "narration_tasks": [str(task.id) for task in narration_tasks],
            "subtitles": str(subtitle_artifact.id),
        },
        "artifact_storage_key": storage_key,
        "output_path": str(stable_video),
        "narration_output_path": str(stable_narration) if stable_narration else None,
        "quality_boundary": {
            "image": "reference-image-driven" if not args.mock_media else "mock-placeholder",
            "motion": "wan2.1_i2v" if not args.mock_media else "fixture-color-card",
            "subtitle": (
                "continuous_audio_edge_word_boundary_editorial_sentence_cues"
                if audio_scene_timings is not None
                else "continuous_audio_scene_estimate"
            ),
            "subtitle_artifact_precision": subtitle_metadata.get(
                "alignment_precision",
                "provided_cues",
            ),
            "narration_timing": (
                "one_continuous_audio_track_edge_word_boundary_editorial_sentence_cues_pause_compacted"
                if narration_pause_report.get("pause_compaction") == "applied"
                and audio_scene_timings is not None
                else "one_continuous_audio_track_edge_word_boundary_editorial_sentence_cues"
                if audio_scene_timings is not None
                else "one_continuous_audio_track_text_weighted_scene_cues"
            ),
            "reference_review": "needs_review" if not args.mock_media else "not_applicable",
            "human_review_required": True,
            "reference_reused": bool(args.reuse_recent_references and not args.mock_media),
        },
        "portfolio_readiness": portfolio_readiness,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    await _close_provider(image_provider)
    await _close_provider(identity_provider)
    await _close_provider(video_provider)
    await _close_provider(tts_provider)
    await storage.close()
    return report


async def _close_provider(provider: object) -> None:
    close = getattr(provider, "close", None)
    if close is not None:
        result = close()
        if asyncio.iscoroutine(result):
            await result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_sample(parse_args())), ensure_ascii=False, indent=2))
