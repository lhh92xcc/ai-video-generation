from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
import os
import re
import subprocess
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from app.config import load_settings
from app.domain.models import ArtifactSummary, GenerationTaskRecord
from app.repositories.in_memory import InMemoryStore
from app.services.artifact_service import ArtifactService
from app.storage.factory import create_artifact_storage
from app.storage.s3_compatible import S3CompatibleArtifactStorage
from app.storage.local import LocalFileArtifactStorage, MockArtifactStorage
from app.storage.protocol import StorageError


def test_local_artifact_storage_writes_atomically_and_returns_hash(tmp_path) -> None:
    async def exercise() -> None:
        storage = LocalFileArtifactStorage(tmp_path)
        artifact = await storage.put_bytes(
            "reference-images/asset/v2/image.png",
            b"image-bytes",
            "image/png",
        )
        await storage.close()

        target = tmp_path / "reference-images" / "asset" / "v2" / "image.png"
        assert target.read_bytes() == b"image-bytes"
        assert artifact.uri == "local://reference-images/asset/v2/image.png"
        assert artifact.size_bytes == len(b"image-bytes")
        assert artifact.sha256 == hashlib.sha256(b"image-bytes").hexdigest()

    asyncio.run(exercise())


def test_local_and_mock_artifact_storage_can_read_existing_bytes(tmp_path) -> None:
    async def exercise() -> None:
        local_storage = LocalFileArtifactStorage(tmp_path / "local")
        mock_storage = MockArtifactStorage()
        content = b"readable-artifact"

        await local_storage.put_bytes("videos/episode-1/clip.mp4", content, "video/mp4")
        await mock_storage.put_bytes("videos/episode-1/clip.mp4", content, "video/mp4")

        assert await local_storage.get_bytes("videos/episode-1/clip.mp4") == content
        assert await mock_storage.get_bytes("videos/episode-1/clip.mp4") == content

        with pytest.raises(StorageError) as local_error:
            await local_storage.get_bytes("videos/episode-1/missing.mp4")
        with pytest.raises(StorageError) as mock_error:
            await mock_storage.get_bytes("videos/episode-1/missing.mp4")
        assert local_error.value.code == "STORAGE_NOT_FOUND"
        assert mock_error.value.code == "STORAGE_NOT_FOUND"

        await local_storage.close()
        await mock_storage.close()

    asyncio.run(exercise())


def test_local_artifact_storage_rejects_path_traversal(tmp_path) -> None:
    async def exercise() -> None:
        storage = LocalFileArtifactStorage(tmp_path)
        with pytest.raises(ValueError):
            await storage.put_bytes("../outside.txt", b"blocked", "text/plain")
        await storage.close()

    asyncio.run(exercise())


def test_s3_artifact_storage_signs_put_and_returns_metadata() -> None:
    async def exercise() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000/minio",
            bucket="ai-video-artifacts",
            region="us-east-1",
            access_key="test-access",
            secret_key="test-secret",
            client=client,
            clock=lambda: datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc),
        )

        content = b"image-bytes"
        artifact = await storage.put_bytes(
            "reference-images/asset/v2/image.png",
            content,
            "image/png",
        )
        await storage.close()
        await client.aclose()

        assert artifact.uri == "s3://ai-video-artifacts/reference-images/asset/v2/image.png"
        assert artifact.storage_key == "reference-images/asset/v2/image.png"
        assert artifact.content_type == "image/png"
        assert artifact.size_bytes == len(content)
        assert artifact.sha256 == hashlib.sha256(content).hexdigest()
        assert len(requests) == 1
        request = requests[0]
        assert request.method == "PUT"
        assert request.url.path == "/minio/ai-video-artifacts/reference-images/asset/v2/image.png"
        assert request.content == content
        assert request.headers["content-type"] == "image/png"
        assert request.headers["x-amz-content-sha256"] == artifact.sha256
        assert request.headers["x-amz-date"] == "20260822T123456Z"
        authorization = request.headers["authorization"]
        assert authorization.startswith(
            "AWS4-HMAC-SHA256 Credential=test-access/20260822/us-east-1/s3/aws4_request, "
        )
        assert "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date" in authorization
        assert re.search(r"Signature=[0-9a-f]{64}$", authorization)

    asyncio.run(exercise())


def test_s3_artifact_storage_reads_bytes_with_presigned_get() -> None:
    async def exercise() -> None:
        requests: list[httpx.Request] = []
        content = b"video-bytes"

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=content, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000/minio",
            bucket="ai-video-artifacts",
            region="us-east-1",
            access_key="test-access",
            secret_key="test-secret",
            client=client,
            clock=lambda: datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc),
        )

        assert await storage.get_bytes("video-clips/episode-1/clip.mp4") == content
        await storage.close()
        await client.aclose()

        assert len(requests) == 1
        request = requests[0]
        assert request.method == "GET"
        assert request.url.path == "/minio/ai-video-artifacts/video-clips/episode-1/clip.mp4"
        assert request.url.params["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
        assert request.url.params["X-Amz-SignedHeaders"] == "host"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (404, "STORAGE_NOT_FOUND"),
        (401, "STORAGE_AUTH_FAILED"),
        (429, "STORAGE_RATE_LIMITED"),
        (503, "STORAGE_UNAVAILABLE"),
        (400, "STORAGE_DOWNLOAD_FAILED"),
    ],
)
def test_s3_artifact_storage_maps_download_errors(status_code: int, error_code: str) -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000",
            bucket="artifacts",
            region="us-east-1",
            access_key="access",
            secret_key="secret",
            client=client,
        )
        with pytest.raises(StorageError) as error:
            await storage.get_bytes("file.bin")
        await storage.close()
        await client.aclose()
        assert error.value.code == error_code

    asyncio.run(exercise())


def test_s3_artifact_storage_creates_presigned_get_url() -> None:
    storage = S3CompatibleArtifactStorage(
        endpoint="http://localhost:9000/minio",
        bucket="ai-video-artifacts",
        region="us-east-1",
        access_key="test-access",
        secret_key="test-secret",
        signed_url_expire_seconds=900,
        clock=lambda: datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc),
    )

    url = storage.create_download_url(
        "reference-images/asset/v2/image.png",
        expires_in_seconds=600,
    )
    parsed_url = urlsplit(url)
    query = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "http"
    assert parsed_url.netloc == "localhost:9000"
    assert parsed_url.path == "/minio/ai-video-artifacts/reference-images/asset/v2/image.png"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Credential"] == [
        "test-access/20260822/us-east-1/s3/aws4_request"
    ]
    assert query["X-Amz-Date"] == ["20260822T123456Z"]
    assert query["X-Amz-Expires"] == ["600"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Signature"] == [
        "c379a22685ae8c85436e65c4fcd63060bfe12263a456ae06345608b6310b873a"
    ]


def test_s3_public_endpoint_is_used_for_browser_download_urls() -> None:
    storage = S3CompatibleArtifactStorage(
        endpoint="http://minio:9000",
        public_endpoint="http://localhost:9000",
        bucket="artifacts",
        region="us-east-1",
        access_key="access",
        secret_key="secret",
        clock=lambda: datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc),
    )

    url = storage.create_download_url("episode-videos/episode-1/rendered.mp4")

    assert url.startswith(
        "http://localhost:9000/artifacts/episode-videos/episode-1/rendered.mp4?"
    )


@pytest.mark.integration
def test_real_minio_artifact_storage_uploads_and_downloads() -> None:
    if os.getenv("AI_VIDEO_RUN_MINIO_INTEGRATION") != "1":
        pytest.skip("set AI_VIDEO_RUN_MINIO_INTEGRATION=1 to run the MinIO integration test")

    endpoint = os.getenv("AI_VIDEO_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    bucket = os.getenv("AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
    access_key = os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY", "ai-video-dev")
    secret_key = os.getenv("AI_VIDEO_STORAGE_SECRET_KEY", "ai-video-dev-password")
    key = f"integration/pytest-real-minio-{uuid4().hex}.bin"
    content = b"pytest-real-minio"

    async def exercise() -> None:
        storage = S3CompatibleArtifactStorage(
            endpoint=endpoint,
            bucket=bucket,
            region=os.getenv("AI_VIDEO_STORAGE_REGION", "us-east-1"),
            access_key=access_key,
            secret_key=secret_key,
            timeout_seconds=int(os.getenv("AI_VIDEO_STORAGE_TIMEOUT_SECONDS", "30")),
            signed_url_expire_seconds=300,
        )
        try:
            stored = await storage.put_bytes(key, content, "application/octet-stream")
            downloaded = await storage.get_bytes(key)

            assert downloaded == content
            assert stored.sha256 == hashlib.sha256(downloaded).hexdigest()
        finally:
            await storage.close()
            if (
                os.getenv("AI_VIDEO_MINIO_COMPOSE_CLEANUP", "1") == "1"
                and urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
            ):
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "minio",
                        "mc",
                        "rm",
                        "--quiet",
                        f"local/{bucket}/{key}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

    asyncio.run(exercise())


def test_s3_artifact_storage_rejects_invalid_presigned_url_expiration() -> None:
    storage = S3CompatibleArtifactStorage(
        endpoint="http://localhost:9000",
        bucket="artifacts",
        region="us-east-1",
        access_key="access",
        secret_key="secret",
    )

    with pytest.raises(StorageError) as error:
        storage.create_download_url("file.bin", expires_in_seconds=604801)

    assert error.value.code == "STORAGE_INVALID_REQUEST"


@pytest.mark.parametrize("status_code", [200, 204])
def test_s3_artifact_storage_accepts_success_statuses(status_code: int) -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000",
            bucket="artifacts",
            region="us-east-1",
            access_key="access",
            secret_key="secret",
            client=client,
        )
        artifact = await storage.put_bytes("file.bin", b"content", "application/octet-stream")
        await storage.close()
        await client.aclose()
        assert artifact.uri == "s3://artifacts/file.bin"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (401, "STORAGE_AUTH_FAILED"),
        (403, "STORAGE_AUTH_FAILED"),
        (429, "STORAGE_RATE_LIMITED"),
        (500, "STORAGE_UNAVAILABLE"),
        (503, "STORAGE_UNAVAILABLE"),
        (400, "STORAGE_UPLOAD_FAILED"),
    ],
)
def test_s3_artifact_storage_maps_upload_errors(status_code: int, error_code: str) -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000",
            bucket="artifacts",
            region="us-east-1",
            access_key="access",
            secret_key="secret",
            client=client,
        )
        with pytest.raises(StorageError) as error:
            await storage.put_bytes("file.bin", b"content", "application/octet-stream")
        await storage.close()
        await client.aclose()
        assert error.value.code == error_code

    asyncio.run(exercise())


def test_s3_artifact_storage_maps_timeout() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("simulated timeout", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000",
            bucket="artifacts",
            region="us-east-1",
            access_key="access",
            secret_key="secret",
            client=client,
        )
        with pytest.raises(StorageError) as error:
            await storage.put_bytes("file.bin", b"content", "application/octet-stream")
        await storage.close()
        await client.aclose()
        assert error.value.code == "STORAGE_TIMEOUT"

    asyncio.run(exercise())


def test_s3_artifact_storage_rejects_unsafe_key_and_missing_credentials() -> None:
    async def exercise() -> None:
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000",
            bucket="artifacts",
            region="us-east-1",
            access_key=None,
            secret_key=None,
        )
        with pytest.raises(ValueError):
            await storage.put_bytes("../outside.bin", b"blocked", "application/octet-stream")
        with pytest.raises(StorageError) as error:
            await storage.put_bytes("file.bin", b"blocked", "application/octet-stream")
        await storage.close()
        assert error.value.code == "STORAGE_AUTH_REQUIRED"

    asyncio.run(exercise())


def test_s3_storage_configuration_and_factory_aliases(monkeypatch) -> None:
    monkeypatch.setenv("AI_VIDEO_STORAGE_PROVIDER", "minio")
    monkeypatch.setenv("AI_VIDEO_STORAGE_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("AI_VIDEO_STORAGE_PUBLIC_ENDPOINT", "https://video.example.com")
    monkeypatch.setenv("AI_VIDEO_STORAGE_BUCKET", "test-bucket")
    monkeypatch.setenv("AI_VIDEO_STORAGE_REGION", "us-east-1")
    monkeypatch.setenv("AI_VIDEO_STORAGE_ACCESS_KEY", "access")
    monkeypatch.setenv("AI_VIDEO_STORAGE_SECRET_KEY", "secret")
    monkeypatch.setenv("AI_VIDEO_STORAGE_TIMEOUT_SECONDS", "15")

    settings = load_settings("config/config.example.toml")
    storage = create_artifact_storage(settings)

    assert isinstance(storage, S3CompatibleArtifactStorage)
    assert settings.storage_endpoint == "http://localhost:9000"
    assert settings.storage_public_endpoint == "https://video.example.com"
    assert settings.storage_bucket == "test-bucket"
    assert settings.storage_timeout_seconds == 15
    assert settings.storage_signed_url_expire_seconds == 900

    async def close() -> None:
        await storage.close()

    asyncio.run(close())


def test_local_and_mock_storage_report_download_url_boundary(tmp_path) -> None:
    local_storage = LocalFileArtifactStorage(tmp_path)
    mock_storage = create_artifact_storage(load_settings("config/config.example.toml"))

    with pytest.raises(StorageError) as local_error:
        local_storage.create_download_url("file.bin")
    with pytest.raises(StorageError) as mock_error:
        mock_storage.create_download_url("file.bin")

    assert local_error.value.code == "STORAGE_DOWNLOAD_UNSUPPORTED"
    assert mock_error.value.code == "STORAGE_DOWNLOAD_UNSUPPORTED"


def test_artifact_service_adds_s3_download_url_from_registry() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        artifact = ArtifactSummary(
            type="reference_image",
            provider="test-image",
            metadata={
                "storage_key": "reference-images/asset/v2/image.png",
                "content_type": "image/png",
                "size_bytes": 11,
                "sha256": "a" * 64,
            },
        )
        task = GenerationTaskRecord(
            project_id=uuid4(),
            artifacts=[artifact],
        )
        await store.create_task(task)
        storage = S3CompatibleArtifactStorage(
            endpoint="http://localhost:9000",
            bucket="artifacts",
            region="us-east-1",
            access_key="access",
            secret_key="secret",
            clock=lambda: datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc),
        )
        result = await ArtifactService(store, storage).get_artifact(artifact.id, 300)
        no_url_result = await ArtifactService(store, storage).get_artifact(
            artifact.id,
            300,
            include_download_url=False,
        )
        await storage.close()

        assert result.task_id == task.id
        assert result.download_url is not None
        assert "X-Amz-Expires=300" in result.download_url
        assert no_url_result.download_url is None

    asyncio.run(exercise())
