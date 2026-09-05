"""Character, location and prop asset library application service."""

from __future__ import annotations

from uuid import UUID

from app.domain.models import (
    AssetCreateRequest,
    AssetBatchReviewRequest,
    AssetRecord,
    AssetReviewRecord,
    AssetReviewRequest,
    AssetReviewResult,
    AssetStatus,
    AssetType,
    AssetVersionCreateRequest,
    CharacterAssetContent,
    LocationAssetContent,
    PropAssetContent,
    utc_now,
)
from app.repositories.protocol import NovelStore
from app.services.novel_service import (
    NovelService,
    StoryBibleNotFoundError,
)


class AssetNotFoundError(Exception):
    """Raised when an asset does not exist."""


class AssetInputError(Exception):
    """Raised when an asset payload does not match its asset type."""


class AssetReviewError(Exception):
    """Raised when an asset review transition is not allowed."""


class AssetVersionConflictError(Exception):
    """Raised when an edit was based on an outdated asset version."""


class AssetService:
    _REVIEW_TRANSITIONS = {
        AssetStatus.DRAFT: {AssetStatus.NEEDS_REVIEW, AssetStatus.ARCHIVED},
        AssetStatus.NEEDS_REVIEW: {
            AssetStatus.DRAFT,
            AssetStatus.READY,
            AssetStatus.ARCHIVED,
        },
        AssetStatus.READY: {AssetStatus.NEEDS_REVIEW, AssetStatus.ARCHIVED},
        AssetStatus.ARCHIVED: {AssetStatus.DRAFT, AssetStatus.NEEDS_REVIEW},
    }

    def __init__(self, store: NovelStore, novel_service: NovelService) -> None:
        self._store = store
        self._novel_service = novel_service

    async def create_asset(
        self,
        project_id: UUID,
        request: AssetCreateRequest,
    ) -> AssetRecord:
        await self._novel_service.get_project(project_id)
        story_bible = await self._novel_service.get_story_bible(project_id)
        self._validate_content(request.asset_type, request.content)
        return await self._store.save_asset_version(
            AssetRecord(
                project_id=project_id,
                story_bible_id=story_bible.id,
                asset_type=request.asset_type,
                name=request.name,
                aliases=request.aliases,
                status=request.status,
                content=request.content,
                source_chapter_numbers=request.source_chapter_numbers,
                provider="manual",
                model="manual-v1",
                duration_ms=0,
            )
        )

    async def sync_story_bible_assets(self, project_id: UUID) -> list[AssetRecord]:
        story_bible = await self._novel_service.get_story_bible(project_id)
        source_chapters = story_bible.content.source_chapter_numbers
        assets: list[AssetRecord] = []
        for character in story_bible.content.characters:
            assets.append(
                await self._store.save_asset_version(
                    AssetRecord(
                        project_id=project_id,
                        story_bible_id=story_bible.id,
                        asset_type=AssetType.CHARACTER,
                        name=character.name,
                        status=AssetStatus.NEEDS_REVIEW,
                        content=CharacterAssetContent(
                            role=character.role,
                            traits=character.traits,
                            appearance=character.appearance,
                            relationships=character.relationships,
                            voice_notes=character.voice_notes,
                        ),
                        source_chapter_numbers=source_chapters,
                        provider="story_bible_sync",
                        model=story_bible.model,
                        duration_ms=0,
                    )
                )
            )
        for location in story_bible.content.locations:
            assets.append(
                await self._store.save_asset_version(
                    AssetRecord(
                        project_id=project_id,
                        story_bible_id=story_bible.id,
                        asset_type=AssetType.LOCATION,
                        name=location.name,
                        status=AssetStatus.NEEDS_REVIEW,
                        content=LocationAssetContent(
                            description=location.description,
                            visual_keywords=location.visual_keywords,
                        ),
                        source_chapter_numbers=source_chapters,
                        provider="story_bible_sync",
                        model=story_bible.model,
                        duration_ms=0,
                    )
                )
            )
        for prop in story_bible.content.props:
            assets.append(
                await self._store.save_asset_version(
                    AssetRecord(
                        project_id=project_id,
                        story_bible_id=story_bible.id,
                        asset_type=AssetType.PROP,
                        name=prop.name,
                        status=AssetStatus.NEEDS_REVIEW,
                        content=PropAssetContent(
                            purpose=prop.purpose,
                            description=prop.description,
                            visual_keywords=prop.visual_keywords,
                            continuity_notes=prop.continuity_notes,
                        ),
                        source_chapter_numbers=source_chapters,
                        provider="story_bible_sync",
                        model=story_bible.model,
                        duration_ms=0,
                    )
                )
            )
        return assets

    async def list_assets(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
    ) -> list[AssetRecord]:
        await self._novel_service.get_project(project_id)
        return await self._store.list_assets(project_id, asset_type)

    async def get_asset(self, asset_id: UUID) -> AssetRecord:
        asset = await self._store.get_asset(asset_id)
        if asset is None:
            raise AssetNotFoundError
        return asset

    async def create_asset_version(
        self,
        asset_id: UUID,
        request: AssetVersionCreateRequest,
    ) -> AssetRecord:
        asset = await self.get_asset(asset_id)
        latest_asset = next(
            (
                item
                for item in await self._store.list_assets(asset.project_id, asset.asset_type)
                if item.asset_key == asset.asset_key
            ),
            asset,
        )
        if (
            request.expected_version is not None
            and latest_asset.version != request.expected_version
        ):
            raise AssetVersionConflictError(
                f"Asset version changed from {request.expected_version} to {latest_asset.version}"
            )
        self._validate_content(asset.asset_type, request.content)
        return await self._store.save_asset_version(
            AssetRecord(
                project_id=latest_asset.project_id,
                story_bible_id=latest_asset.story_bible_id,
                asset_type=asset.asset_type,
                name=latest_asset.name,
                aliases=request.aliases if request.aliases is not None else latest_asset.aliases,
                status=request.status,
                content=request.content,
                source_chapter_numbers=(
                    request.source_chapter_numbers
                    if request.source_chapter_numbers
                    else latest_asset.source_chapter_numbers
                ),
                provider="manual",
                model="manual-v1",
                duration_ms=0,
                created_at=latest_asset.created_at,
                updated_at=utc_now(),
            )
        )

    async def review_asset(
        self,
        asset_id: UUID,
        request: AssetReviewRequest,
    ) -> AssetReviewResult:
        asset = await self.get_asset(asset_id)
        latest_asset = next(
            (
                item
                for item in await self._store.list_assets(asset.project_id, asset.asset_type)
                if item.asset_key == asset.asset_key
            ),
            None,
        )
        if latest_asset is not None:
            asset = latest_asset
        if request.status == asset.status:
            raise AssetReviewError("Review status must change the current asset status")
        if request.status not in self._REVIEW_TRANSITIONS[asset.status]:
            raise AssetReviewError(
                f"Cannot change asset status from {asset.status.value} to {request.status.value}"
            )

        reviewed_asset = await self._store.save_asset_version(
            AssetRecord(
                asset_key=asset.asset_key,
                project_id=asset.project_id,
                story_bible_id=asset.story_bible_id,
                asset_type=asset.asset_type,
                name=asset.name,
                aliases=asset.aliases,
                status=request.status,
                content=asset.content,
                source_chapter_numbers=asset.source_chapter_numbers,
                provider="manual_review",
                model="asset-review-v1",
                duration_ms=0,
                created_at=asset.created_at,
                updated_at=utc_now(),
            )
        )
        review = await self._store.save_asset_review(
            AssetReviewRecord(
                asset_key=reviewed_asset.asset_key,
                asset_id=reviewed_asset.id,
                version=reviewed_asset.version,
                from_status=asset.status,
                to_status=reviewed_asset.status,
                reviewer=request.reviewer,
                comment=request.comment,
            )
        )
        return AssetReviewResult(asset=reviewed_asset, review=review)

    async def review_assets(
        self,
        project_id: UUID,
        request: AssetBatchReviewRequest,
    ) -> list[AssetReviewResult]:
        """Approve selected or all reviewable assets in one explicit operation."""

        await self._novel_service.get_project(project_id)
        assets = await self._store.list_assets(project_id)
        selected_ids = set(request.asset_ids or [])
        if request.asset_ids is None:
            targets = [asset for asset in assets if asset.status == AssetStatus.NEEDS_REVIEW]
        else:
            targets = [asset for asset in assets if asset.id in selected_ids]
        return [
            await self.review_asset(
                asset.id,
                AssetReviewRequest(
                    status=request.status,
                    reviewer=request.reviewer,
                    comment=request.comment,
                ),
            )
            for asset in targets
            if asset.status == AssetStatus.NEEDS_REVIEW
        ]

    async def list_asset_reviews(self, asset_id: UUID) -> list[AssetReviewRecord]:
        asset = await self.get_asset(asset_id)
        return await self._store.list_asset_reviews(asset.asset_key)

    @staticmethod
    def _validate_content(asset_type: AssetType, content: object) -> None:
        expected = {
            AssetType.CHARACTER: CharacterAssetContent,
            AssetType.LOCATION: LocationAssetContent,
            AssetType.PROP: PropAssetContent,
        }[asset_type]
        if not isinstance(content, expected):
            raise AssetInputError(
                f"Asset type {asset_type.value} requires {expected.__name__} content"
            )
