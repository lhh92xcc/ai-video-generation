"""Experimental bounded local pacing for one continuous narration waveform.

The TTS provider still synthesizes one waveform.  This module only applies a
small, auditable ``atempo`` adjustment to timing-safe slices of that waveform
and joins the slices with a short crossfade.  It is intended to smooth local
rate spikes without recreating independent voices or hard-cutting audio. It is
not used by the portfolio runner: phrase-level waveform surgery can still be
audible on neural speech, so the default path keeps the provider waveform
untouched. Keep this module isolated for experiments and regression tests.
"""

from __future__ import annotations

import asyncio
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from collections.abc import Sequence

from app.media.narration_text import join_narration_units, normalize_narration_text
from app.providers.errors_audio import AudioPacingError


@dataclass(frozen=True, slots=True)
class AudioPacingSegment:
    """One contiguous source slice and its bounded tempo factor."""

    source_start_seconds: float
    source_end_seconds: float
    tempo_factor: float = 1.0
    label: str = ""


@dataclass(frozen=True, slots=True)
class AudioPacingResult:
    """The derived continuous waveform and the applied pacing metadata."""

    content: bytes
    content_type: str
    changed: bool
    crossfade_seconds: float
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class NarrationPhraseSpan:
    """A phrase mapped to a source-waveform interval."""

    scene_index: int
    text: str
    character_count: int
    character_start: int
    character_end: int
    source_start_seconds: float
    source_end_seconds: float
    speech_start_seconds: float
    speech_end_seconds: float
    tempo_factor: float


@dataclass(frozen=True, slots=True)
class NarrationPacingPlan:
    """Phrase slices that cover one complete continuous narration waveform."""

    text: str
    phrases: tuple[NarrationPhraseSpan, ...]
    segments: tuple[AudioPacingSegment, ...]
    target_characters_per_second: float


@dataclass(frozen=True, slots=True)
class PacedSceneTiming:
    """One scene's measured timing after phrase pacing and crossfades."""

    start_seconds: float
    end_seconds: float
    speech_start_seconds: float
    speech_end_seconds: float


_PHRASE_BOUNDARIES = frozenset("，,、：:；;。！？!? \t")


def build_narration_pacing_plan(
    scenes: Sequence[tuple[str, str, str]],
    raw_word_boundaries: object,
    source_duration_seconds: float,
    *,
    target_characters_per_second: float = 3.8,
    min_tempo_factor: float = 0.82,
    max_tempo_factor: float = 1.08,
) -> NarrationPacingPlan | None:
    """Build safe phrase slices from one provider word-boundary timeline.

    The input waveform remains continuous.  The returned source intervals are
    contiguous and each phrase owns the pause before the next phrase.  A small
    bounded ``atempo`` correction is computed from spoken characters rather
    than from the visual shot duration, so a short fast phrase cannot make the
    whole episode sound like fast-forward.
    """

    if (
        not scenes
        or not isinstance(raw_word_boundaries, list)
        or not math.isfinite(source_duration_seconds)
        or source_duration_seconds <= 0
        or target_characters_per_second <= 0
        or not 0 < min_tempo_factor <= 1
        or max_tempo_factor < 1
    ):
        return None

    scene_texts = [
        join_narration_units([text])
        for _title, text, _prompt in scenes
        if normalize_narration_text(text)
    ]
    if len(scene_texts) != len(scenes) or not all(scene_texts):
        return None
    full_text = join_narration_units(scene_texts)

    boundaries: list[tuple[int, int, float, float]] = []
    source_character_cursor = 0
    for item in raw_word_boundaries:
        if not isinstance(item, dict):
            return None
        token_text = item.get("text")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        token = _timing_text(token_text) if isinstance(token_text, str) else ""
        if not token:
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        start_seconds = float(start)
        end_seconds = float(end)
        if (
            not math.isfinite(start_seconds)
            or not math.isfinite(end_seconds)
            or start_seconds < 0
            or end_seconds <= start_seconds
        ):
            return None
        boundaries.append(
            (
                source_character_cursor,
                source_character_cursor + len(token),
                start_seconds,
                end_seconds,
            )
        )
        source_character_cursor += len(token)
    target_character_count = len(_timing_text(full_text))
    if not boundaries or source_character_cursor != target_character_count:
        return None

    def time_at_character(character_offset: int) -> float:
        if character_offset <= 0:
            return boundaries[0][2]
        if character_offset >= source_character_cursor:
            return boundaries[-1][3]
        for index, (token_start, token_end, start, end) in enumerate(boundaries):
            if character_offset < token_end:
                fraction = (character_offset - token_start) / (token_end - token_start)
                return start + (end - start) * fraction
            if character_offset == token_end:
                return boundaries[index + 1][2] if index + 1 < len(boundaries) else end
        return boundaries[-1][3]

    def speech_start_at_character(character_offset: int) -> float:
        for token_start, token_end, start, end in boundaries:
            if character_offset < token_end:
                fraction = max(0, character_offset - token_start) / (token_end - token_start)
                return start + (end - start) * fraction
        return boundaries[-1][3]

    def speech_end_at_character(character_offset: int) -> float:
        previous_end = boundaries[0][2]
        for token_start, token_end, start, end in boundaries:
            if character_offset <= token_start:
                return previous_end
            if character_offset < token_end:
                fraction = (character_offset - token_start) / (token_end - token_start)
                return start + (end - start) * fraction
            previous_end = end
        return boundaries[-1][3]

    phrase_specs: list[tuple[int, str, int, int]] = []
    global_character_offset = 0
    for scene_index, scene_text in enumerate(scene_texts, start=1):
        phrase_start_raw_index = 0
        phrase_start_character_offset = 0
        local_character_offset = 0
        for raw_index, character in enumerate(scene_text):
            if character.isalnum():
                local_character_offset += 1
            if character not in _PHRASE_BOUNDARIES:
                continue
            if local_character_offset <= phrase_start_character_offset:
                phrase_start_raw_index = raw_index + 1
                continue
            phrase_text = scene_text[phrase_start_raw_index:raw_index].strip()
            if phrase_text:
                phrase_specs.append(
                    (
                        scene_index,
                        phrase_text,
                        global_character_offset + phrase_start_character_offset,
                        global_character_offset + local_character_offset,
                    )
                )
            phrase_start_raw_index = raw_index + 1
            phrase_start_character_offset = local_character_offset
        if local_character_offset > phrase_start_character_offset:
            phrase_text = scene_text[phrase_start_raw_index:].strip()
            if phrase_text:
                phrase_specs.append(
                    (
                        scene_index,
                        phrase_text,
                        global_character_offset + phrase_start_character_offset,
                        global_character_offset + local_character_offset,
                    )
                )
        global_character_offset += local_character_offset

    if not phrase_specs:
        return None

    phrases: list[NarrationPhraseSpan] = []
    for index, (scene_index, phrase_text, character_start, character_end) in enumerate(
        phrase_specs
    ):
        character_count = character_end - character_start
        if character_count <= 0:
            continue
        speech_start = speech_start_at_character(character_start)
        speech_end = speech_end_at_character(character_end)
        if speech_end <= speech_start:
            return None
        next_phrase_start = (
            time_at_character(phrase_specs[index + 1][2])
            if index + 1 < len(phrase_specs)
            else source_duration_seconds
        )
        source_start = 0.0 if index == 0 else time_at_character(character_start)
        source_end = min(source_duration_seconds, next_phrase_start)
        target_speech_duration = character_count / target_characters_per_second
        characters_per_second = character_count / (speech_end - speech_start)
        raw_tempo = target_characters_per_second / characters_per_second
        tempo_factor = max(
            min_tempo_factor,
            min(1.0, max_tempo_factor, raw_tempo),
        )
        if source_end <= source_start:
            return None
        phrases.append(
            NarrationPhraseSpan(
                scene_index=scene_index,
                text=phrase_text,
                character_count=character_count,
                character_start=character_start,
                character_end=character_end,
                source_start_seconds=round(source_start, 6),
                source_end_seconds=round(source_end, 6),
                speech_start_seconds=round(speech_start, 6),
                speech_end_seconds=round(speech_end, 6),
                tempo_factor=round(tempo_factor, 6),
            )
        )

    if not phrases or abs(phrases[-1].source_end_seconds - source_duration_seconds) > 0.05:
        return None
    segments = tuple(
        AudioPacingSegment(
            source_start_seconds=phrase.source_start_seconds,
            source_end_seconds=phrase.source_end_seconds,
            tempo_factor=phrase.tempo_factor,
            label=f"scene-{phrase.scene_index}",
        )
        for phrase in phrases
    )
    return NarrationPacingPlan(
        text=full_text,
        phrases=tuple(phrases),
        segments=segments,
        target_characters_per_second=target_characters_per_second,
    )


def build_paced_scene_timings(
    plan: NarrationPacingPlan,
    paced_duration_seconds: float,
    crossfade_seconds: float,
) -> list[PacedSceneTiming]:
    """Project phrase output positions back to contiguous scene boundaries."""

    if paced_duration_seconds <= 0 or crossfade_seconds < 0:
        raise ValueError("paced duration must be positive and crossfade non-negative")
    phrase_positions: list[tuple[NarrationPhraseSpan, float, float, float, float]] = []
    cursor = 0.0
    for index, phrase in enumerate(plan.phrases):
        source_duration = phrase.source_end_seconds - phrase.source_start_seconds
        output_duration = source_duration / phrase.tempo_factor
        output_start = cursor
        relative_speech_start = (
            phrase.speech_start_seconds - phrase.source_start_seconds
        ) / phrase.tempo_factor
        relative_speech_end = (
            phrase.speech_end_seconds - phrase.source_start_seconds
        ) / phrase.tempo_factor
        phrase_positions.append(
            (
                phrase,
                output_start,
                output_start + output_duration,
                output_start + relative_speech_start,
                output_start + relative_speech_end,
            )
        )
        cursor += output_duration
        if index < len(plan.phrases) - 1:
            cursor -= crossfade_seconds

    expected_duration = cursor
    if expected_duration <= 0:
        raise ValueError("pacing plan produced a non-positive duration")
    scale = paced_duration_seconds / expected_duration
    phrase_positions = [
        (
            phrase,
            round(start * scale, 6),
            round(end * scale, 6),
            round(speech_start * scale, 6),
            round(speech_end * scale, 6),
        )
        for phrase, start, end, speech_start, speech_end in phrase_positions
    ]

    by_scene: dict[int, list[tuple[NarrationPhraseSpan, float, float, float, float]]] = {}
    for position in phrase_positions:
        by_scene.setdefault(position[0].scene_index, []).append(position)
    timings: list[PacedSceneTiming] = []
    for scene_index in range(1, max(by_scene) + 1):
        scene_phrases = by_scene.get(scene_index)
        if not scene_phrases:
            raise ValueError(f"scene {scene_index} has no paced narration phrase")
        scene_start = scene_phrases[0][1]
        if scene_index < max(by_scene):
            next_scene = by_scene[scene_index + 1]
            scene_end = next_scene[0][1]
        else:
            scene_end = paced_duration_seconds
        timings.append(
            PacedSceneTiming(
                start_seconds=round(scene_start, 6),
                end_seconds=round(scene_end, 6),
                speech_start_seconds=round(scene_phrases[0][3], 6),
                speech_end_seconds=round(scene_phrases[-1][4], 6),
            )
        )
    return timings


def build_paced_word_boundaries(
    plan: NarrationPacingPlan,
    raw_word_boundaries: object,
    paced_duration_seconds: float,
    crossfade_seconds: float,
) -> list[dict[str, object]] | None:
    """Map provider word events through the paced continuous waveform."""

    if not isinstance(raw_word_boundaries, list) or paced_duration_seconds <= 0:
        return None
    positions: list[tuple[NarrationPhraseSpan, float, float]] = []
    cursor = 0.0
    for index, phrase in enumerate(plan.phrases):
        output_duration = (
            phrase.source_end_seconds - phrase.source_start_seconds
        ) / phrase.tempo_factor
        positions.append((phrase, cursor, cursor + output_duration))
        cursor += output_duration
        if index < len(plan.phrases) - 1:
            cursor -= crossfade_seconds
    if cursor <= 0:
        return None
    scale = paced_duration_seconds / cursor

    def map_source_time(source_time: float) -> float:
        for index, (phrase, output_start, output_end) in enumerate(positions):
            is_last = index == len(positions) - 1
            if source_time < phrase.source_end_seconds or is_last:
                mapped = output_start + (
                    source_time - phrase.source_start_seconds
                ) / phrase.tempo_factor
                return max(0.0, min(paced_duration_seconds, mapped * scale))
        return paced_duration_seconds

    mapped: list[dict[str, object]] = []
    source_character_cursor = 0
    previous_end = 0.0
    for item in raw_word_boundaries:
        if not isinstance(item, dict):
            return None
        token_text = item.get("text")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        token = _timing_text(token_text) if isinstance(token_text, str) else ""
        if (
            not token
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or end <= start
        ):
            return None
        mapped_start = map_source_time(float(start))
        mapped_end = map_source_time(float(end))
        mapped_start = max(previous_end, mapped_start)
        mapped_end = max(mapped_start + 0.001, mapped_end)
        mapped_end = min(paced_duration_seconds, mapped_end)
        if mapped_end <= mapped_start:
            return None
        mapped.append(
            {
                "start_seconds": round(mapped_start, 6),
                "end_seconds": round(mapped_end, 6),
                "text": token_text.strip() if isinstance(token_text, str) else token,
            }
        )
        previous_end = mapped_end
        source_character_cursor += len(token)
    if source_character_cursor != len(_timing_text(plan.text)):
        return None
    return mapped


def _timing_text(text: str) -> str:
    return "".join(character for character in text if character.isalnum())


class FFmpegAudioPacer:
    """Apply small per-slice tempo corrections and smooth their joins."""

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 120,
        sample_rate: int = 24_000,
        channel_count: int = 1,
        crossfade_ms: int = 70,
        min_tempo_factor: float = 0.82,
        max_tempo_factor: float = 1.08,
        target_characters_per_second: float = 3.8,
    ) -> None:
        if sample_rate <= 0 or channel_count <= 0:
            raise ValueError("sample_rate and channel_count must be positive")
        if crossfade_ms < 0:
            raise ValueError("crossfade_ms must not be negative")
        if not 0 < min_tempo_factor <= 1:
            raise ValueError("min_tempo_factor must be in (0, 1]")
        if max_tempo_factor < 1:
            raise ValueError("max_tempo_factor must be at least 1")
        if target_characters_per_second <= 0:
            raise ValueError("target_characters_per_second must be positive")
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self.crossfade_ms = crossfade_ms
        self.min_tempo_factor = min_tempo_factor
        self.max_tempo_factor = max_tempo_factor
        self.target_characters_per_second = target_characters_per_second

    async def pace(
        self,
        content: bytes,
        content_type: str,
        source_duration_seconds: float,
        segments: list[AudioPacingSegment],
    ) -> AudioPacingResult:
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        self._validate_input(
            content,
            normalized_type,
            source_duration_seconds,
            segments,
        )
        if shutil.which(self.binary) is None:
            raise AudioPacingError(
                "AUDIO_PACING_UNAVAILABLE",
                f"{self.binary} is required to smooth narration pacing",
            )

        effective_crossfade = self._effective_crossfade_seconds(segments)
        changed = any(abs(segment.tempo_factor - 1.0) > 1e-6 for segment in segments)
        changed = changed or (len(segments) > 1 and effective_crossfade > 0)

        with TemporaryDirectory(prefix="ai-audio-pacing-") as directory:
            root = Path(directory)
            input_path = root / f"input{self._suffix_for_mime(normalized_type)}"
            output_path = root / "paced.wav"
            await asyncio.to_thread(input_path.write_bytes, content)
            filter_graph = self._filter_graph(segments, effective_crossfade)
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
                "-map",
                "[out]",
                "-ar",
                str(self.sample_rate),
                "-ac",
                str(self.channel_count),
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
                raise AudioPacingError(
                    "AUDIO_PACING_UNAVAILABLE",
                    f"{self.binary} is required to smooth narration pacing",
                ) from exc

            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise AudioPacingError(
                    "AUDIO_PACING_TIMEOUT",
                    "FFmpeg timed out while smoothing narration pacing",
                ) from exc

            if process.returncode != 0 or not output_path.is_file():
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg could not smooth narration pacing"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise AudioPacingError("AUDIO_PACING_FAILED", message)
            paced_content = await asyncio.to_thread(output_path.read_bytes)

        if not paced_content:
            raise AudioPacingError(
                "AUDIO_PACING_FAILED",
                "FFmpeg produced an empty paced narration Artifact",
            )
        expected_duration = sum(
            (segment.source_end_seconds - segment.source_start_seconds)
            / segment.tempo_factor
            for segment in segments
        ) - effective_crossfade * max(0, len(segments) - 1)
        return AudioPacingResult(
            content=paced_content,
            content_type="audio/wav",
            changed=changed,
            crossfade_seconds=effective_crossfade,
            metadata={
                "operation": "phrase_pacing_crossfade",
                "filter": "atempo_acrossfade",
                "source_duration_seconds": round(source_duration_seconds, 6),
                "expected_duration_seconds": round(max(0.0, expected_duration), 6),
                "crossfade_ms": round(effective_crossfade * 1000, 3),
                "segment_count": len(segments),
                "min_tempo_factor": self.min_tempo_factor,
                "max_tempo_factor": self.max_tempo_factor,
                "sample_rate": self.sample_rate,
                "channel_count": self.channel_count,
                "output_format": "wav",
                "continuous_waveform": True,
                "independent_waveform_count": 1,
            },
        )

    def _validate_input(
        self,
        content: bytes,
        content_type: str,
        source_duration_seconds: float,
        segments: list[AudioPacingSegment],
    ) -> None:
        if not content or not content_type.startswith("audio/"):
            raise AudioPacingError(
                "AUDIO_PACING_INPUT_INVALID",
                "Narration pacing requires non-empty audio content",
            )
        if not math.isfinite(source_duration_seconds) or source_duration_seconds <= 0:
            raise AudioPacingError(
                "AUDIO_PACING_INPUT_INVALID",
                "Narration source duration must be a positive finite value",
            )
        if not segments or len(segments) > 64:
            raise AudioPacingError(
                "AUDIO_PACING_INPUT_INVALID",
                "Narration pacing requires between 1 and 64 segments",
            )
        previous_end = 0.0
        for index, segment in enumerate(segments):
            start = segment.source_start_seconds
            end = segment.source_end_seconds
            tempo = segment.tempo_factor
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or not math.isfinite(tempo)
                or start < -0.02
                or end <= start
                or end > source_duration_seconds + 0.02
                or start < previous_end - 0.02
                or tempo < self.min_tempo_factor - 1e-6
                or tempo > self.max_tempo_factor + 1e-6
            ):
                raise AudioPacingError(
                    "AUDIO_PACING_INPUT_INVALID",
                    f"Narration pacing segment {index + 1} is invalid or out of order",
                )
            if index and abs(start - previous_end) > 0.02:
                raise AudioPacingError(
                    "AUDIO_PACING_INPUT_INVALID",
                    "Narration pacing segments must cover one contiguous source timeline",
                )
            previous_end = end
        if abs(segments[0].source_start_seconds) > 0.02:
            raise AudioPacingError(
                "AUDIO_PACING_INPUT_INVALID",
                "Narration pacing must start at the beginning of the source waveform",
            )
        if abs(previous_end - source_duration_seconds) > 0.05:
            raise AudioPacingError(
                "AUDIO_PACING_INPUT_INVALID",
                "Narration pacing must cover the complete source waveform",
            )

    def _effective_crossfade_seconds(
        self,
        segments: list[AudioPacingSegment],
    ) -> float:
        requested = self.crossfade_ms / 1000
        if requested <= 0 or len(segments) <= 1:
            return 0.0
        shortest_output = min(
            (segment.source_end_seconds - segment.source_start_seconds)
            / segment.tempo_factor
            for segment in segments
        )
        return round(min(requested, shortest_output / 3), 6)

    def _filter_graph(
        self,
        segments: list[AudioPacingSegment],
        crossfade_seconds: float,
    ) -> str:
        split_labels = "".join(f"[src{index}]" for index in range(len(segments)))
        chains = [
            f"[0:a]aresample=async=0:first_pts=0,asplit={len(segments)}{split_labels}"
        ]
        for index, segment in enumerate(segments):
            chains.append(
                f"[src{index}]atrim=start={segment.source_start_seconds:.6f}:"
                f"end={segment.source_end_seconds:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"atempo={segment.tempo_factor:.6f},"
                f"aresample={self.sample_rate}:async=0,"
                f"asetpts=PTS-STARTPTS[s{index}]"
            )
        current = "[s0]"
        for index in range(1, len(segments)):
            output = f"[join{index}]"
            if crossfade_seconds > 0:
                chains.append(
                    f"{current}[s{index}]acrossfade="
                    f"d={crossfade_seconds:.6f}:c1=tri:c2=tri{output}"
                )
            else:
                chains.append(
                    f"{current}[s{index}]concat=n=2:v=0:a=1{output}"
                )
            current = output
        chains.append(
            f"{current}aresample={self.sample_rate}:async=1,"
            "asetpts=N/SR/TB[out]"
        )
        return ";".join(chains)

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
