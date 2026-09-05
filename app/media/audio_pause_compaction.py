"""Conservative pause compaction for one continuous narration waveform.

The provider remains responsible for synthesizing the voice.  This module only
removes excess time from low-energy gaps that Edge TTS reports between adjacent
word events; voiced samples are never time-stretched.  Because every cut is
inside a provider-reported gap, the result remains one continuous waveform and
does not need phrase-level ``atempo`` or ``acrossfade`` processing.
"""

from __future__ import annotations

import asyncio
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.providers.errors_audio import AudioPauseCompactionError


@dataclass(frozen=True, slots=True)
class AudioPauseCompactionResult:
    """The derived waveform, remapped timing metadata and operation trace."""

    content: bytes
    content_type: str
    changed: bool
    removed_seconds: float
    word_boundaries: list[dict[str, object]]
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class _PauseCut:
    source_start_seconds: float
    source_end_seconds: float
    original_gap_seconds: float
    kept_gap_seconds: float
    kind: str
    punctuation: str

    @property
    def removed_seconds(self) -> float:
        return self.source_end_seconds - self.source_start_seconds


class FFmpegNarrationPauseCompactor:
    """Shorten only excessive inter-word pauses in a continuous waveform.

    This is intentionally different from local pacing: no speech region is
    sped up or slowed down.  The operation is useful when a neural voice emits
    a long sentence pause at a visual transition and makes the result sound
    like independent audio clips.
    """

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 120,
        min_gap_seconds: float = 0.20,
        soft_keep_seconds: float = 0.12,
        strong_keep_seconds: float = 0.22,
        neutral_keep_seconds: float = 0.10,
        trailing_keep_seconds: float = 0.18,
        tolerance_seconds: float = 0.015,
        allowed_gap_keep_seconds: float | None = None,
    ) -> None:
        values = (
            min_gap_seconds,
            soft_keep_seconds,
            strong_keep_seconds,
            neutral_keep_seconds,
            trailing_keep_seconds,
            tolerance_seconds,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("pause compaction durations must be finite and non-negative")
        if (
            allowed_gap_keep_seconds is not None
            and (
                not math.isfinite(allowed_gap_keep_seconds)
                or allowed_gap_keep_seconds < 0
            )
        ):
            raise ValueError(
                "allowed_gap_keep_seconds must be finite and non-negative"
            )
        if soft_keep_seconds > strong_keep_seconds:
            raise ValueError("soft pause cannot be longer than strong pause")
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.min_gap_seconds = min_gap_seconds
        self.soft_keep_seconds = soft_keep_seconds
        self.strong_keep_seconds = strong_keep_seconds
        self.neutral_keep_seconds = neutral_keep_seconds
        self.trailing_keep_seconds = trailing_keep_seconds
        self.tolerance_seconds = tolerance_seconds
        self.allowed_gap_keep_seconds = allowed_gap_keep_seconds

    async def compact(
        self,
        content: bytes,
        content_type: str,
        source_duration_seconds: float,
        text: str,
        raw_word_boundaries: object,
        *,
        allowed_gap_after_character_offsets: set[int] | None = None,
    ) -> AudioPauseCompactionResult:
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if not content or not normalized_type.startswith("audio/"):
            raise AudioPauseCompactionError(
                "AUDIO_PAUSE_COMPACTION_INPUT_INVALID",
                "Pause compaction requires non-empty audio content",
            )
        if (
            not math.isfinite(source_duration_seconds)
            or source_duration_seconds <= 0
            or source_duration_seconds > 3600
        ):
            raise AudioPauseCompactionError(
                "AUDIO_PAUSE_COMPACTION_INPUT_INVALID",
                "Narration duration must be positive and no longer than 3600 seconds",
            )

        boundaries = self._normalize_boundaries(raw_word_boundaries)
        normalized_allowed_offsets = (
            None
            if allowed_gap_after_character_offsets is None
            else self._normalize_character_offsets(allowed_gap_after_character_offsets)
        )
        if (
            normalized_allowed_offsets is not None
            and self.allowed_gap_keep_seconds is not None
        ):
            cuts, selected_gap_count = self._build_scene_boundary_cuts(
                text,
                boundaries,
                source_duration_seconds,
                normalized_allowed_offsets,
                self.allowed_gap_keep_seconds,
            )
            compaction_scope = "selected_character_boundaries_uniform_keep"
        else:
            cuts = self._build_cuts(
                text,
                boundaries,
                source_duration_seconds,
                allowed_gap_after_character_offsets=normalized_allowed_offsets,
            )
            selected_gap_count = len(normalized_allowed_offsets) if normalized_allowed_offsets is not None else None
            compaction_scope = (
                "all_provider_gaps"
                if normalized_allowed_offsets is None
                else "selected_character_boundaries_only"
            )
        if not cuts:
            return AudioPauseCompactionResult(
                content=content,
                content_type=normalized_type,
                changed=False,
                removed_seconds=0.0,
                word_boundaries=boundaries,
                metadata={
                    "pause_compaction": "not_needed",
                    "filter": "none",
                    "source_duration_seconds": round(source_duration_seconds, 6),
                    "removed_pause_seconds": 0.0,
                    "pause_cut_count": 0,
                    "word_boundaries_remapped": False,
                    "compaction_scope": compaction_scope,
                    "allowed_gap_after_character_count": (
                        None
                        if normalized_allowed_offsets is None
                        else len(normalized_allowed_offsets)
                    ),
                    "allowed_gap_keep_seconds": (
                        round(self.allowed_gap_keep_seconds, 6)
                        if self.allowed_gap_keep_seconds is not None
                        else None
                    ),
                    "selected_gap_count": selected_gap_count,
                },
            )

        if shutil.which(self.binary) is None:
            raise AudioPauseCompactionError(
                "AUDIO_PAUSE_COMPACTION_UNAVAILABLE",
                f"{self.binary} is required to compact narration pauses",
            )

        intervals = self._remaining_intervals(source_duration_seconds, cuts)
        compacted_content = await self._render(content, normalized_type, intervals)
        if not compacted_content:
            raise AudioPauseCompactionError(
                "AUDIO_PAUSE_COMPACTION_FAILED",
                "FFmpeg produced an empty compacted narration Artifact",
            )

        removed_seconds = sum(cut.removed_seconds for cut in cuts)
        remapped_boundaries = [
            {
                **boundary,
                "start_seconds": round(
                    self._map_time(float(boundary["start_seconds"]), cuts),
                    6,
                ),
                "end_seconds": round(
                    self._map_time(float(boundary["end_seconds"]), cuts),
                    6,
                ),
            }
            for boundary in boundaries
        ]
        return AudioPauseCompactionResult(
            content=compacted_content,
            content_type="audio/wav",
            changed=True,
            removed_seconds=round(removed_seconds, 6),
            word_boundaries=remapped_boundaries,
            metadata={
                "pause_compaction": "applied",
                "filter": "gap_trim_concat",
                "source_duration_seconds": round(source_duration_seconds, 6),
                "estimated_output_duration_seconds": round(
                    source_duration_seconds - removed_seconds,
                    6,
                ),
                "removed_pause_seconds": round(removed_seconds, 6),
                "pause_cut_count": len(cuts),
                "word_boundaries_remapped": True,
                "voiced_audio_time_stretched": False,
                "compaction_scope": compaction_scope,
                "allowed_gap_after_character_count": (
                    None
                    if normalized_allowed_offsets is None
                    else len(normalized_allowed_offsets)
                ),
                "allowed_gap_keep_seconds": (
                    round(self.allowed_gap_keep_seconds, 6)
                    if self.allowed_gap_keep_seconds is not None
                    else None
                ),
                "selected_gap_count": selected_gap_count,
                "pause_cuts": [
                    {
                        "kind": cut.kind,
                        "punctuation": cut.punctuation,
                        "source_start_seconds": round(cut.source_start_seconds, 6),
                        "source_end_seconds": round(cut.source_end_seconds, 6),
                        "original_gap_seconds": round(cut.original_gap_seconds, 6),
                        "kept_gap_seconds": round(cut.kept_gap_seconds, 6),
                        "removed_seconds": round(cut.removed_seconds, 6),
                    }
                    for cut in cuts
                ],
            },
        )

    def _build_cuts(
        self,
        text: str,
        boundaries: list[dict[str, object]],
        source_duration_seconds: float,
        *,
        allowed_gap_after_character_offsets: set[int] | None = None,
    ) -> list[_PauseCut]:
        positions = [index for index, character in enumerate(text) if character.isalnum()]
        spans: list[tuple[dict[str, object], int, int, int, int]] = []
        cursor = 0
        for boundary in boundaries:
            token = self._timing_text(str(boundary["text"]))
            token_length = len(token)
            if token_length <= 0 or cursor + token_length > len(positions):
                return []
            start_index = positions[cursor]
            end_index = positions[cursor + token_length - 1] + 1
            spans.append(
                (
                    boundary,
                    start_index,
                    end_index,
                    cursor,
                    cursor + token_length,
                )
            )
            cursor += token_length
        if cursor != len(positions):
            return []

        cuts: list[_PauseCut] = []
        for previous, next_item in zip(spans, spans[1:]):
            (
                previous_boundary,
                _previous_text_start,
                previous_text_end,
                _previous_character_start,
                previous_character_end,
            ) = previous
            next_boundary, next_text_start, _next_text_end, _, _ = next_item
            if (
                allowed_gap_after_character_offsets is not None
                and previous_character_end not in allowed_gap_after_character_offsets
            ):
                continue
            start = self._finite_number(previous_boundary.get("end_seconds"))
            end = self._finite_number(next_boundary.get("start_seconds"))
            if start is None or end is None or end <= start:
                continue
            gap = end - start
            punctuation = text[previous_text_end:next_text_start]
            kind, kept = self._pause_policy(punctuation, gap)
            if gap <= kept + self.tolerance_seconds:
                continue
            cuts.append(
                _PauseCut(
                    source_start_seconds=start + kept,
                    source_end_seconds=end,
                    original_gap_seconds=gap,
                    kept_gap_seconds=kept,
                    kind=kind,
                    punctuation=punctuation,
                )
            )

        last_end = self._finite_number(boundaries[-1].get("end_seconds")) if boundaries else None
        if (
            allowed_gap_after_character_offsets is None
            and last_end is not None
            and source_duration_seconds - last_end > self.trailing_keep_seconds + self.tolerance_seconds
        ):
            cuts.append(
                _PauseCut(
                    source_start_seconds=last_end + self.trailing_keep_seconds,
                    source_end_seconds=source_duration_seconds,
                    original_gap_seconds=source_duration_seconds - last_end,
                    kept_gap_seconds=self.trailing_keep_seconds,
                    kind="trailing",
                    punctuation="",
                )
            )
        return self._merge_or_sort_cuts(cuts, source_duration_seconds)

    def _build_scene_boundary_cuts(
        self,
        text: str,
        boundaries: list[dict[str, object]],
        source_duration_seconds: float,
        scene_boundary_character_offsets: set[int],
        keep_seconds: float,
    ) -> tuple[list[_PauseCut], int]:
        positions = [index for index, character in enumerate(text) if character.isalnum()]
        spans: list[tuple[dict[str, object], int, int, int, int]] = []
        cursor = 0
        for boundary in boundaries:
            token_length = len(self._timing_text(str(boundary["text"])))
            if token_length <= 0 or cursor + token_length > len(positions):
                return [], 0
            spans.append(
                (
                    boundary,
                    positions[cursor],
                    positions[cursor + token_length - 1] + 1,
                    cursor,
                    cursor + token_length,
                )
            )
            cursor += token_length
        if cursor != len(positions):
            return [], 0

        cuts: list[_PauseCut] = []
        gap_count = 0
        for previous, following in zip(spans, spans[1:]):
            (
                previous_boundary,
                _previous_text_start,
                previous_text_end,
                _previous_character_start,
                previous_character_end,
            ) = previous
            next_boundary, next_text_start, _next_text_end, next_character_start, _ = following
            if previous_character_end not in scene_boundary_character_offsets:
                continue
            # A provider token crossing the scene boundary has no safe silent
            # interval to edit. Do not interpolate or remove voiced samples.
            if next_character_start != previous_character_end:
                continue
            start = self._finite_number(previous_boundary.get("end_seconds"))
            end = self._finite_number(next_boundary.get("start_seconds"))
            if start is None or end is None or end <= start:
                continue
            gap = end - start
            if gap <= 0:
                continue
            gap_count += 1
            if gap <= keep_seconds + self.tolerance_seconds:
                continue
            cuts.append(
                _PauseCut(
                    source_start_seconds=start + keep_seconds,
                    source_end_seconds=end,
                    original_gap_seconds=gap,
                    kept_gap_seconds=keep_seconds,
                    kind="scene_boundary",
                    punctuation=text[previous_text_end:next_text_start],
                )
            )
        return self._merge_or_sort_cuts(cuts, source_duration_seconds), gap_count

    def _pause_policy(self, punctuation: str, gap: float) -> tuple[str, float]:
        if any(character in "。！？!?；;…" for character in punctuation):
            return "strong", self.strong_keep_seconds
        if any(character in "，,、：:" for character in punctuation):
            return "soft", self.soft_keep_seconds
        if gap >= self.min_gap_seconds:
            return "neutral", self.neutral_keep_seconds
        return "short", gap

    @staticmethod
    def _merge_or_sort_cuts(cuts: list[_PauseCut], duration: float) -> list[_PauseCut]:
        ordered = sorted(cuts, key=lambda cut: cut.source_start_seconds)
        result: list[_PauseCut] = []
        for cut in ordered:
            start = max(0.0, min(duration, cut.source_start_seconds))
            end = max(0.0, min(duration, cut.source_end_seconds))
            if end <= start:
                continue
            if result and start < result[-1].source_end_seconds:
                previous = result[-1]
                result[-1] = _PauseCut(
                    source_start_seconds=previous.source_start_seconds,
                    source_end_seconds=max(previous.source_end_seconds, end),
                    original_gap_seconds=previous.original_gap_seconds,
                    kept_gap_seconds=previous.kept_gap_seconds,
                    kind=previous.kind,
                    punctuation=previous.punctuation,
                )
                continue
            result.append(
                _PauseCut(
                    source_start_seconds=start,
                    source_end_seconds=end,
                    original_gap_seconds=cut.original_gap_seconds,
                    kept_gap_seconds=cut.kept_gap_seconds,
                    kind=cut.kind,
                    punctuation=cut.punctuation,
                )
            )
        return result

    @staticmethod
    def _remaining_intervals(
        duration: float,
        cuts: list[_PauseCut],
    ) -> list[tuple[float, float]]:
        intervals: list[tuple[float, float]] = []
        cursor = 0.0
        for cut in cuts:
            if cut.source_start_seconds > cursor:
                intervals.append((cursor, cut.source_start_seconds))
            cursor = max(cursor, cut.source_end_seconds)
        if cursor < duration:
            intervals.append((cursor, duration))
        return [(start, end) for start, end in intervals if end - start > 0.001]

    async def _render(
        self,
        content: bytes,
        content_type: str,
        intervals: list[tuple[float, float]],
    ) -> bytes:
        if not intervals:
            raise AudioPauseCompactionError(
                "AUDIO_PAUSE_COMPACTION_FAILED",
                "Pause compaction removed the complete narration waveform",
            )
        with TemporaryDirectory(prefix="ai-audio-pause-compaction-") as directory:
            root = Path(directory)
            input_path = root / f"input{self._suffix_for_mime(content_type)}"
            output_path = root / "compacted.wav"
            await asyncio.to_thread(input_path.write_bytes, content)
            parts = [
                (
                    f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )
                for index, (start, end) in enumerate(intervals)
            ]
            labels = "".join(f"[a{index}]" for index in range(len(parts)))
            filter_graph = (
                ";".join(parts)
                + ";"
                + labels
                + f"concat=n={len(parts)}:v=0:a=1,"
                "aresample=async=1:first_pts=0,asetpts=N/SR/TB"
            )
            command = [
                self.binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(input_path),
                "-filter_complex",
                filter_graph,
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(output_path),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise AudioPauseCompactionError(
                    "AUDIO_PAUSE_COMPACTION_UNAVAILABLE",
                    f"{self.binary} is required to compact narration pauses",
                ) from exc
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise AudioPauseCompactionError(
                    "AUDIO_PAUSE_COMPACTION_TIMEOUT",
                    "FFmpeg timed out while compacting narration pauses",
                ) from exc
            if process.returncode != 0 or not output_path.is_file():
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg could not compact narration pauses"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise AudioPauseCompactionError("AUDIO_PAUSE_COMPACTION_FAILED", message)
            return await asyncio.to_thread(output_path.read_bytes)

    @staticmethod
    def _normalize_boundaries(raw_boundaries: object) -> list[dict[str, object]]:
        if not isinstance(raw_boundaries, list) or not raw_boundaries:
            return []
        boundaries: list[dict[str, object]] = []
        previous_end = 0.0
        for item in raw_boundaries:
            if not isinstance(item, dict):
                return []
            text = item.get("text")
            start = FFmpegNarrationPauseCompactor._finite_number(item.get("start_seconds"))
            end = FFmpegNarrationPauseCompactor._finite_number(item.get("end_seconds"))
            if not isinstance(text, str) or not text.strip() or start is None or end is None:
                return []
            if start < 0 or end <= start or start + 0.001 < previous_end:
                return []
            boundaries.append(
                {
                    "text": text,
                    "start_seconds": start,
                    "end_seconds": end,
                }
            )
            previous_end = end
        return boundaries

    @staticmethod
    def _normalize_character_offsets(raw_offsets: object) -> set[int]:
        if not isinstance(raw_offsets, (set, list, tuple)):
            return set()
        offsets: set[int] = set()
        for value in raw_offsets:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                return set()
            offsets.add(value)
        return offsets

    @staticmethod
    def _timing_text(text: str) -> str:
        return "".join(character for character in text if character.isalnum())

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return None
        return float(value)

    @staticmethod
    def _map_time(value: float, cuts: list[_PauseCut]) -> float:
        removed = 0.0
        for cut in cuts:
            if value <= cut.source_start_seconds:
                break
            if value >= cut.source_end_seconds:
                removed += cut.removed_seconds
                continue
            return cut.source_start_seconds - removed
        return value - removed

    @staticmethod
    def _suffix_for_mime(content_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/webm": ".webm",
        }.get(content_type, ".audio")
