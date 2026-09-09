#!/usr/bin/env python3
"""Upload a novel and start the complete production DAG through the API.

This command is a thin client for the public novel-production endpoints.  It
does not implement generation logic, bypass review gates, or print secrets.  A
human-review ``blocked`` state is an expected result: rerun with the printed
project ID after reviewing the assets in the creator UI.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Any

import httpx


MAX_SOURCE_BYTES = 5 * 1024 * 1024
ALLOWED_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown"}
BLOCKED_EXIT_CODE = 2


class ProductionCliError(RuntimeError):
    """A user-facing API or input failure."""


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "HTTP_ERROR")
            message = str(error.get("message") or "请求失败")
            return f"{code}: {message}"
        detail = payload.get("detail")
        if detail:
            return str(detail)
    return f"HTTP {response.status_code}"


class ProductionApi:
    """Small synchronous client for the novel production API."""

    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise ProductionCliError(f"无法连接 API：{exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ProductionCliError(_error_message(response))
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise ProductionCliError("API 返回不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ProductionCliError("API 返回格式无效")
        return payload

    def create_project(
        self,
        *,
        title: str,
        language: str,
        episodes: int,
        episode_duration: int,
        rights_status: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/novel-projects",
            json={
                "title": title,
                "language": language,
                "target_episode_count": episodes,
                "target_episode_duration_seconds": episode_duration,
                "rights_status": rights_status,
            },
        )

    def upload_source(self, project_id: str, source_path: Path, content_type: str) -> dict[str, Any]:
        try:
            source = source_path.read_bytes()
        except OSError as exc:
            raise ProductionCliError(f"无法读取小说文件：{source_path}") from exc
        if len(source) > MAX_SOURCE_BYTES:
            raise ProductionCliError("小说文件超过 5 MB 上限")
        return self._request(
            "POST",
            f"/api/v1/novel-projects/{project_id}/sources",
            files={"file": (source_path.name, source, content_type)},
        )

    def start_production(
        self,
        project_id: str,
        *,
        idempotency_key: str,
        episodes: int,
        include_bgm: bool,
        subtitle_mode: str,
        quality_profile: str | None,
        image_provider_profile: str | None,
        video_provider_profile: str | None,
        shot_keyframe_mode: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target_episode_count": episodes,
            "include_reference_images": True,
            "include_narration": True,
            "include_subtitles": True,
            "include_bgm": include_bgm,
            "include_video": True,
            "include_assembly": True,
            "subtitle_mode": subtitle_mode,
            "shot_keyframe_mode": shot_keyframe_mode,
            "auto_advance": True,
        }
        optional_values = {
            "visual_quality_profile_id": quality_profile,
            "image_provider_profile_id": image_provider_profile,
            "video_provider_profile_id": video_provider_profile,
        }
        payload.update({key: value for key, value in optional_values.items() if value})
        return self._request(
            "POST",
            f"/api/v1/novel-projects/{project_id}/production-runs",
            headers={"Idempotency-Key": idempotency_key},
            json=payload,
        )

    def get_production(self, project_id: str, run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/novel-projects/{project_id}/production-runs/{run_id}",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--novel", type=Path, help="TXT/Markdown 小说文件；使用 --project-id 恢复时可省略")
    parser.add_argument("--project-id", help="已有项目 ID；提供后跳过创建项目和上传文件")
    parser.add_argument("--title", help="新项目名称；省略时使用小说文件名")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--episodes", type=int, default=1, choices=tuple(range(1, 101)))
    parser.add_argument("--episode-duration", type=int, default=60, choices=tuple(range(30, 601)))
    parser.add_argument(
        "--rights-status",
        choices=("unknown", "pending", "confirmed", "denied"),
        default="unknown",
    )
    parser.add_argument(
        "--quality-profile",
        choices=("local_safe", "local_balanced", "high_quality"),
        help="视觉质量档案 ID；省略时使用当前配置默认值",
    )
    parser.add_argument("--image-provider-profile", help="参考图 Provider Profile ID")
    parser.add_argument("--video-provider-profile", help="视频 Provider Profile ID")
    parser.add_argument("--subtitle-mode", choices=("align", "asr"), default="align")
    parser.add_argument("--shot-keyframe-mode", choices=("auto", "always", "off"), default="auto")
    parser.add_argument("--include-bgm", action="store_true")
    parser.add_argument("--idempotency-key", help="重复执行时复用同一个 Run；默认按 project ID 生成")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=0.0, help="0 表示不设轮询总时限")
    parser.add_argument("--no-wait", action="store_true", help="启动 Run 后立即退出，不轮询状态")
    return parser


def _validate_source(path: Path) -> tuple[Path, str]:
    path = path.expanduser().resolve()
    content_type = ALLOWED_SUFFIXES.get(path.suffix.lower())
    if content_type is None:
        raise ProductionCliError("小说文件必须是 .txt 或 .md")
    if not path.is_file():
        raise ProductionCliError(f"小说文件不存在：{path}")
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ProductionCliError("小说文件超过 5 MB 上限")
    except OSError as exc:
        raise ProductionCliError(f"无法检查小说文件：{path}") from exc
    return path, content_type


def _default_key(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]
    return f"novel-production-{digest}"


def _print_run(run: dict[str, Any], *, first: bool = False) -> None:
    prefix = "已启动" if first else "状态"
    status = str(run.get("status") or "unknown")
    stage = str(run.get("stage") or "production")
    message = str(run.get("message") or "")
    print(f"{prefix}：{status} · 阶段 {stage} · {message}")


def run(args: argparse.Namespace, *, client: httpx.Client | None = None) -> int:
    if args.poll_interval <= 0:
        raise ProductionCliError("--poll-interval 必须大于 0")
    if args.timeout_seconds < 0:
        raise ProductionCliError("--timeout-seconds 不能小于 0")
    if args.project_id is not None and args.novel is not None:
        raise ProductionCliError("恢复已有项目时不要同时提供 --novel")
    if args.project_id is None and args.novel is None:
        raise ProductionCliError("新建项目时必须提供 --novel")

    owns_client = client is None
    http_client = client or httpx.Client(timeout=30.0)
    api = ProductionApi(http_client, args.base_url)
    try:
        project_id = args.project_id
        episodes = args.episodes
        if project_id is None:
            assert args.novel is not None
            source_path, content_type = _validate_source(args.novel)
            title = (args.title or source_path.stem).strip()
            if not title:
                raise ProductionCliError("项目名称不能为空")
            project = api.create_project(
                title=title,
                language=args.language,
                episodes=episodes,
                episode_duration=args.episode_duration,
                rights_status=args.rights_status,
            )
            project_id = str(project.get("id") or "")
            if not project_id:
                raise ProductionCliError("创建项目响应缺少 project ID")
            api.upload_source(project_id, source_path, content_type)
            print(f"项目已创建并上传原文：{project_id}")
        else:
            print(f"继续已有项目：{project_id}")

        idempotency_key = args.idempotency_key or _default_key(project_id)
        run_payload = api.start_production(
            project_id,
            idempotency_key=idempotency_key,
            episodes=episodes,
            include_bgm=args.include_bgm,
            subtitle_mode=args.subtitle_mode,
            quality_profile=args.quality_profile,
            image_provider_profile=args.image_provider_profile,
            video_provider_profile=args.video_provider_profile,
            shot_keyframe_mode=args.shot_keyframe_mode,
        )
        run_id = str(run_payload.get("run_id") or "")
        if not run_id:
            raise ProductionCliError("启动生产响应缺少 run ID")
        _print_run(run_payload, first=True)
        print(f"项目 ID：{project_id}")
        print(f"Run ID：{run_id}")
        print(f"幂等键：{idempotency_key}")
        if args.no_wait:
            print("已提交，未等待任务完成。可在前台远程队列查看状态。")
            return 0

        started = time.monotonic()
        previous: tuple[str, str, str] | None = None
        while True:
            current = api.get_production(project_id, run_id)
            signature = (
                str(current.get("status") or "unknown"),
                str(current.get("stage") or "production"),
                str(current.get("message") or ""),
            )
            if signature != previous:
                _print_run(current)
                previous = signature
            status = signature[0]
            if status == "completed":
                print("完整生产 Run 已完成。请在前台检查最终成片和作品集审核报告。")
                return 0
            if status == "failed":
                print("完整生产 Run 失败；请查看任务中心的错误码和失败阶段。", file=sys.stderr)
                return 1
            if status == "blocked":
                print(
                    "Run 在人工审核或配置门禁处暂停；完成前台审核后，用相同项目 ID 和幂等键重新执行。",
                    file=sys.stderr,
                )
                print(
                    f"恢复命令：python scripts/run-novel-production.py --project-id {project_id} "
                    f"--idempotency-key {idempotency_key}",
                    file=sys.stderr,
                )
                return BLOCKED_EXIT_CODE
            if args.timeout_seconds and time.monotonic() - started >= args.timeout_seconds:
                raise ProductionCliError("轮询超过 --timeout-seconds，Run 仍在进行中")
            time.sleep(args.poll_interval)
    finally:
        if owns_client:
            http_client.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except ProductionCliError as exc:
        print(f"生产 CLI 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
