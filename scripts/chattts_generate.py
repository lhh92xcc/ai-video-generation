#!/usr/bin/env python3
"""Generate one WAV with ChatTTS in its dedicated runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--voice", default="default")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--speed-token", default="[speed_1]")
    parser.add_argument(
        "--max-new-token",
        type=int,
        default=3600,
        help="Maximum audio-code tokens for one continuous synthesis",
    )
    parser.add_argument(
        "--chunk-max-characters",
        type=int,
        default=120,
        help=(
            "Legacy compatibility value; continuous synthesis no longer splits "
            "the input into independent waveforms"
        ),
    )
    parser.add_argument(
        "--crossfade-ms",
        type=int,
        default=120,
        help="Legacy compatibility value; no waveform crossfade is performed",
    )
    # Kept as a hidden compatibility flag for older local commands. The
    # continuous runner deliberately ignores phrase boundaries for synthesis.
    parser.add_argument("--segments-json", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metadata-output", type=Path)
    return parser.parse_args()


def _synthesize_continuous_waveform(
    chat: Any,
    text: str,
    infer_params: Any,
    numpy_module: Any,
    refine_params: Any | None = None,
) -> Any:
    """Synthesize one continuous waveform for the complete narration text."""

    normalized_text = text.strip()
    if not normalized_text:
        raise SystemExit("ChatTTS text must not be empty")
    infer_kwargs: dict[str, Any] = {
        "params_infer_code": infer_params,
        # Let ChatTTS refine punctuation and prosody once for the whole text.
        # Splitting into independent phrase waveforms causes audible resets.
        "skip_refine_text": False,
        "split_text": False,
    }
    if refine_params is not None:
        infer_kwargs["params_refine_text"] = refine_params
    wavs = chat.infer([normalized_text], **infer_kwargs)
    if not wavs or len(wavs) != 1:
        raise SystemExit("ChatTTS must return exactly one continuous waveform")
    values = numpy_module.asarray(wavs[0], dtype=numpy_module.float32).reshape(-1)
    if values.size == 0:
        raise SystemExit("ChatTTS returned an empty waveform")
    return values


def _split_narration_chunks(text: str, max_characters: int) -> list[str]:
    """Split long narration only at natural sentence boundaries.

    ChatTTS has a finite audio-code context. Keeping several complete
    sentences in one inference preserves prosody across most of the episode;
    the renderer then crossfades the small number of context-safe chunks.
    """

    normalized_text = re.sub(r"\s+", "", text.strip())
    if not normalized_text:
        raise SystemExit("ChatTTS text must not be empty")
    if max_characters < 32:
        raise SystemExit("--chunk-max-characters must be at least 32")
    if len(normalized_text) <= max_characters:
        return [normalized_text]

    sentences = [
        item.strip()
        for item in re.findall(r".*?(?:[。！？!?；;]|$)", normalized_text)
        if item.strip()
    ]
    if not sentences:
        return [normalized_text]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_characters:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(sentence), max_characters):
                chunks.append(sentence[start : start + max_characters])
            continue
        if current and len(current) + len(sentence) > max_characters:
            chunks.append(current)
            current = ""
        current += sentence
    if current:
        chunks.append(current)
    return chunks


def _crossfade_waveforms(
    waveforms: list[Any],
    crossfade_samples: int,
    numpy_module: Any,
) -> Any:
    """Join context-safe waveforms with a short equal-power crossfade."""

    if not waveforms:
        raise SystemExit("ChatTTS produced no waveforms")
    result = numpy_module.asarray(waveforms[0], dtype=numpy_module.float32).reshape(-1)
    for next_waveform in waveforms[1:]:
        following = numpy_module.asarray(next_waveform, dtype=numpy_module.float32).reshape(-1)
        overlap = min(max(0, crossfade_samples), result.size, following.size)
        if overlap <= 0:
            result = numpy_module.concatenate((result, following))
            continue
        phase = numpy_module.linspace(0.0, numpy_module.pi / 2.0, overlap, endpoint=False)
        fade_out = numpy_module.cos(phase)
        fade_in = numpy_module.sin(phase)
        blended = result[-overlap:] * fade_out + following[:overlap] * fade_in
        result = numpy_module.concatenate((result[:-overlap], blended, following[overlap:]))
    return result


def _synthesize_long_text(
    chat: Any,
    text: str,
    infer_params: Any,
    numpy_module: Any,
    *,
    chunk_max_characters: int,
    crossfade_samples: int,
    refine_params: Any | None = None,
) -> tuple[Any, list[str]]:
    """Synthesize the complete narration as one waveform.

    ChatTTS can concatenate returned arrays, but every independent inference
    restarts the acoustic state. That restart is audible as a new onset,
    prosody change, or speed jump even when the arrays are crossfaded. The
    default runner therefore refuses to manufacture a long narration from
    separately synthesized chunks. The old splitter and crossfade helpers are
    retained only for tests and compatibility with historical artifacts.
    """

    del chunk_max_characters, crossfade_samples
    normalized_text = text.strip()
    waveform = _synthesize_continuous_waveform(
        chat,
        normalized_text,
        infer_params,
        numpy_module,
        refine_params,
    )
    return waveform, [normalized_text]


def main() -> None:
    args = parse_args()
    if args.max_new_token < 256:
        raise SystemExit("--max-new-token must be at least 256")
    if args.crossfade_ms < 0:
        raise SystemExit("--crossfade-ms must not be negative")
    try:
        import numpy as np
        import torch
        import ChatTTS
    except ImportError as exc:
        raise SystemExit(f"ChatTTS runtime is incomplete: {exc}") from exc

    seed = int(hashlib.sha256(args.voice.encode("utf-8")).hexdigest()[:8], 16)
    torch.manual_seed(seed)
    np.random.seed(seed % (2**32))
    if torch.cuda.is_available():
        device = "cuda"
    elif args.device == "mps" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    project_root = Path(__file__).resolve().parents[1]
    chattts_root = Path(__file__).resolve().parents[1] / "local-runtimes" / "chattts"
    asset_root = Path(
        os.getenv(
            "CHAT_TTS_ASSET_PATH",
            str(chattts_root),
        )
    )
    asset_root.mkdir(parents=True, exist_ok=True)
    chat = ChatTTS.Chat()
    chat.load(
        source="local",
        custom_path=str(asset_root),
        compile=False,
        device=device,
    )
    speaker = chat.sample_random_speaker()
    infer_params = ChatTTS.Chat.InferCodeParams(
        prompt=args.speed_token,
        spk_emb=speaker,
        # Lower randomness keeps local portfolio narration from changing its
        # local speaking rate too aggressively between adjacent sentences.
        temperature=0.15,
        top_P=0.65,
        top_K=15,
        max_new_token=args.max_new_token,
        manual_seed=seed,
    )
    refine_params = ChatTTS.Chat.RefineTextParams(
        temperature=0.15,
        top_P=0.65,
        top_K=15,
        max_new_token=min(1024, max(384, len(args.text.strip()) * 2)),
        manual_seed=seed,
    )
    sample_rate = 24000
    waveform, chunks = _synthesize_long_text(
        chat,
        args.text,
        infer_params,
        np,
        chunk_max_characters=args.chunk_max_characters,
        crossfade_samples=round(sample_rate * args.crossfade_ms / 1000),
        refine_params=refine_params,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        import soundfile as sf

        sf.write(str(args.output), waveform, sample_rate, subtype="PCM_16")
    except ImportError:
        import scipy.io.wavfile

        scipy.io.wavfile.write(str(args.output), sample_rate, np.clip(waveform, -1, 1))

    if args.metadata_output is not None:
        args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
        args.metadata_output.write_text(
            json.dumps(
                {
                    "segmentation": "continuous_text",
                    "synthesis_mode": "single_waveform",
                    "pause_policy": "provider_punctuation",
                    "speed_token": args.speed_token,
                    "sampling_policy": "deterministic_low_variance",
                    "refine_text": True,
                    "max_new_token": args.max_new_token,
                    "chunk_max_characters": args.chunk_max_characters,
                    "crossfade_ms": args.crossfade_ms,
                    "chunk_count": 1,
                    "waveform_join_strategy": "none",
                    "chunk_characters": [len(chunks[0])],
                    "independent_waveform_count": 1,
                    "sample_rate": sample_rate,
                    "text_characters": len(args.text.strip()),
                    "duration_seconds": round(int(waveform.size) / sample_rate, 6),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
