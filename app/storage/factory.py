"""Build the configured Artifact storage adapter."""

from __future__ import annotations

from app.config import Settings
from app.storage.local import LocalFileArtifactStorage, MockArtifactStorage
from app.storage.protocol import ArtifactStorage
from app.storage.s3_compatible import S3CompatibleArtifactStorage


def create_artifact_storage(settings: Settings) -> ArtifactStorage:
    if settings.storage_provider == "local":
        return LocalFileArtifactStorage(settings.storage_base_path, settings.storage_uri_prefix)
    if settings.storage_provider == "mock":
        return MockArtifactStorage()
    if settings.storage_provider in {"s3", "s3_compatible", "minio"}:
        return S3CompatibleArtifactStorage(
            endpoint=settings.storage_endpoint,
            bucket=settings.storage_bucket,
            region=settings.storage_region,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            public_endpoint=settings.storage_public_endpoint,
            timeout_seconds=settings.storage_timeout_seconds,
            signed_url_expire_seconds=settings.storage_signed_url_expire_seconds,
        )
    raise ValueError(
        f"Unsupported storage provider: {settings.storage_provider}. "
        "Use local, mock, s3_compatible, minio, or s3."
    )
