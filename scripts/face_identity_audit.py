#!/usr/bin/env python3
"""Inspect a reference image and sampled video frames with InsightFace.

This helper is intentionally executed by the ComfyUI Python environment, not
the application virtualenv.  It emits one JSON object on stdout so the worker
can persist a conservative, inspectable report.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def main() -> int:
    args = _parse_args()
    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except Exception as exc:  # pragma: no cover - exercised in the external runtime
        _emit({
            "status": "unavailable",
            "reason": "identity_audit_dependencies_missing",
            "detail": str(exc)[:500],
            "human_review_required": True,
        })
        return 0

    try:
        reference = cv2.imread(str(args.reference))
        if reference is None:
            _emit({
                "status": "error",
                "reason": "reference_image_decode_failed",
                "human_review_required": True,
            })
            return 1

        model_root = args.model_root or os.getenv("INSIGHTFACE_MODEL_ROOT") or None
        app = FaceAnalysis(
            name=args.model_name,
            root=model_root,
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=-1, det_size=(640, 640))
        reference_faces = app.get(reference)
        if not reference_faces:
            _emit({
                "status": "reference_no_face",
                "reference_face_count": 0,
                "video_faces_detected": 0,
                "human_review_required": True,
            })
            return 0
        reference_face = max(reference_faces, key=_face_area)
        reference_embedding = _embedding(reference_face)
        if reference_embedding is None:
            _emit({
                "status": "reference_no_face",
                "reference_face_count": len(reference_faces),
                "video_faces_detected": 0,
                "reason": "reference_embedding_missing",
                "human_review_required": True,
            })
            return 0

        capture = cv2.VideoCapture(str(args.video))
        if not capture.isOpened():
            _emit({
                "status": "error",
                "reason": "video_decode_failed",
                "reference_face_count": len(reference_faces),
                "human_review_required": True,
            })
            return 1
        try:
            total_frames = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            indices = _sample_indices(total_frames, args.max_frames)
            similarities: list[float] = []
            video_faces_detected = 0
            frames_with_faces = 0
            decoded_frames = 0
            for index in indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                decoded_frames += 1
                faces = app.get(frame)
                video_faces_detected += len(faces)
                if not faces:
                    continue
                frames_with_faces += 1
                frame_similarities = [
                    _cosine_similarity(reference_embedding, embedding)
                    for face in faces
                    if (embedding := _embedding(face)) is not None
                ]
                if frame_similarities:
                    similarities.append(max(frame_similarities))
        finally:
            capture.release()

        if not similarities:
            _emit({
                "status": "no_face",
                "reference_face_count": len(reference_faces),
                "video_faces_detected": video_faces_detected,
                "sampled_frame_count": len(indices),
                "decoded_frame_count": decoded_frames,
                "frames_with_faces": frames_with_faces,
                "threshold": args.threshold,
                "human_review_required": True,
            })
            return 0

        minimum = min(similarities)
        mean = sum(similarities) / len(similarities)
        maximum = max(similarities)
        status = "passed" if minimum >= args.threshold else "failed"
        _emit({
            "status": status,
            "reference_face_count": len(reference_faces),
            "video_faces_detected": video_faces_detected,
            "sampled_frame_count": len(indices),
            "decoded_frame_count": decoded_frames,
            "frames_with_faces": frames_with_faces,
            "similarity_count": len(similarities),
            "min_similarity": round(float(minimum), 6),
            "mean_similarity": round(float(mean), 6),
            "max_similarity": round(float(maximum), 6),
            "threshold": args.threshold,
            "model": args.model_name,
            "human_review_required": status != "passed" or len(reference_faces) != 1,
        })
        return 0
    except Exception as exc:  # pragma: no cover - exercised in the external runtime
        _emit({
            "status": "error",
            "reason": "identity_audit_failed",
            "detail": str(exc)[:500],
            "human_review_required": True,
        })
        return 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--model-root", default="")
    parser.add_argument("--model-name", default="antelopev2")
    parser.add_argument("--threshold", default=0.4, type=float)
    parser.add_argument("--max-frames", default=8, type=int)
    return parser.parse_args()


def _sample_indices(total_frames: int, max_frames: int) -> list[int]:
    if total_frames <= 0:
        return [0]
    count = max(1, min(total_frames, max_frames))
    if count == 1:
        return [0]
    return sorted({round(index * (total_frames - 1) / (count - 1)) for index in range(count)})


def _face_area(face: Any) -> float:
    bbox = getattr(face, "bbox", None)
    if bbox is None or len(bbox) < 4:
        return 0.0
    return max(0.0, float(bbox[2] - bbox[0])) * max(0.0, float(bbox[3] - bbox[1]))


def _embedding(face: Any) -> Any:
    embedding = getattr(face, "normed_embedding", None)
    if embedding is None:
        embedding = getattr(face, "embedding", None)
    return embedding


def _cosine_similarity(left: Any, right: Any) -> float:
    import numpy as np

    left_array = np.asarray(left, dtype=np.float32)
    right_array = np.asarray(right, dtype=np.float32)
    left_norm = float(np.linalg.norm(left_array))
    right_norm = float(np.linalg.norm(right_array))
    if left_norm <= 0 or right_norm <= 0:
        return -1.0
    return float(np.dot(left_array, right_array) / (left_norm * right_norm))


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
