#!/usr/bin/env python3
"""Small HTTP bridge for a locally installed MuseTalk runtime.

The project does not ship MuseTalk weights or its implementation.  Run this
script on the Windows GPU host after installing a compatible wrapper that
accepts the CLI contract documented in ``docs/05-deployment.md``.  Docker API
and Worker instances can then call the bridge over the LAN/host gateway.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import math
import os
import secrets
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


RUNTIME_PATH = os.getenv("MUSETALK_RUNTIME_PATH", sys.executable)
WRAPPER_PATH = os.getenv("MUSETALK_WRAPPER_PATH", "")
MODEL_ROOT = os.getenv("MUSETALK_MODEL_ROOT", "")
DEVICE = os.getenv("MUSETALK_DEVICE", "cuda")
MODEL_NAME = os.getenv("MUSETALK_MODEL", "MuseTalk-local")
API_KEY = os.getenv("MUSETALK_API_KEY", "")
FFPROBE_BINARY = os.getenv("MUSETALK_FFPROBE_BINARY", "ffprobe")
MAX_UPLOAD_BYTES = _env_int("MUSETALK_MAX_UPLOAD_BYTES", 524_288_000)
MAX_OUTPUT_BYTES = _env_int("MUSETALK_MAX_OUTPUT_BYTES", 524_288_000)
TIMEOUT_SECONDS = _env_int("MUSETALK_TIMEOUT_SECONDS", 900)

app = FastAPI(title="MuseTalk HTTP Bridge", version="0.1.0")
_gpu_lock = asyncio.Lock()


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    runtime_ok = _executable_exists(RUNTIME_PATH)
    wrapper_ok = bool(WRAPPER_PATH) and Path(WRAPPER_PATH).is_file()
    configured = runtime_ok and wrapper_ok
    return {
        "status": "ok" if configured else "degraded",
        "provider": "musetalk_http",
        "model": MODEL_NAME,
        "configured": configured,
        "runtime_path": RUNTIME_PATH,
        "wrapper_path": WRAPPER_PATH,
        "device": DEVICE,
    }


@app.post("/v1/lip-sync")
async def lip_sync(
    video: UploadFile = File(...),
    audio: UploadFile = File(...),
    face_region: str = Form("auto"),
    face_padding: int = Form(0),
    model: str = Form(MODEL_NAME),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    _check_auth(authorization, x_api_key)
    if face_region not in {"auto", "full_frame"}:
        raise HTTPException(status_code=400, detail="face_region must be auto or full_frame")
    if face_padding < 0 or face_padding > 100:
        raise HTTPException(status_code=400, detail="face_padding must be between 0 and 100")
    _validate_configuration()

    video_bytes = await _read_limited(video)
    audio_bytes = await _read_limited(audio)
    duration_ms: int | None = None
    async with _gpu_lock:
        with TemporaryDirectory(prefix="ai-video-musetalk-http-") as directory:
            root = Path(directory)
            video_path = root / _input_name(video.filename, video.content_type, "input.mp4")
            audio_path = root / _input_name(audio.filename, audio.content_type, "input.wav")
            output_path = root / "lip-synced.mp4"
            video_path.write_bytes(video_bytes)
            audio_path.write_bytes(audio_bytes)
            command = [
                RUNTIME_PATH,
                WRAPPER_PATH,
                "--video",
                str(video_path),
                "--audio",
                str(audio_path),
                "--output",
                str(output_path),
                "--face-region",
                face_region,
                "--face-padding",
                str(face_padding),
                "--device",
                DEVICE,
            ]
            if MODEL_ROOT:
                command.extend(["--model-root", MODEL_ROOT])
            stdout, stderr = await _run(command)
            if not output_path.is_file() or output_path.stat().st_size == 0:
                detail = stderr.strip() or stdout.strip() or "wrapper did not create an output file"
                raise HTTPException(status_code=502, detail=detail[:500])
            if output_path.stat().st_size > MAX_OUTPUT_BYTES:
                raise HTTPException(status_code=413, detail="MuseTalk output exceeds the configured limit")
            output_bytes = await asyncio.to_thread(output_path.read_bytes)
            duration_ms = await _probe_duration_ms(output_path)

    payload: dict[str, object] = {
        "video_base64": base64.b64encode(output_bytes).decode("ascii"),
        "mime_type": "video/mp4",
        "provider": "musetalk_http",
        "model": model or MODEL_NAME,
        "metadata": {
            "inference": "musetalk_http_bridge",
            "device": DEVICE,
            "wrapper_path": WRAPPER_PATH,
            "stdout_present": bool(stdout),
            "duration_source": "ffprobe" if duration_ms is not None else "application_probe_unavailable",
        },
    }
    if duration_ms is not None:
        payload["duration_seconds"] = max(1, math.ceil(duration_ms / 1000))
        payload["duration_ms"] = duration_ms
    return JSONResponse(payload)


async def _read_limited(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="MuseTalk input exceeds the configured limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_configuration() -> None:
    if not _executable_exists(RUNTIME_PATH):
        raise HTTPException(status_code=503, detail="MuseTalk runtime is not configured")
    if not WRAPPER_PATH or not Path(WRAPPER_PATH).is_file():
        raise HTTPException(status_code=503, detail="MuseTalk wrapper is not configured")
    if MODEL_ROOT and not Path(MODEL_ROOT).is_dir():
        raise HTTPException(status_code=503, detail="MuseTalk model root was not found")


def _check_auth(authorization: str | None, x_api_key: str | None) -> None:
    if not API_KEY:
        return
    presented = x_api_key or ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not presented or not hmac.compare_digest(presented, API_KEY):
        raise HTTPException(status_code=401, detail="MuseTalk bridge authentication failed")


async def _run(command: list[str]) -> tuple[str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="MuseTalk runtime could not be started") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise HTTPException(status_code=504, detail="MuseTalk inference timed out") from exc
    stdout_text = stdout.decode("utf-8", errors="replace")[-2000:]
    stderr_text = stderr.decode("utf-8", errors="replace")[-2000:]
    if process.returncode != 0:
        raise HTTPException(status_code=502, detail=(stderr_text or stdout_text or "MuseTalk wrapper failed")[-500:])
    return stdout_text, stderr_text


async def _probe_duration_ms(path: Path) -> int | None:
    """Return output video duration; never use inference wall time as duration."""

    if not _executable_exists(FFPROBE_BINARY):
        return None
    command = [
        FFPROBE_BINARY,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except (FileNotFoundError, TimeoutError, OSError):
        return None
    if process.returncode != 0:
        return None
    try:
        duration = float(stdout.decode("utf-8", errors="replace").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return max(1, round(duration * 1000))


def _input_name(filename: str | None, content_type: str | None, fallback: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".wav", ".mp3", ".ogg"}:
        suffix = {
            "video/webm": ".webm",
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
        }.get((content_type or "").split(";", 1)[0].lower(), Path(fallback).suffix)
    return Path(fallback).stem + suffix


def _executable_exists(path: str) -> bool:
    candidate = Path(path)
    if candidate.is_absolute() or any(separator in path for separator in ("/", "\\")):
        return candidate.is_file()
    return secrets.compare_digest(path, sys.executable) or bool(next(
        (item for item in os.getenv("PATH", "").split(os.pathsep) if Path(item, path).is_file()),
        "",
    ))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "scripts.musetalk_http_runner:app",
        host=os.getenv("MUSETALK_HOST", "0.0.0.0"),
        port=_env_int("MUSETALK_PORT", 8090),
        reload=False,
    )
