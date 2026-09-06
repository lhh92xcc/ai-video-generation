#!/usr/bin/env python3
"""Validate the Windows GPU host before the first real generation run.

This script is intentionally read-only.  It never downloads models, changes
ComfyUI files, starts services, or prints secrets.  The PowerShell launcher
uses it after Docker/host services are started, while it can also be run on
its own for a strict preflight.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    message: str
    required: bool = True

    @property
    def status(self) -> str:
        if self.ok:
            return "PASS"
        return "FAIL" if self.required else "WARN"


IMAGE_NODES = (
    "UnetLoaderGGUF",
    "DualCLIPLoaderGGUF",
    "VAELoader",
)
IDENTITY_NODES = (
    "PulidFluxModelLoader",
    "PulidFluxInsightFaceLoader",
    "PulidFluxEvaClipLoader",
    "ApplyPulidFlux",
)
VIDEO_NODES = (
    "WanVideoModelLoader",
    "WanVideoVAELoader",
    "LoadWanVideoT5TextEncoder",
    "WanVideoImageToVideoEncode",
    "WanVideoSampler",
    "VHS_VideoCombine",
)


def _env_or(config: dict[str, Any], section: str, key: str, env_name: str, default: str) -> str:
    value = os.getenv(env_name)
    if value and value.strip():
        return value.strip()
    raw = config.get(section, {}).get(key, default)
    return str(raw)


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


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


def _load_workflow(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"文件不存在：{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"无法读取 JSON：{exc}"
    if not isinstance(payload, dict) or not payload:
        return None, "workflow 必须是非空 JSON object"
    return payload, None


def _workflow_classes(workflow: dict[str, Any]) -> set[str]:
    classes: set[str] = set()
    for node in workflow.values():
        if isinstance(node, dict) and isinstance(node.get("class_type"), str):
            classes.add(node["class_type"])
    return classes


def _check_any_file(paths: tuple[Path, ...]) -> bool:
    return any(path.is_file() and path.stat().st_size > 0 for path in paths)


class WindowsRuntimePreflight:
    def __init__(
        self,
        *,
        project_root: Path,
        comfyui_root: Path | None,
        require_comfyui: bool,
        validate_comfyui_assets: bool,
        require_ollama_model: bool,
        require_musetalk: bool,
        comfyui_url: str = "http://127.0.0.1:8188",
        ollama_url: str = "http://127.0.0.1:11434",
        musetalk_url: str = "http://127.0.0.1:8090",
    ) -> None:
        self.project_root = project_root
        self.comfyui_root = comfyui_root
        self.require_comfyui = require_comfyui
        self.validate_comfyui_assets = validate_comfyui_assets
        self.require_ollama_model = require_ollama_model
        self.require_musetalk = require_musetalk
        self.comfyui_url = comfyui_url.rstrip("/")
        self.ollama_url = ollama_url.rstrip("/")
        self.musetalk_url = musetalk_url.rstrip("/")
        self.results: list[CheckResult] = []
        self.config: dict[str, Any] = {}

    def add(self, name: str, ok: bool, message: str, *, required: bool = True) -> None:
        result = CheckResult(name=name, ok=ok, message=message, required=required)
        self.results.append(result)
        print(f"[{result.status}] {name}: {message}")

    def run(self) -> int:
        print("AI Video Generation / Windows GPU 主机媒体前置检查")
        print(f"项目目录: {self.project_root}")
        if self.comfyui_root:
            print(f"ComfyUI 目录: {self.comfyui_root}")
        print("")

        self._load_config()
        self._check_project_workflows()
        self._check_comfyui_http()
        self._check_ollama()
        self._check_musetalk()
        if self.validate_comfyui_assets:
            self._check_comfyui_assets()

        failures = [result.name for result in self.results if not result.ok and result.required]
        print("")
        if failures:
            print("媒体前置检查失败：" + ", ".join(failures))
            return 1
        print("媒体前置检查通过。")
        return 0

    def _load_config(self) -> None:
        path = self.project_root / "config" / "config.windows_gpu.toml"
        if not path.is_file():
            self.add("Windows 配置", False, f"配置不存在：{path}")
            return
        try:
            self.config = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            self.add("Windows 配置", False, f"无法读取 TOML：{exc}")
            return
        self.add("Windows 配置", True, "TOML 可读取")

    def _check_project_workflows(self) -> None:
        image_path = self.project_root / _env_or(
            self.config, "image_generation", "workflow_path", "AI_VIDEO_IMAGE_WORKFLOW_PATH", "config/comfyui/flux-schnell-t2i-api.json"
        )
        identity_path = self.project_root / _env_or(
            self.config, "image_generation", "identity_workflow_path", "AI_VIDEO_IMAGE_IDENTITY_WORKFLOW_PATH", "config/comfyui/flux-schnell-faceid-reference-api.json"
        )
        video_path = self.project_root / _env_or(
            self.config, "video_generation", "workflow_path", "AI_VIDEO_VIDEO_WORKFLOW_PATH", "config/comfyui/wan2.1-i2v-api.json"
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

    def _check_comfyui_http(self) -> None:
        required = self.require_comfyui or self.validate_comfyui_assets
        system, error = _request_json(_join_url(self.comfyui_url, "/system_stats"))
        if system is None:
            self.add("ComfyUI /system_stats", False, error or "无响应", required=required)
            return
        self.add("ComfyUI /system_stats", True, "服务响应正常")

        node_info, error = _request_json(_join_url(self.comfyui_url, "/object_info"), timeout=10.0)
        if node_info is None:
            self.add("ComfyUI 节点清单", False, error or "无法读取 /object_info", required=required)
            return
        available = set(node_info)
        expected = set(IMAGE_NODES + IDENTITY_NODES + VIDEO_NODES)
        missing = sorted(expected - available)
        if missing:
            self.add(
                "ComfyUI 必需节点",
                False,
                "未注册：" + ", ".join(missing),
                required=required,
            )
        else:
            self.add("ComfyUI 必需节点", True, f"已注册 {len(expected)} 个工作流节点")

    def _check_ollama(self) -> None:
        tags, error = _request_json(_join_url(self.ollama_url, "/api/tags"))
        if tags is None:
            self.add("Ollama", False, error or "无响应", required=self.require_ollama_model)
            return
        model = _env_or(self.config, "llm", "model", "AI_VIDEO_LLM_MODEL", "qwen2.5:7b")
        names = {
            str(item.get("name") or item.get("model"))
            for item in tags.get("models", [])
            if isinstance(item, dict)
        }
        if model in names:
            self.add("Ollama 模型", True, f"已找到 {model}")
        else:
            self.add(
                "Ollama 模型",
                False,
                f"未找到 {model}，当前模型：{', '.join(sorted(name for name in names if name != 'None')) or '无'}",
                required=self.require_ollama_model,
            )

    def _check_musetalk(self) -> None:
        health, error = _request_json(_join_url(self.musetalk_url, "/healthz"))
        if health is None:
            self.add("MuseTalk bridge", False, error or "无响应", required=self.require_musetalk)
            return
        configured = health.get("configured") is True
        status = str(health.get("status", "unknown"))
        if configured and status == "ok":
            self.add("MuseTalk runtime", True, "bridge、wrapper 和运行时已配置")
        else:
            self.add(
                "MuseTalk runtime",
                False,
                "bridge 在线但 wrapper/model 尚未就绪",
                required=self.require_musetalk,
            )

    def _check_comfyui_assets(self) -> None:
        if self.comfyui_root is None:
            self.add("ComfyUI 模型目录", False, "需要 -ComfyUIRoot 或 COMFYUI_ROOT 才能检查实际文件")
            return
        if not self.comfyui_root.is_dir():
            self.add("ComfyUI 模型目录", False, f"目录不存在：{self.comfyui_root}")
            return

        image_model = _env_or(
            self.config, "image_generation", "model", "AI_VIDEO_IMAGE_MODEL", "flux1-schnell-Q4_K_S.gguf"
        )
        video_model = _env_or(
            self.config, "video_generation", "model", "AI_VIDEO_VIDEO_MODEL", "wan2.1-i2v-14b-480p-Q4_K_S.gguf"
        )
        required_files = (
            ("Flux 模型", self.comfyui_root / "models" / "unet" / image_model),
            ("Wan 模型", self.comfyui_root / "models" / "diffusion_models" / video_model),
            (
                "Flux T5",
                self.comfyui_root / "models" / "clip" / "t5-v1_1-xxl-encoder-Q4_K_S.gguf",
            ),
            ("Flux CLIP-L", self.comfyui_root / "models" / "clip" / "clip_l.safetensors"),
            ("Flux VAE", self.comfyui_root / "models" / "vae" / "flux1_vae.safetensors"),
            (
                "PuLID 权重",
                self.comfyui_root / "models" / "pulid" / "pulid_flux_v0.9.1.safetensors",
            ),
            (
                "Wan T5",
                self.comfyui_root / "models" / "text_encoders" / "umt5-xxl-enc-fp8_e4m3fn.safetensors",
            ),
            (
                "Wan VAE",
                self.comfyui_root / "models" / "vae" / "Wan2_1_VAE_bf16.safetensors",
            ),
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

        insightface_root = self.comfyui_root / "models" / "insightface" / "models" / "antelopev2"
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
            "已找到全部模型" if not missing else "缺少：" + ", ".join(missing),
        )

        eva_candidates = (
            Path(os.getenv("USERPROFILE", "")) / ".cache" / "clip" / "EVA02_CLIP_L_336_psz14_s6B.pt",
            self.comfyui_root / "models" / "clip" / "EVA02_CLIP_L_336_psz14_s6B.pt",
        )
        self.add(
            "PuLID EVA-CLIP",
            _check_any_file(eva_candidates),
            "已找到 EVA-CLIP" if _check_any_file(eva_candidates) else "未找到；按 PuLID 安装方式放入用户缓存或 ComfyUI/models/clip",
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--comfyui-root", type=Path)
    parser.add_argument("--require-comfyui", action="store_true")
    parser.add_argument("--validate-comfyui-assets", action="store_true")
    parser.add_argument("--require-ollama-model", action="store_true")
    parser.add_argument("--require-musetalk", action="store_true")
    parser.add_argument("--comfyui-url", default="http://127.0.0.1:8188")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--musetalk-url", default="http://127.0.0.1:8090")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    checker = WindowsRuntimePreflight(
        project_root=args.project_root.resolve(),
        comfyui_root=args.comfyui_root.resolve() if args.comfyui_root else None,
        require_comfyui=args.require_comfyui,
        validate_comfyui_assets=args.validate_comfyui_assets,
        require_ollama_model=args.require_ollama_model,
        require_musetalk=args.require_musetalk,
        comfyui_url=args.comfyui_url,
        ollama_url=args.ollama_url,
        musetalk_url=args.musetalk_url,
    )
    return checker.run()


if __name__ == "__main__":
    sys.exit(main())
