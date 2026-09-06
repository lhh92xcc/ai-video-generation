"""Artifact Registry queries and storage-backed download URL assembly."""

from __future__ import annotations

from uuid import UUID

from app.domain.models import ArtifactRecord
from app.repositories.protocol import ProjectTaskStore
from app.storage.protocol import ArtifactStorage, StorageError


class ArtifactNotFoundError(Exception):
    """Raised when an Artifact Registry record does not exist."""


class ArtifactContentUnavailableError(Exception):
    """Raised when an Artifact Registry record has no readable binary content."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(code, message)


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

    async def read_content(self, artifact: ArtifactRecord) -> tuple[bytes, str]:
        """Read an Artifact through the configured storage adapter.

        Local and Mock storage intentionally do not expose their internal URI as
        a browser URL.  The API content route uses this method after performing
        project authorization, keeping storage paths and credentials private.
        """

        storage_key = artifact.metadata.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key.strip():
            raise ArtifactContentUnavailableError(
                "ARTIFACT_CONTENT_UNAVAILABLE",
                "This Artifact does not contain readable binary content",
            )

        try:
            content = await self._artifact_storage.get_bytes(storage_key)
        except ValueError as exc:
            raise ArtifactContentUnavailableError(
                "ARTIFACT_CONTENT_UNAVAILABLE",
                "This Artifact has an invalid storage reference",
            ) from exc

        return content, self._content_type(artifact)

    @staticmethod
    def _content_type(artifact: ArtifactRecord) -> str:
        configured = artifact.metadata.get("content_type")
        if isinstance(configured, str) and configured.strip() and not any(
            character in configured for character in "\r\n"
        ):
            return configured.strip()

        fallback_types = {
            "reference_image": "image/png",
            "video_clip": "video/mp4",
            "lip_synced_video": "video/mp4",
            "rendered_video": "video/mp4",
            "audio_narration": "audio/mpeg",
            "audio_bgm": "audio/mpeg",
            "subtitle_srt": "application/x-subrip; charset=utf-8",
            "script_json": "application/json; charset=utf-8",
            "story_bible_json": "application/json; charset=utf-8",
            "episode_outline_json": "application/json; charset=utf-8",
            "episode_script_json": "application/json; charset=utf-8",
            "shot_list_json": "application/json; charset=utf-8",
        }
        return fallback_types.get(artifact.type, "application/octet-stream")
