"""Storage contract for binary video-production artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    uri: str
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: str


class StorageError(Exception):
    """Stable error raised by an Artifact storage adapter."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_storage_key(storage_key: str) -> str:
    """Return a normalized relative key or raise for unsafe paths."""

    path = PurePosixPath(storage_key)
    if not storage_key or "\x00" in storage_key or path.is_absolute() or ".." in path.parts:
        raise ValueError("Artifact storage key must be a safe relative path")
    return str(path)


class ArtifactStorage(Protocol):
    async def put_bytes(
        self,
        storage_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredArtifact:
        ...

    async def get_bytes(self, storage_key: str) -> bytes:
        """Read an existing artifact by its safe storage key."""
        ...

    def create_download_url(
        self,
        storage_key: str,
        expires_in_seconds: int | None = None,
    ) -> str:
        ...

    async def close(self) -> None:
        ...
