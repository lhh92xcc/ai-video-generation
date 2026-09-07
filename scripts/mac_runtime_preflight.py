#!/usr/bin/env python3
"""Run a read-only preflight for the local Mac media host.

The check is deliberately separate from setup and generation.  It never
downloads models, starts services, edits configuration, or prints secrets.
It is intended to answer one question before a real portfolio run:
"Is this Mac ready to execute the selected local profile?"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.windows_runtime_preflight import (  # noqa: E402
    IDENTITY_NODES,
    IMAGE_NODES,
    VIDEO_NODES,
    _workflow_classes,
    _load_workflow,
)


LOCAL_COMFYUI_PROVIDERS = frozenset({"comfyui", "comfyui_wan_i2v"})
LOCAL_LLM_PROVIDERS = frozenset({"ollama"})
LOCAL_MUSETALK_PROVIDERS = frozenset({"musetalk", "musetalk_http"})


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One visible preflight result."""

    name: str
    ok: bool
    message: str
    required: bool = True

    @property
    def status(self) -> str:
        if self.ok:
            return "PASS"
        return "FAIL" if self.required else "WARN"


def _request_json(url: str, timeout: float = 5.0) -> tuple[dict[str, Any] | None, str | None]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (OSError, URLError, TimeoutError) as exc:
        return None, str(exc)[:160]
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        return None, f"响应不是有效 JSON：{exc}"
    if not isinstance(payload, dict):
        return None, "响应不是 JSON object"
    return payload, None


def _run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, f"命令不存在：{command[0]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:160]

    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return completed.returncode == 0, output[:20000]


def _resolve_path(project_root: Path, value: str | None, default: Path) -> Path:
    if not value or not value.strip():
        return default
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else project_root / candidate


def _strip_v1_suffix(url: str) -> str:
    normalized = url.rstrip("/")
    return normalized[:-3] if normalized.endswith("/v1") else normalized


def _config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


def resolve_config_path(project_root: Path, explicit: str | None = None) -> Path:
    """Resolve config with the same precedence used by the local runner."""

    configured = explicit or os.getenv("AI_VIDEO_CONFIG")
    if configured:
        return _resolve_path(project_root, configured, project_root / "config/config.example.toml")

    profile = os.getenv("AI_VIDEO_PROFILE", "")
    local_path = project_root / "config/config.local.toml"
    if profile == "local_mac_16gb" and local_path.is_file():
        return local_path
    return project_root / "config/config.example.toml"


class MacRuntimePreflight:
    """Read-only checks for a local Mac production profile."""

    def __init__(
        self,
        *,
        project_root: Path,
        config_path: Path | None = None,
        comfyui_root: Path | None = None,
        comfyui_url: str | None = None,
        ollama_url: str | None = None,
        validate_models: bool = False,
        require_compose: bool = False,
        require_mps: bool = False,
        min_free_gb: float = 10.0,
    ) -> None:
        self.project_root = project_root
        selected_config = config_path or resolve_config_path(project_root)
        self.config_path = (
            selected_config
            if selected_config.is_absolute()
            else project_root / selected_config
        )
        self.comfyui_root = comfyui_root or _resolve_path(
            project_root,
            os.getenv("COMFYUI_HOME"),
            Path.home() / "ComfyUI",
        )
        self.comfyui_url_override = comfyui_url
        self.ollama_url_override = ollama_url
        self.validate_models = validate_models
        self.require_compose = require_compose
        self.require_mps = require_mps
        self.min_free_gb = min_free_gb
        self.config: dict[str, Any] = {}
        self.results: list[CheckResult] = []
        self._renderer_uses_docker_fallback = False

    def add(self, name: str, ok: bool, message: str, *, required: bool = True) -> None:
        result = CheckResult(name=name, ok=ok, message=message, required=required)
        self.results.append(result)
        print(f"[{result.status}] {name}: {message}")

    def _value(self, section: str, key: str, env_name: str, default: str = "") -> str:
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()
        raw = _config_section(self.config, section).get(key, default)
        if raw is None or not str(raw).strip():
            return default
        return str(raw)

    @property
    def image_provider(self) -> str:
        return self._value("image_generation", "provider", "AI_VIDEO_IMAGE_PROVIDER", "mock").lower()

    @property
    def video_provider(self) -> str:
        return self._value("video_generation", "provider", "AI_VIDEO_VIDEO_PROVIDER", "mock").lower()

    @property
    def llm_provider(self) -> str:
        return self._value("llm", "provider", "AI_VIDEO_LLM_PROVIDER", "mock").lower()

    @property
    def lip_sync_provider(self) -> str:
        return self._value("lip_sync", "provider", "AI_VIDEO_LIP_SYNC_PROVIDER", "mock").lower()

    def run(self) -> int:
        print("AI Video Generation / Mac 本地运行前置检查")
        print(f"项目目录: {self.project_root}")
        print(f"配置文件: {self.config_path}")
        print(f"ComfyUI 目录: {self.comfyui_root}")
        print("")

        self._load_config()
        if self.config:
            self._check_project_commands()
            self._check_project_workflows()
            self._check_ollama()
            self._check_comfyui_http()
            self._check_mps()
            self._check_comfyui_assets()
            self._check_musetalk()
            self._check_ffmpeg()
            self._check_compose()
            self._check_disk()

        failures = [result.name for result in self.results if not result.ok and result.required]
        warnings = [result.name for result in self.results if not result.ok and not result.required]
        print("")
        if failures:
            print("Mac 本地前置检查失败：" + ", ".join(failures))
            if warnings:
                print("同时存在可选警告：" + ", ".join(warnings))
            return 1
        if warnings:
            print("Mac 本地前置检查通过，但存在警告：" + ", ".join(warnings))
        else:
            print("Mac 本地前置检查通过。")
        return 0

    def _load_config(self) -> None:
        if not self.config_path.is_file():
            self.add("配置文件", False, f"文件不存在：{self.config_path}")
            return
        try:
            self.config = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            self.add("配置文件", False, f"无法读取 TOML：{exc}")
            return
        self.add("配置文件", True, "TOML 可读取")

    def _check_project_commands(self) -> None:
        for name, command, required in (
            ("uv", "uv", True),
            ("Docker CLI", "docker", self.require_compose or self._renderer_fallback_configured()),
        ):
            path = shutil.which(command)
            self.add(name, path is not None, path or f"找不到 {command}", required=required)

    def _renderer_fallback_configured(self) -> bool:
        binary = self._value("video_generation", "binary", "AI_VIDEO_VIDEO_BINARY", "ffmpeg")
        return "ffmpeg-with-libass.sh" in binary

    def _check_project_workflows(self) -> None:
        if self.image_provider not in LOCAL_COMFYUI_PROVIDERS and self.video_provider not in LOCAL_COMFYUI_PROVIDERS:
            self.add("ComfyUI workflow", True, "当前配置未启用本地 ComfyUI", required=False)
            return

        image_section = _config_section(self.config, "image_generation")
        video_section = _config_section(self.config, "video_generation")
        image_path = _resolve_path(
            self.project_root,
            os.getenv("AI_VIDEO_IMAGE_WORKFLOW_PATH") or str(image_section.get("workflow_path", "")),
            self.project_root / "config/comfyui/flux-schnell-t2i-api.json",
        )
        identity_path = _resolve_path(
            self.project_root,
            os.getenv("AI_VIDEO_IMAGE_IDENTITY_WORKFLOW_PATH") or str(image_section.get("identity_workflow_path", "")),
            self.project_root / "config/comfyui/flux-schnell-faceid-reference-api.json",
        )
        video_path = _resolve_path(
            self.project_root,
            os.getenv("AI_VIDEO_VIDEO_WORKFLOW_PATH") or str(video_section.get("workflow_path", "")),
            self.project_root / "config/comfyui/wan2.1-i2v-api.json",
        )
        self._check_workflow(
            "Flux 参考图 workflow",
            image_path,
            required_nodes=IMAGE_NODES,
            required_placeholders=("__AI_VIDEO_PROMPT__", "__AI_VIDEO_CHECKPOINT__"),
        )
        self._check_workflow(
            "Flux 身份 workflow",
            identity_path,
            required_nodes=IMAGE_NODES + IDENTITY_NODES,
            required_placeholders=(
                "__AI_VIDEO_PROMPT__",
                "__AI_VIDEO_CHECKPOINT__",
                "__AI_VIDEO_IDENTITY_IMAGE__",
            ),
        )
        self._check_workflow(
            "Wan I2V workflow",
            video_path,
            required_nodes=VIDEO_NODES,
            required_placeholders=("__AI_VIDEO_PROMPT__", "__AI_VIDEO_INPUT_IMAGE__"),
        )

    def _check_workflow(
        self,
        name: str,
        path: Path,
        *,
        required_nodes: tuple[str, ...],
        required_placeholders: tuple[str, ...],
    ) -> None:
        workflow, error = _load_workflow(path)
        if workflow is None:
            self.add(name, False, error or "workflow 无法读取")
            return
        serialized = json.dumps(workflow, ensure_ascii=False)
        classes = _workflow_classes(workflow)
        missing_nodes = [node for node in required_nodes if node not in classes]
        missing_placeholders = [marker for marker in required_placeholders if marker not in serialized]
        if missing_nodes or missing_placeholders:
            details: list[str] = []
            if missing_nodes:
                details.append("缺少节点 " + ", ".join(missing_nodes))
            if missing_placeholders:
                details.append("缺少占位符 " + ", ".join(missing_placeholders))
            self.add(name, False, "；".join(details))
            return
        self.add(name, True, f"JSON、节点和占位符正常（{path.name}）")

    def _check_ollama(self) -> None:
        required = self.llm_provider in LOCAL_LLM_PROVIDERS
        if not required:
            self.add("Ollama", True, f"当前配置为 {self.llm_provider or 'mock'}，不需要本地 Ollama", required=False)
            return
        base_url = self.ollama_url_override or self._value(
            "llm", "base_url", "AI_VIDEO_LLM_BASE_URL", "http://127.0.0.1:11434/v1"
        )
        url = _strip_v1_suffix(base_url)
        tags, error = _request_json(f"{url}/api/tags")
        if tags is None:
            self.add("Ollama", False, error or "无响应", required=required)
            return
        self.add("Ollama API", True, "服务响应正常", required=required)
        model = self._value("llm", "model", "AI_VIDEO_LLM_MODEL", "qwen2.5:7b")
        names = {
            str(item.get("name") or item.get("model"))
            for item in tags.get("models", [])
            if isinstance(item, dict)
        }
        if model in names:
            self.add("Ollama 模型", True, f"已找到 {model}", required=required)
        else:
            visible = sorted(name for name in names if name != "None")
            self.add(
                "Ollama 模型",
                False,
                f"未找到 {model}，当前模型：{', '.join(visible) or '无'}",
                required=required,
            )

    def _check_comfyui_http(self) -> None:
        required = self.image_provider in LOCAL_COMFYUI_PROVIDERS or self.video_provider in LOCAL_COMFYUI_PROVIDERS
        if not required:
            self.add("ComfyUI", True, "当前配置未启用本地 ComfyUI", required=False)
            return
        base_url = self.comfyui_url_override or self._value(
            "image_generation", "base_url", "AI_VIDEO_COMFYUI_BASE_URL", "http://127.0.0.1:8188"
        )
        system, error = _request_json(f"{base_url.rstrip('/')}/system_stats")
        if system is None:
            self.add("ComfyUI /system_stats", False, error or "无响应", required=required)
            return
        self.add("ComfyUI /system_stats", True, "服务响应正常", required=required)
        node_info, error = _request_json(f"{base_url.rstrip('/')}/object_info", timeout=10.0)
        if node_info is None:
            self.add("ComfyUI 节点清单", False, error or "无法读取 /object_info", required=required)
            return
        expected = set(IMAGE_NODES + IDENTITY_NODES + VIDEO_NODES)
        missing = sorted(expected - set(node_info))
        self.add(
            "ComfyUI 必需节点",
            not missing,
            "已注册全部工作流节点" if not missing else "未注册：" + ", ".join(missing),
            required=required,
        )

    def _check_mps(self) -> None:
        python_value = os.getenv("COMFYUI_PYTHON", "")
        python_path = _resolve_path(
            self.project_root,
            python_value,
            self.comfyui_root / ".venv/bin/python",
        )
        if not python_path.is_file():
            self.add("Apple MPS", False, f"找不到 ComfyUI Python：{python_path}", required=self.require_mps)
            return
        ok, output = _run_command(
            [
                str(python_path),
                "-c",
                "import torch; print('built=' + str(torch.backends.mps.is_built())); print('available=' + str(torch.backends.mps.is_available()))",
            ],
            timeout=30.0,
        )
        if not ok:
            self.add("Apple MPS", False, output or "无法读取 PyTorch MPS 状态", required=self.require_mps)
            return
        available = "available=True" in output and "built=True" in output
        self.add(
            "Apple MPS",
            available,
            "PyTorch MPS 可用" if available else output.replace("\n", "; "),
            required=self.require_mps,
        )

    def _check_comfyui_assets(self) -> None:
        if self.image_provider not in LOCAL_COMFYUI_PROVIDERS and self.video_provider not in LOCAL_COMFYUI_PROVIDERS:
            self.add("ComfyUI 宿主机资源", True, "当前配置未启用本地 ComfyUI", required=False)
            return
        if not self.validate_models:
            self.add("ComfyUI 宿主机资源", True, "未启用模型文件深度检查", required=False)
            return
        if not self.comfyui_root.is_dir():
            self.add("ComfyUI 模型目录", False, f"目录不存在：{self.comfyui_root}")
            return

        image_model = self._value(
            "image_generation", "model", "AI_VIDEO_IMAGE_MODEL", "flux1-schnell-Q4_K_S.gguf"
        )
        video_model = self._value(
            "video_generation", "model", "AI_VIDEO_VIDEO_MODEL", "wan2.1-i2v-14b-480p-Q4_K_S.gguf"
        )
        required_files = (
            ("Flux 模型", self.comfyui_root / "models/unet" / image_model),
            ("Wan 模型", self.comfyui_root / "models/diffusion_models" / video_model),
            ("Flux T5", self.comfyui_root / "models/clip/t5-v1_1-xxl-encoder-Q4_K_S.gguf"),
            ("Flux CLIP-L", self.comfyui_root / "models/clip/clip_l.safetensors"),
            ("Flux VAE", self.comfyui_root / "models/vae/flux1_vae.safetensors"),
            ("PuLID 权重", self.comfyui_root / "models/pulid/pulid_flux_v0.9.1.safetensors"),
            (
                "Wan T5",
                self.comfyui_root / "models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors",
            ),
            ("Wan VAE", self.comfyui_root / "models/vae/Wan2_1_VAE_bf16.safetensors"),
        )
        for label, path in required_files:
            self.add(label, path.is_file() and path.stat().st_size > 0, str(path))

        node_root = self.comfyui_root / "custom_nodes"
        for directory in (
            "ComfyUI-GGUF",
            "ComfyUI-WanVideoWrapper",
            "ComfyUI-VideoHelperSuite",
            "ComfyUI-PuLID-Flux-Enhanced",
        ):
            path = node_root / directory
            self.add(f"ComfyUI 节点目录 · {directory}", path.is_dir(), str(path))

        insightface_root = self.comfyui_root / "models/insightface/models/antelopev2"
        insightface_files = (
            "1k3d68.onnx",
            "2d106det.onnx",
            "genderage.onnx",
            "glintr100.onnx",
            "scrfd_10g_bnkps.onnx",
        )
        missing = [name for name in insightface_files if not (insightface_root / name).is_file()]
        self.add(
            "InsightFace antelopev2",
            not missing,
            "文件齐全" if not missing else "缺少：" + ", ".join(missing),
        )

    def _check_musetalk(self) -> None:
        required = self.lip_sync_provider in LOCAL_MUSETALK_PROVIDERS
        if not required:
            self.add("MuseTalk", True, f"当前配置为 {self.lip_sync_provider or 'mock'}，不需要真实 bridge", required=False)
            return
        base_url = self._value("lip_sync", "base_url", "AI_VIDEO_LIP_SYNC_BASE_URL", "http://127.0.0.1:8090")
        health_path = self._value("lip_sync", "health_path", "AI_VIDEO_LIP_SYNC_HEALTH_PATH", "/healthz")
        payload, error = _request_json(f"{base_url.rstrip('/')}/{health_path.lstrip('/')}")
        if payload is None:
            self.add("MuseTalk bridge", False, error or "无响应")
            return
        configured = payload.get("configured") is True
        status = str(payload.get("status", "unknown"))
        self.add(
            "MuseTalk runtime",
            configured and status == "ok",
            "bridge、wrapper 和模型已配置" if configured and status == "ok" else "bridge 在线但 runtime 未就绪",
        )

    def _check_ffmpeg(self) -> None:
        configured_binary = self._value("video_generation", "binary", "AI_VIDEO_VIDEO_BINARY", "ffmpeg")
        binary_path = _resolve_path(self.project_root, configured_binary, Path(configured_binary))
        ffmpeg = str(binary_path) if binary_path.is_file() else shutil.which(configured_binary)
        ffprobe = shutil.which("ffprobe")
        self.add("FFmpeg", ffmpeg is not None, ffmpeg or "找不到 ffmpeg")
        self.add("FFprobe", ffprobe is not None, ffprobe or "找不到 ffprobe")
        if ffmpeg is None:
            return
        ok, output = _run_command([ffmpeg, "-hide_banner", "-filters"], timeout=20.0)
        if not ok:
            self.add("FFmpeg filter 清单", False, output or "无法读取 filter 清单")
            return
        has_subtitles = "subtitles" in output
        if has_subtitles:
            self.add("FFmpeg 字幕 filter", True, "主机 FFmpeg 提供 subtitles/libass filter")
            return
        fallback = self.project_root / "scripts/ffmpeg-with-libass.sh"
        fallback_ok = fallback.is_file() and os.access(fallback, os.X_OK)
        self._renderer_uses_docker_fallback = fallback_ok and self._renderer_fallback_configured()
        self.add(
            "FFmpeg 字幕 filter",
            self._renderer_uses_docker_fallback,
            "主机缺少 subtitles；将由 Docker 应用镜像提供 libass"
            if self._renderer_uses_docker_fallback
            else "主机缺少 subtitles/libass，且未找到可执行 Docker fallback",
            required=not self._renderer_uses_docker_fallback,
        )

    def _check_compose(self) -> None:
        required = self.require_compose or self._renderer_uses_docker_fallback
        docker = shutil.which("docker")
        if docker is None:
            self.add("Docker Compose", False, "找不到 docker CLI", required=required)
            return
        ok, output = _run_command([docker, "compose", "config", "--quiet"], cwd=self.project_root, timeout=30.0)
        self.add("Docker Compose 配置", ok, output or "Compose 配置可用" if ok else output or "配置无效", required=required)
        if not ok:
            return
        ok, services = _run_command(
            [docker, "compose", "ps", "--services", "--status", "running"],
            cwd=self.project_root,
            timeout=20.0,
        )
        running = set(services.split()) if ok else set()
        have_services = {"api", "worker"}.issubset(running)
        self.add(
            "Docker API/Worker",
            have_services,
            "api、worker 均在运行" if have_services else "需要运行 api 和 worker",
            required=required,
        )
        if "api" in running:
            health, error = _request_json("http://127.0.0.1:8000/healthz")
            self.add("本地 API /healthz", health is not None, error or "API 健康检查通过", required=required)
            ok, filters = _run_command(
                [docker, "compose", "exec", "-T", "api", "ffmpeg", "-hide_banner", "-filters"],
                cwd=self.project_root,
                timeout=30.0,
            )
            has_subtitles = ok and "subtitles" in filters
        self.add(
            "Docker FFmpeg subtitles/libass",
            has_subtitles,
            "容器内字幕 filter 可用" if has_subtitles else "容器内缺少 subtitles/libass filter",
            required=required and self._renderer_uses_docker_fallback,
        )

    def _check_disk(self) -> None:
        try:
            free_gb = shutil.disk_usage(self.project_root).free / (1024**3)
        except OSError as exc:
            self.add("可用磁盘", False, str(exc))
            return
        self.add(
            "可用磁盘",
            free_gb >= self.min_free_gb,
            f"{free_gb:.1f} GB（门槛 {self.min_free_gb:.1f} GB）",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--comfyui-root", type=Path, default=None)
    parser.add_argument("--comfyui-url", default=None)
    parser.add_argument("--ollama-url", default=None)
    parser.add_argument("--validate-models", action="store_true")
    parser.add_argument("--require-compose", action="store_true")
    parser.add_argument("--require-mps", action="store_true")
    parser.add_argument("--min-free-gb", type=float, default=10.0)
    args = parser.parse_args(argv)

    project_root = args.project_root.expanduser().resolve()
    config_path = args.config
    if config_path is not None:
        config_path = _resolve_path(project_root, str(config_path), project_root / "config/config.example.toml")
    comfyui_root = args.comfyui_root.expanduser().resolve() if args.comfyui_root else None
    checker = MacRuntimePreflight(
        project_root=project_root,
        config_path=config_path,
        comfyui_root=comfyui_root,
        comfyui_url=args.comfyui_url,
        ollama_url=args.ollama_url,
        validate_models=args.validate_models,
        require_compose=args.require_compose,
        require_mps=args.require_mps,
        min_free_gb=args.min_free_gb,
    )
    return checker.run()


if __name__ == "__main__":
    raise SystemExit(main())
