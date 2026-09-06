from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import httpx

from app.config import load_settings
from app.domain.models import ArtifactSummary, GenerationTaskRecord, ProjectRecord
from app.main import create_app


def test_artifact_content_route_serves_local_media_and_byte_ranges(tmp_path) -> None:
    async def exercise() -> None:
        settings = replace(
            load_settings("config/config.example.toml"),
            auth_mode="local",
            persistence_backend="memory",
            queue_backend="in_process",
            storage_provider="local",
            storage_base_path=str(tmp_path),
        )
        app = create_app(settings)
        project_id = uuid4()
        image = ArtifactSummary(
            type="reference_image",
            provider="test-image",
            metadata={
                "storage_key": "reference-images/asset/v1/image.png",
                "content_type": "image/png",
            },
        )
        video = ArtifactSummary(
            type="video_clip",
            provider="test-video",
            metadata={
                "storage_key": "video-clips/episode-1/clip.mp4",
                "content_type": "video/mp4",
            },
        )
        task = GenerationTaskRecord(project_id=project_id, artifacts=[image, video])
        await app.state.store.create_project(
            ProjectRecord(
                id=project_id,
                title="Artifact 内容测试",
                topic="浏览器预览",
                language="zh-CN",
                target_duration_seconds=30,
                aspect_ratio="9:16",
                tone="测试",
            )
        )
        await app.state.artifact_storage.put_bytes(
            "reference-images/asset/v1/image.png",
            b"png-bytes",
            "image/png",
        )
        await app.state.artifact_storage.put_bytes(
            "video-clips/episode-1/clip.mp4",
            b"0123456789",
            "video/mp4",
        )
        await app.state.store.create_task(task)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            image_response = await client.get(f"/api/v1/artifacts/{image.id}/content")
            assert image_response.status_code == 200
            assert image_response.content == b"png-bytes"
            assert image_response.headers["content-type"] == "image/png"
            assert image_response.headers["content-disposition"].startswith("inline;")

            download_response = await client.get(
                f"/api/v1/artifacts/{image.id}/content?download=true"
            )
            assert download_response.status_code == 200
            assert download_response.content == b"png-bytes"
            assert download_response.headers["content-disposition"].startswith("attachment;")

            video_response = await client.get(
                f"/api/v1/artifacts/{video.id}/content",
                headers={"Range": "bytes=2-5"},
            )
            assert video_response.status_code == 206
            assert video_response.content == b"2345"
            assert video_response.headers["content-type"] == "video/mp4"
            assert video_response.headers["content-range"] == "bytes 2-5/10"
            assert video_response.headers["accept-ranges"] == "bytes"

        await app.state.task_queue.close()
        await app.state.artifact_storage.close()

    import asyncio

    asyncio.run(exercise())


def test_artifact_content_route_reports_artifacts_without_binary_content(tmp_path) -> None:
    async def exercise() -> None:
        settings = replace(
            load_settings("config/config.example.toml"),
            auth_mode="local",
            persistence_backend="memory",
            queue_backend="in_process",
            storage_provider="local",
            storage_base_path=str(tmp_path),
        )
        app = create_app(settings)
        artifact = ArtifactSummary(type="script_json", provider="test-script")
        project_id = uuid4()
        task = GenerationTaskRecord(project_id=project_id, artifacts=[artifact])
        await app.state.store.create_project(
            ProjectRecord(
                id=project_id,
                title="无二进制 Artifact 测试",
                topic="内容读取",
                language="zh-CN",
                target_duration_seconds=30,
                aspect_ratio="9:16",
                tone="测试",
            )
        )
        await app.state.store.create_task(task)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/api/v1/artifacts/{artifact.id}/content")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ARTIFACT_CONTENT_UNAVAILABLE"
        await app.state.task_queue.close()
        await app.state.artifact_storage.close()

    import asyncio

    asyncio.run(exercise())
