"""Atomic local-file Artifact storage for development and small samples."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from app.storage.protocol import StorageError, StoredArtifact, validate_storage_key


class LocalFileArtifactStorage:
    def __init__(self, root_dir: str | Path, uri_prefix: str = "local://") -> None:
        self._root_dir = Path(root_dir)
        self._uri_prefix = uri_prefix if uri_prefix.endswith("/") else f"{uri_prefix}/"

    async def put_bytes(
        self,
        storage_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredArtifact:
        safe_key = self._validate_key(storage_key)
        await asyncio.to_thread(self._write_atomically, safe_key, content)
        return StoredArtifact(
            uri=f"{self._uri_prefix}{safe_key}",
            storage_key=safe_key,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def get_bytes(self, storage_key: str) -> bytes:
        safe_key = self._validate_key(storage_key)
        target = self._root_dir / safe_key
        try:
            return await asyncio.to_thread(target.read_bytes)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise StorageError(
                "STORAGE_NOT_FOUND",
                "Artifact was not found in local storage",
            ) from exc
        except OSError as exc:
            raise StorageError(
                "STORAGE_DOWNLOAD_FAILED",
                "Local Artifact storage could not read the artifact",
            ) from exc

    async def close(self) -> None:
        return None

    def create_download_url(
        self,
        storage_key: str,
        expires_in_seconds: int | None = None,
    ) -> str:
        validate_storage_key(storage_key)
        raise StorageError(
            "STORAGE_DOWNLOAD_UNSUPPORTED",
            "Local Artifact storage does not provide a download URL",
        )

    def _write_atomically(self, storage_key: str, content: bytes) -> None:
        target = self._root_dir / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate_key(storage_key: str) -> str:
        return validate_storage_key(storage_key)


class MockArtifactStorage:
    """Return deterministic metadata without writing files."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def put_bytes(
        self,
        storage_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredArtifact:
        safe_key = validate_storage_key(storage_key)
        self._objects[safe_key] = content
        return StoredArtifact(
            uri=f"mock://artifacts/{safe_key}",
            storage_key=safe_key,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def get_bytes(self, storage_key: str) -> bytes:
        safe_key = validate_storage_key(storage_key)
        try:
            return self._objects[safe_key]
        except KeyError as exc:
            raise StorageError(
                "STORAGE_NOT_FOUND",
                "Artifact was not found in mock storage",
            ) from exc

    async def close(self) -> None:
        return None

    def create_download_url(
        self,
        storage_key: str,
        expires_in_seconds: int | None = None,
    ) -> str:
        validate_storage_key(storage_key)
        raise StorageError(
            "STORAGE_DOWNLOAD_UNSUPPORTED",
            "Mock Artifact storage does not provide a download URL",
        )
