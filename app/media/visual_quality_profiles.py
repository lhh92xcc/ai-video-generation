"""Versioned, provider-neutral visual quality presets.

The presets describe the image/video parameters that make a local run
repeatable.  They are deliberately not tied to a particular graphics card:
the selected preset is a starting point and the resulting media still needs
human review on the target machine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from math import isfinite


class VisualQualityProfileError(Exception):
    """A requested visual quality preset cannot be resolved."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class VisualQualityProfile:
    """A safe image/video generation preset that can be stored as a snapshot."""

    profile_id: str
    label: str
    description: str
    recommended_for: str
    image_width: int
    image_height: int
    image_steps: int
    image_guidance: float
    image_identity_weight: float
    video_width: int
    video_height: int
    video_fps: int
    video_steps: int
    video_cfg: float
    video_noise_aug_strength: float
    video_motion_zoom: float
    version: str = "visual-quality-v1"

    def as_public_dict(self) -> dict[str, object]:
        """Return the complete non-sensitive preset for the UI and API."""

        return asdict(self)

    def as_snapshot(self) -> dict[str, object]:
        """Return a detached task snapshot so later preset edits do not alter history."""

        return dict(self.as_public_dict())

    @classmethod
    def from_snapshot(cls, snapshot: object) -> "VisualQualityProfile":
        """Rebuild a validated profile from a task's persisted JSON snapshot."""

        if not isinstance(snapshot, dict):
            raise TypeError("visual quality snapshot must be an object")
        field_names = {item.name for item in fields(cls)}
        missing = field_names.difference(snapshot)
        if missing:
            raise ValueError(f"visual quality snapshot is missing {sorted(missing)}")

        values = {name: snapshot[name] for name in field_names}
        text_fields = {"profile_id", "label", "description", "recommended_for", "version"}
        int_fields = {
            "image_width",
            "image_height",
            "image_steps",
            "video_width",
            "video_height",
            "video_fps",
            "video_steps",
        }
        float_fields = {
            "image_guidance",
            "image_identity_weight",
            "video_cfg",
            "video_noise_aug_strength",
            "video_motion_zoom",
        }
        for name in text_fields:
            if not isinstance(values[name], str) or not values[name].strip():
                raise TypeError(f"visual quality snapshot field {name!r} must be non-empty text")
        for name in int_fields:
            if isinstance(values[name], bool) or not isinstance(values[name], int) or values[name] < 1:
                raise TypeError(f"visual quality snapshot field {name!r} must be a positive integer")
        for name in float_fields:
            if isinstance(values[name], bool) or not isinstance(values[name], (int, float)):
                raise TypeError(f"visual quality snapshot field {name!r} must be numeric")
            if not isfinite(float(values[name])):
                raise ValueError(f"visual quality snapshot field {name!r} must be finite")

        return cls(**values)


class VisualQualityProfileRegistry:
    """Resolve the small, reviewed set of visual quality presets."""

    _PROFILES: tuple[VisualQualityProfile, ...] = (
        VisualQualityProfile(
            profile_id="local_safe",
            label="Local Safe · 保守稳定",
            description="较低分辨率与采样压力，优先保证本地 GPU 能完整跑通。",
            recommended_for="本地 smoke、低显存或首次验收",
            image_width=512,
            image_height=768,
            image_steps=4,
            image_guidance=3.5,
            image_identity_weight=0.90,
            video_width=320,
            video_height=576,
            video_fps=8,
            video_steps=6,
            video_cfg=5.0,
            video_noise_aug_strength=0.02,
            video_motion_zoom=1.08,
        ),
        VisualQualityProfile(
            profile_id="local_balanced",
            label="Local Balanced · 平衡质量",
            description="提高关键帧和视频采样规格，仍保持逐镜头、低并发的本地路线。",
            recommended_for="目标 GPU 主机的首轮作品集验收",
            image_width=576,
            image_height=1024,
            image_steps=6,
            image_guidance=4.0,
            image_identity_weight=0.92,
            video_width=384,
            video_height=672,
            video_fps=12,
            video_steps=8,
            video_cfg=5.5,
            video_noise_aug_strength=0.015,
            video_motion_zoom=1.06,
        ),
        VisualQualityProfile(
            profile_id="high_quality",
            label="High Quality · 高质量候选",
            description="更高分辨率和采样预算，质量潜力更高，但显存、耗时和失败率必须实测。",
            recommended_for="高质量对照样片、单任务低并发",
            image_width=720,
            image_height=1280,
            image_steps=8,
            image_guidance=4.5,
            image_identity_weight=0.95,
            video_width=576,
            video_height=1024,
            video_fps=16,
            video_steps=12,
            video_cfg=6.0,
            video_noise_aug_strength=0.01,
            video_motion_zoom=1.04,
        ),
    )

    def __init__(self, default_profile_id: str = "local_safe") -> None:
        self._profiles = {profile.profile_id: profile for profile in self._PROFILES}
        self._default_profile_id = (
            default_profile_id if default_profile_id in self._profiles else "local_safe"
        )

    @property
    def default_profile_id(self) -> str:
        return self._default_profile_id

    def list_profiles(self) -> list[VisualQualityProfile]:
        return list(self._profiles.values())

    def resolve(self, profile_id: str | None = None) -> VisualQualityProfile:
        selected_id = profile_id or self._default_profile_id
        profile = self._profiles.get(selected_id)
        if profile is None:
            raise VisualQualityProfileError(
                "VISUAL_QUALITY_PROFILE_NOT_FOUND",
                f"Visual quality profile {selected_id!r} was not found",
            )
        return profile
