"""ChatTTS adapter executed in its own local Python runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from app.domain.models import TTSGenerationRequest, TTSGenerationResult
from app.providers.errors_audio import TTSProviderError


class ChatTTSProvider:
    def __init__(
        self,
        runtime_path: str = "local-runtimes/chattts/.venv/bin/python",
        script_path: str = "scripts/chattts_generate.py",
        device: str = "mps",
        timeout_seconds: int = 900,
        segment_max_characters: int = 22,
        speed_token: str = "[speed_1]",
        max_new_token: int = 3600,
        chunk_max_characters: int = 120,
        crossfade_ms: int = 120,
    ) -> None:
        self.runtime_path = Path(runtime_path)
        self.script_path = Path(script_path)
        self.device = device
        self.timeout_seconds = max(1, timeout_seconds)
        # Kept in the constructor for configuration compatibility. Continuous
        # synthesis intentionally does not split a request into independent
        # waveforms; the old values are only retained in artifact metadata.
        self.segment_max_characters = max(4, segment_max_characters)
        self.speed_token = speed_token.strip() or "[speed_1]"
        if max_new_token < 256:
            raise ValueError("max_new_token must be at least 256")
        self.max_new_token = max_new_token
        if chunk_max_characters < 32:
            raise ValueError("chunk_max_characters must be at least 32")
        if crossfade_ms < 0:
            raise ValueError("crossfade_ms must not be negative")
        self.chunk_max_characters = chunk_max_characters
        self.crossfade_ms = crossfade_ms

    async def generate_speech(self, request: TTSGenerationRequest) -> TTSGenerationResult:
        if not self.runtime_path.is_file() or not os.access(self.runtime_path, os.X_OK):
            raise TTSProviderError(
                "TTS_PROVIDER_UNAVAILABLE",
                f"ChatTTS runtime was not found: {self.runtime_path}; run scripts/setup-chattts-mac.sh",
            )
        if not self.script_path.is_file():
            raise TTSProviderError(
                "TTS_PROVIDER_NOT_CONFIGURED",
                f"ChatTTS runner was not found: {self.script_path}",
            )

        started = monotonic()
        with TemporaryDirectory(prefix="ai-video-chattts-") as directory:
            output_path = Path(directory) / "speech.wav"
            metadata_path = Path(directory) / "speech.json"
            command = [
                str(self.runtime_path),
                str(self.script_path),
                "--text",
                request.text,
                "--voice",
                request.voice,
                "--device",
                self.device,
                "--speed-token",
                self.speed_token,
                "--max-new-token",
                str(self.max_new_token),
                "--chunk-max-characters",
                str(self.chunk_max_characters),
                "--crossfade-ms",
                str(self.crossfade_ms),
                "--output",
                str(output_path),
                "--metadata-output",
                str(metadata_path),
            ]
            environment = dict(os.environ)
            environment["PYTHONUNBUFFERED"] = "1"
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except FileNotFoundError as exc:
                raise TTSProviderError("TTS_PROVIDER_UNAVAILABLE", "ChatTTS runtime could not be started") from exc
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise TTSProviderError("TTS_PROVIDER_TIMEOUT", "ChatTTS generation timed out") from exc

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip() or stdout.decode(
                    "utf-8", errors="replace"
                ).strip()
                raise TTSProviderError(
                    "TTS_PROVIDER_FAILED",
                    f"ChatTTS generation failed: {detail[:500] or 'unknown error'}",
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise TTSProviderError("TTS_PROVIDER_INVALID_RESPONSE", "ChatTTS did not produce a WAV file")
            content = await asyncio.to_thread(output_path.read_bytes)
            pacing_metadata = self._read_pacing_metadata(metadata_path)

        return TTSGenerationResult(
            audio_base64=base64.b64encode(content).decode("ascii"),
            mime_type="audio/wav",
            provider="chattts",
            model="ChatTTS-local",
            duration_seconds=0,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "runtime_path": str(self.runtime_path),
                "script_path": str(self.script_path),
                "device": self.device,
                "voice_profile": request.voice,
                "speed_token": self.speed_token,
                "max_new_token": self.max_new_token,
                "chunk_max_characters": self.chunk_max_characters,
                "crossfade_ms": self.crossfade_ms,
                "segment_max_characters": self.segment_max_characters,
                **pacing_metadata,
                "local": True,
            },
        )

    @staticmethod
    def _read_pacing_metadata(path: Path) -> dict[str, object]:
        """Read continuous-synthesis metadata without trusting phrase guesses."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {
                "segmentation": "provider_default",
                "synthesis_mode": "provider_default",
            }
        if not isinstance(payload, dict):
            return {
                "segmentation": "provider_default",
                "synthesis_mode": "provider_default",
            }
        segments = payload.get("segments")
        metadata: dict[str, object] = {
            "segmentation": str(payload.get("segmentation", "continuous_text")),
            "synthesis_mode": str(payload.get("synthesis_mode", "continuous_text")),
            "pause_policy": payload.get("pause_policy", "provider_punctuation"),
        }
        for key in (
            "max_new_token",
            "chunk_max_characters",
            "crossfade_ms",
            "chunk_count",
            "waveform_join_strategy",
            "chunk_characters",
            "independent_waveform_count",
            "sampling_policy",
            "refine_text",
        ):
            if key in payload:
                metadata[key] = payload[key]
        # A single scene-level timing is safe to consume. Multiple phrase
        # timings may come from an older custom runner and are intentionally
        # ignored because they describe stitched waveforms.
        if isinstance(segments, list) and len(segments) == 1:
            metadata["segments"] = segments
        return metadata
