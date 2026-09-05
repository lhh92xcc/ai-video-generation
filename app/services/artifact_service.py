"""Artifact Registry queries and storage-backed download URL assembly."""

from __future__ import annotations

from uuid import UUID

from app.domain.models import ArtifactRecord
from app.repositories.protocol import ProjectTaskStore
from app.storage.protocol import ArtifactStorage, StorageError


class ArtifactNotFoundError(Exception):
    """Raised when an Artifact Registry record does not exist."""


class ArtifactService:
    def __init__(
        self,
        store: ProjectTaskStore,
        artifact_storage: ArtifactStorage,
    ) -> None:
        self._store = store
        self._artifact_storage = artifact_storage

    async def get_artifact(
        self,
        artifact_id: UUID,
        expires_in_seconds: int | None = None,
        include_download_url: bool = True,
    ) -> ArtifactRecord:
        artifact = await self._store.get_artifact(artifact_id)
        if artifact is None:
            raise ArtifactNotFoundError

        if include_download_url:
            self.attach_download_url(artifact, expires_in_seconds)
        return artifact

    async def list_artifacts(
        self,
        project_id: UUID | None = None,
        artifact_type: str | None = None,
        limit: int = 100,
        expires_in_seconds: int | None = None,
        include_download_url: bool = True,
    ) -> list[ArtifactRecord]:
        artifacts = await self._store.list_artifacts(project_id, artifact_type, limit)
        if include_download_url:
            for artifact in artifacts:
                self.attach_download_url(artifact, expires_in_seconds)
        return artifacts

    def attach_download_url(
        self,
        artifact: ArtifactRecord,
        expires_in_seconds: int | None = None,
    ) -> None:
        """Attach a temporary URL after the caller has completed authorization."""

        storage_key = artifact.metadata.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key:
            return
        try:
            artifact.download_url = self._artifact_storage.create_download_url(
                storage_key,
                expires_in_seconds,
            )
        except StorageError as exc:
            if exc.code != "STORAGE_DOWNLOAD_UNSUPPORTED":
                raise
