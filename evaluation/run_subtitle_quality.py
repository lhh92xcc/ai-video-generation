"""Run deterministic subtitle quality checks against an SRT Artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.media.subtitle_quality import evaluate_subtitle_cues
from app.rendering.subtitles import parse_srt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srt-path", type=Path, required=True)
    parser.add_argument("--audio-duration-seconds", type=float, required=True)
    parser.add_argument("--reference-text-path", type=Path)
    parser.add_argument("--alignment-precision", default="unknown")
    parser.add_argument("--max-character-error-rate", type=float, default=0.05)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    reference_text = None
    if args.reference_text_path:
        reference_text = args.reference_text_path.read_text(encoding="utf-8")
    report = evaluate_subtitle_cues(
        parse_srt(args.srt_path.read_bytes()),
        audio_duration_seconds=args.audio_duration_seconds,
        reference_text=reference_text,
        alignment_precision=args.alignment_precision,
        max_character_error_rate=args.max_character_error_rate,
    ).as_dict()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
