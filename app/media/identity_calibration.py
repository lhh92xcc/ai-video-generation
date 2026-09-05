"""Read-only threshold calibration for persisted identity-audit reports."""

from __future__ import annotations

import math
from collections import Counter
from uuid import UUID

from app.domain.models import (
    ArtifactRecord,
    IdentityCalibrationRequest,
    IdentityCalibrationResponse,
    IdentityCalibrationThresholdResult,
)
from app.repositories.protocol import ProjectTaskStore


class IdentityCalibrationService:
    """Compare candidate thresholds without changing the production setting."""

    def __init__(self, store: ProjectTaskStore) -> None:
        self._store = store

    async def calibrate(
        self,
        project_id: UUID,
        request: IdentityCalibrationRequest,
        current_threshold: float,
    ) -> IdentityCalibrationResponse:
        artifacts = await self._store.list_artifacts(
            project_id=project_id,
            artifact_type="video_clip",
            limit=request.max_artifacts,
        )
        selected = [artifact for artifact in artifacts if self._matches_episode(artifact, request)]
        reports = [
            artifact.metadata.get("identity_audit")
            for artifact in selected
            if isinstance(artifact.metadata.get("identity_audit"), dict)
        ]
        status_counts = Counter(
            str(report.get("status", "unknown"))
            for report in reports
        )
        scores = [
            float(report["min_similarity"])
            for report in reports
            if self._is_number(report.get("min_similarity"))
        ]
        threshold_results = [
            self._threshold_result(threshold, scores)
            for threshold in request.thresholds
        ]
        return IdentityCalibrationResponse(
            project_id=project_id,
            episode_id=request.episode_id,
            current_threshold=round(float(current_threshold), 6),
            artifact_count=len(selected),
            audited_artifact_count=len(reports),
            eligible_sample_count=len(scores),
            # This counts every selected video artifact that cannot contribute
            # a numeric similarity sample, including artifacts without an
            # identity report at all.  Counting only ``reports`` would make an
            # unaudited clip disappear from the calibration accounting.
            excluded_artifact_count=max(0, len(selected) - len(scores)),
            status_counts=dict(sorted(status_counts.items())),
            thresholds=threshold_results,
        )

    @staticmethod
    def _matches_episode(
        artifact: ArtifactRecord,
        request: IdentityCalibrationRequest,
    ) -> bool:
        if request.episode_id is None:
            return True
        return str(artifact.metadata.get("episode_id", "")) == str(request.episode_id)

    @staticmethod
    def _is_number(value: object) -> bool:
        if isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _threshold_result(
        threshold: float,
        scores: list[float],
    ) -> IdentityCalibrationThresholdResult:
        passed = sum(score >= threshold for score in scores)
        sample_count = len(scores)
        return IdentityCalibrationThresholdResult(
            threshold=round(float(threshold), 6),
            passed_count=passed,
            failed_count=sample_count - passed,
            sample_count=sample_count,
            pass_rate=round(passed / sample_count, 6) if sample_count else 0.0,
        )
