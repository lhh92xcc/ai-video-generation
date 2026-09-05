"""Project-level reusable voice profiles for narration and dialogue."""

from __future__ import annotations

from uuid import UUID

from app.domain.models import (
    AssetStatus,
    AssetType,
    VoiceAssetCreateRequest,
    VoiceAssetRecord,
    VoiceAssetStatus,
    utc_now,
)
from app.repositories.protocol import NovelStore
from app.services.asset_service import AssetNotFoundError
from app.services.novel_service import NovelProjectNotFoundError


class VoiceAssetInputError(Exception):
    """Raised when a voice profile cannot be bound to the requested project."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VoiceAssetNotFoundError(Exception):
    """Raised when a voice profile does not exist."""


class VoiceAssetService:
    def __init__(self, store: NovelStore) -> None:
        self._store = store

    async def create(
        self,
        project_id: UUID,
        request: VoiceAssetCreateRequest,
    ) -> VoiceAssetRecord:
        await self._require_project(project_id)
        character_asset_key: UUID | None = None
        if request.character_asset_id is not None:
            asset = await self._store.get_asset(request.character_asset_id)
            if asset is None:
                raise AssetNotFoundError
            if asset.project_id != project_id:
                raise VoiceAssetInputError(
                    "VOICE_ASSET_PROJECT_MISMATCH",
                    "The character asset does not belong to this project",
                )
            if asset.asset_type != AssetType.CHARACTER:
                raise VoiceAssetInputError(
                    "VOICE_ASSET_CHARACTER_REQUIRED",
                    "A voice asset can only be bound to a character asset",
                )
            if asset.status == AssetStatus.ARCHIVED:
                raise VoiceAssetInputError(
                    "VOICE_ASSET_CHARACTER_ARCHIVED",
                    "An archived character asset cannot receive a voice profile",
                )
            character_asset_key = asset.asset_key

        return await self._store.save_voice_asset(
            VoiceAssetRecord(
                project_id=project_id,
                character_asset_key=character_asset_key,
                label=request.label,
                language=request.language,
                provider=request.provider,
                model=request.model,
                voice=request.voice,
                rate=request.rate,
                volume=request.volume,
                style=request.style,
                status=request.status,
                metadata={
                    "character_asset_id": (
                        str(request.character_asset_id)
                        if request.character_asset_id is not None
                        else None
                    ),
                },
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    async def list(self, project_id: UUID) -> list[VoiceAssetRecord]:
        await self._require_project(project_id)
        return await self._store.list_voice_assets(project_id)

    async def get(self, voice_asset_id: UUID) -> VoiceAssetRecord:
        voice_asset = await self._store.get_voice_asset(voice_asset_id)
        if voice_asset is None:
            raise VoiceAssetNotFoundError
        return voice_asset

    async def require_for_project(
        self,
        project_id: UUID,
        voice_asset_id: UUID,
    ) -> VoiceAssetRecord:
        voice_asset = await self.get(voice_asset_id)
        if voice_asset.project_id != project_id:
            raise VoiceAssetInputError(
                "VOICE_ASSET_PROJECT_MISMATCH",
                "The voice asset does not belong to this project",
            )
        if voice_asset.status != VoiceAssetStatus.READY:
            raise VoiceAssetInputError(
                "VOICE_ASSET_NOT_READY",
                f"Voice asset {voice_asset.id} is {voice_asset.status.value}",
            )
        return voice_asset

    async def find_ready_by_speaker(
        self,
        project_id: UUID,
        speaker: str,
    ) -> VoiceAssetRecord | None:
        normalized = speaker.strip()
        if not normalized:
            return None
        assets = await self.list(project_id)
        ready = [asset for asset in assets if asset.status == VoiceAssetStatus.READY]
        exact_label = [asset for asset in ready if asset.label == normalized]
        if len(exact_label) == 1:
            return exact_label[0]

        characters = await self._store.list_assets(project_id, AssetType.CHARACTER)
        character_keys = {
            asset.asset_key
            for asset in characters
            if asset.name == normalized or normalized in asset.aliases
        }
        bound = [asset for asset in ready if asset.character_asset_key in character_keys]
        return bound[0] if len(bound) == 1 else None

    async def _require_project(self, project_id: UUID) -> None:
        if await self._store.get_novel_project(project_id) is None:
            raise NovelProjectNotFoundError
