"""Novel ingestion, chapter splitting and StoryBible application service."""

from __future__ import annotations

import hashlib
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.domain.models import (
    AssetStatus,
    AssetRecord,
    AssetType,
    ChapterRecord,
    EpisodeRecord,
    EpisodeScriptContent,
    EpisodeScriptDraftRecord,
    EpisodeScriptDraftMergePreviewRequest,
    EpisodeScriptDraftMergePreviewResponse,
    EpisodeScriptDraftUpsertRequest,
    EpisodeScriptRecord,
    EpisodeScriptImpactAsset,
    EpisodeScriptImpactReport,
    EpisodeScriptImpactShot,
    EpisodeScriptVersionCreateRequest,
    EpisodeStatus,
    NovelProjectCreateRequest,
    NovelProjectRecord,
    NovelProjectStatus,
    NovelSourceRecord,
    RightsStatus,
    ShotAssetReference,
    ShotListRecord,
    StoryBibleRecord,
    utc_now,
)
from app.providers.novel_pipeline import NovelDramaGenerationProvider
from app.providers.story_bible import StoryBibleGenerationProvider
from app.repositories.protocol import NovelStore
from app.services.novel_parser import split_chapters


class NovelProjectNotFoundError(Exception):
    """Raised when a novel project does not exist."""


class NovelSourceNotFoundError(Exception):
    """Raised when a novel source does not exist."""


class NovelInputError(Exception):
    """Raised when an uploaded novel cannot be accepted."""


class StoryBibleNotFoundError(Exception):
    """Raised when a project has no generated StoryBible."""


class EpisodeNotFoundError(Exception):
    """Raised when an episode does not exist."""


class EpisodeScriptNotFoundError(Exception):
    """Raised when an episode has no generated script."""


class EpisodeScriptVersionConflictError(Exception):
    """Raised when an edit was based on an outdated script version."""


class EpisodeScriptDraftNotFoundError(Exception):
    """Raised when an episode has no recoverable script draft."""


class EpisodeScriptDraftConflictError(Exception):
    """Raised when an autosave was based on an outdated draft revision."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class EpisodeScriptDraftStaleError(Exception):
    """Raised when a draft is based on a script that is no longer current."""


class ShotListNotFoundError(Exception):
    """Raised when an episode has no generated shot list."""


def _normalize_asset_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    ignored = set("-—_·•:：,，.。!！?？/\\()（）[]【】\"'‘’“”")
    return "".join(
        character
        for character in normalized
        if not character.isspace() and character not in ignored
    )


def _changed_paths(base: Any, candidate: Any, path: str = "") -> set[str]:
    """Return leaf paths changed from base to candidate for conflict reporting."""

    if isinstance(base, dict) and isinstance(candidate, dict):
        paths: set[str] = set()
        for key in sorted(set(base) | set(candidate)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in base or key not in candidate:
                paths.add(child_path)
            else:
                paths.update(_changed_paths(base[key], candidate[key], child_path))
        return paths
    if isinstance(base, list) and isinstance(candidate, list):
        if len(base) != len(candidate):
            return {path or "$"}
        paths: set[str] = set()
        for index, (base_item, candidate_item) in enumerate(zip(base, candidate)):
            paths.update(_changed_paths(base_item, candidate_item, f"{path}[{index}]"))
        return paths
    return set() if base == candidate else {path or "$"}


_MISSING = object()


def _same_value(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    return left == right


def _copy_merge_value(value: Any) -> Any:
    return value if value is _MISSING else deepcopy(value)


def _merge_changed_paths(base: Any, candidate: Any, path: str) -> set[str]:
    """Return safe-to-display paths for a one-sided change."""

    if _same_value(base, candidate):
        return set()
    if isinstance(base, dict) and isinstance(candidate, dict):
        paths: set[str] = set()
        for key in sorted(set(base) | set(candidate)):
            child_path = f"{path}.{key}" if path else str(key)
            paths.update(
                _merge_changed_paths(
                    base.get(key, _MISSING),
                    candidate.get(key, _MISSING),
                    child_path,
                )
            )
        return paths
    if isinstance(base, list) or isinstance(candidate, list):
        return {path or "$"}
    return {path or "$"}


def _three_way_merge(
    base: Any,
    current: Any,
    submitted: Any,
    path: str = "",
) -> tuple[Any, set[str], set[str]]:
    """Conservatively merge JSON-like values without mutating either input."""

    current_changed = not _same_value(base, current)
    submitted_changed = not _same_value(base, submitted)

    if not current_changed and not submitted_changed:
        return _copy_merge_value(base), set(), set()
    if current_changed and not submitted_changed:
        return _copy_merge_value(current), _merge_changed_paths(base, current, path), set()
    if not current_changed and submitted_changed:
        return _copy_merge_value(submitted), _merge_changed_paths(base, submitted, path), set()

    # Lists are intentionally atomic. Index-based merging is unsafe when a user
    # inserts or removes a scene/dialogue on either side.
    if isinstance(base, list) or isinstance(current, list) or isinstance(submitted, list):
        return None, set(), {path or "$"}

    if isinstance(base, dict) and isinstance(current, dict) and isinstance(submitted, dict):
        merged: dict[str, Any] = {}
        mergeable_paths: set[str] = set()
        conflict_paths: set[str] = set()
        for key in sorted(set(base) | set(current) | set(submitted)):
            child_path = f"{path}.{key}" if path else str(key)
            child_base = base.get(key, _MISSING)
            child_current = current.get(key, _MISSING)
            child_submitted = submitted.get(key, _MISSING)
            child_merged, child_mergeable, child_conflicts = _three_way_merge(
                child_base,
                child_current,
                child_submitted,
                child_path,
            )
            if child_merged is not _MISSING and child_merged is not None:
                merged[key] = child_merged
            elif child_merged is _MISSING:
                pass
            mergeable_paths.update(child_mergeable)
            conflict_paths.update(child_conflicts)
        return (
            merged if not conflict_paths else None,
            mergeable_paths,
            conflict_paths,
        )

    if _same_value(current, submitted):
        return _copy_merge_value(current), _merge_changed_paths(base, current, path), set()
    return None, set(), {path or "$"}


class NovelService:
    MAX_SOURCE_BYTES = 5 * 1024 * 1024
    ALLOWED_EXTENSIONS = {".txt": "text/plain", ".md": "text/markdown"}

    def __init__(
        self,
        store: NovelStore,
        story_bible_provider: StoryBibleGenerationProvider,
        novel_pipeline_provider: NovelDramaGenerationProvider,
    ) -> None:
        self._store = store
        self._story_bible_provider = story_bible_provider
        self._novel_pipeline_provider = novel_pipeline_provider

    async def create_project(self, request: NovelProjectCreateRequest) -> NovelProjectRecord:
        project = NovelProjectRecord(
            title=request.title,
            language=request.language,
            target_episode_count=request.target_episode_count,
            target_episode_duration_seconds=request.target_episode_duration_seconds,
            rights_status=request.rights_status,
        )
        return await self._store.create_novel_project(project)

    async def get_project(self, project_id: UUID) -> NovelProjectRecord:
        project = await self._store.get_novel_project(project_id)
        if project is None:
            raise NovelProjectNotFoundError
        return project

    async def list_projects(self) -> list[NovelProjectRecord]:
        return await self._store.list_novel_projects()

    async def import_source(
        self,
        project_id: UUID,
        filename: str,
        raw_content: bytes,
        rights_status: RightsStatus | None = None,
    ) -> tuple[NovelSourceRecord, list[ChapterRecord]]:
        project = await self.get_project(project_id)
        suffix = Path(filename or "").suffix.lower()
        content_type = self.ALLOWED_EXTENSIONS.get(suffix)
        if content_type is None:
            raise NovelInputError("Only .txt and .md novel files are supported")
        if not raw_content:
            raise NovelInputError("Novel file must not be empty")
        if len(raw_content) > self.MAX_SOURCE_BYTES:
            raise NovelInputError("Novel file must not exceed 5 MB")

        try:
            content = raw_content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise NovelInputError("Novel file must use UTF-8 encoding") from exc

        drafts = split_chapters(content)
        if not drafts:
            raise NovelInputError("Novel file does not contain readable text")

        source = NovelSourceRecord(
            project_id=project.id,
            filename=Path(filename).name,
            content_type=content_type,
            size_bytes=len(raw_content),
            checksum=hashlib.sha256(raw_content).hexdigest(),
            content=content.strip(),
            rights_status=rights_status or project.rights_status,
            chapter_count=len(drafts),
        )
        chapters = [
            ChapterRecord(
                source_id=source.id,
                chapter_number=draft.chapter_number,
                title=draft.title,
                content=draft.content,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
            )
            for draft in drafts
        ]
        saved_source, saved_chapters = await self._store.create_novel_source(source, chapters)

        project.source_id = saved_source.id
        project.story_bible_id = None
        project.status = NovelProjectStatus.DRAFT
        project.updated_at = utc_now()
        await self._store.update_novel_project(project)
        return saved_source, saved_chapters

    async def list_chapters(self, project_id: UUID) -> list[ChapterRecord]:
        project = await self.get_project(project_id)
        if project.source_id is None:
            raise NovelSourceNotFoundError
        source = await self._store.get_novel_source(project.source_id)
        if source is None:
            raise NovelSourceNotFoundError
        return await self._store.list_chapters(source.id)

    async def generate_story_bible(self, project_id: UUID) -> StoryBibleRecord:
        project = await self.get_project(project_id)
        if project.source_id is None:
            raise NovelSourceNotFoundError
        source = await self._store.get_novel_source(project.source_id)
        if source is None:
            raise NovelSourceNotFoundError
        chapters = await self._store.list_chapters(source.id)
        project.status = NovelProjectStatus.ANALYZING
        project.updated_at = utc_now()
        await self._store.update_novel_project(project)

        try:
            story_bible = await self._story_bible_provider.generate(project.id, project.title, chapters)
            saved_story_bible = await self._store.save_story_bible(story_bible)
            project.story_bible_id = saved_story_bible.id
            project.status = NovelProjectStatus.READY
            project.updated_at = utc_now()
            await self._store.update_novel_project(project)
            return saved_story_bible
        except Exception:
            project.status = NovelProjectStatus.DRAFT
            project.updated_at = utc_now()
            await self._store.update_novel_project(project)
            raise

    async def get_story_bible(self, project_id: UUID) -> StoryBibleRecord:
        await self.get_project(project_id)
        story_bible = await self._store.get_latest_story_bible(project_id)
        if story_bible is None:
            raise StoryBibleNotFoundError
        return story_bible

    async def plan_episodes(
        self,
        project_id: UUID,
        target_episode_count: int | None = None,
    ) -> list[EpisodeRecord]:
        project = await self.get_project(project_id)
        story_bible = await self.get_story_bible(project_id)
        episode_count = target_episode_count or project.target_episode_count
        episodes = await self._novel_pipeline_provider.plan_episodes(
            project_id=project.id,
            story_bible=story_bible,
            target_episode_count=episode_count,
            target_duration_seconds=project.target_episode_duration_seconds,
        )
        return await self._store.save_episodes(episodes)

    async def list_episodes(self, project_id: UUID) -> list[EpisodeRecord]:
        await self.get_project(project_id)
        return await self._store.list_episodes(project_id)

    async def get_episode(self, episode_id: UUID) -> EpisodeRecord:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError
        return episode

    async def generate_episode_script(self, episode_id: UUID) -> EpisodeScriptRecord:
        episode = await self.get_episode(episode_id)
        story_bible = await self.get_story_bible(episode.project_id)
        script = await self._novel_pipeline_provider.generate_episode_script(
            episode,
            story_bible,
        )
        saved_script = await self._store.save_episode_script(script)
        episode.status = EpisodeStatus.SCRIPTED
        episode.updated_at = utc_now()
        await self._store.update_episode(episode)
        return saved_script

    async def get_episode_script(self, episode_id: UUID) -> EpisodeScriptRecord:
        await self.get_episode(episode_id)
        script = await self._store.get_latest_episode_script(episode_id)
        if script is None:
            raise EpisodeScriptNotFoundError
        return script

    async def get_episode_script_draft(self, episode_id: UUID) -> EpisodeScriptDraftRecord:
        await self.get_episode(episode_id)
        draft = await self._store.get_episode_script_draft(episode_id)
        if draft is None:
            raise EpisodeScriptDraftNotFoundError
        return draft

    async def save_episode_script_draft(
        self,
        episode_id: UUID,
        request: EpisodeScriptDraftUpsertRequest,
    ) -> EpisodeScriptDraftRecord:
        episode = await self.get_episode(episode_id)
        current_script = await self.get_episode_script(episode_id)
        current_draft = await self._store.get_episode_script_draft(episode_id)
        if current_draft is not None and current_draft.base_script_id != current_script.id:
            raise EpisodeScriptDraftStaleError(
                "Draft is based on an older published script version"
            )
        if request.content.episode_number != episode.episode_number:
            raise ValueError("Draft episode_number must match the target episode")

        draft = EpisodeScriptDraftRecord(
            project_id=episode.project_id,
            episode_id=episode.id,
            base_script_id=current_script.id,
            base_script_version=current_script.version,
            content=request.content,
            created_at=current_draft.created_at if current_draft else utc_now(),
        )
        saved = await self._store.save_episode_script_draft(
            draft,
            request.expected_revision,
        )
        if saved is None:
            latest_draft = await self._store.get_episode_script_draft(episode_id)
            base_content = current_script.content.model_dump(mode="json")
            submitted_content = request.content.model_dump(mode="json")
            current_content = (
                latest_draft.content.model_dump(mode="json")
                if latest_draft is not None
                else base_content
            )
            changed_in_current = _changed_paths(base_content, current_content)
            changed_in_submitted = _changed_paths(base_content, submitted_content)
            raise EpisodeScriptDraftConflictError(
                f"Draft revision changed from {request.expected_revision}",
                details={
                    "expected_revision": request.expected_revision,
                    "current_revision": latest_draft.revision if latest_draft else 0,
                    "base_script_id": str(current_script.id),
                    "base_script_version": current_script.version,
                    "conflict_paths": sorted(changed_in_current & changed_in_submitted),
                    "current_draft": (
                        latest_draft.model_dump(mode="json")
                        if latest_draft is not None
                        else None
                    ),
                },
            )
        return saved

    async def preview_episode_script_draft_merge(
        self,
        episode_id: UUID,
        request: EpisodeScriptDraftMergePreviewRequest,
    ) -> EpisodeScriptDraftMergePreviewResponse:
        """Calculate a read-only three-way merge for a local script edit."""

        episode = await self.get_episode(episode_id)
        current_script = await self.get_episode_script(episode_id)
        if (
            request.base_script_id != current_script.id
            or request.base_script_version != current_script.version
        ):
            raise EpisodeScriptDraftStaleError(
                "Merge preview is based on an older published script version"
            )
        if request.content.episode_number != episode.episode_number:
            raise ValueError("Merge preview episode_number must match the target episode")

        current_draft = await self._store.get_episode_script_draft(episode_id)
        if current_draft is not None and current_draft.base_script_id != current_script.id:
            raise EpisodeScriptDraftStaleError(
                "Draft is based on an older published script version"
            )

        base_content = current_script.content.model_dump(mode="json")
        current_content = (
            current_draft.content.model_dump(mode="json")
            if current_draft is not None
            else base_content
        )
        submitted_content = request.content.model_dump(mode="json")
        merged_content, mergeable_paths, conflict_paths = _three_way_merge(
            base_content,
            current_content,
            submitted_content,
        )
        safe_to_apply = not conflict_paths
        return EpisodeScriptDraftMergePreviewResponse(
            episode_id=episode.id,
            base_script_id=current_script.id,
            base_script_version=current_script.version,
            current_revision=current_draft.revision if current_draft is not None else 0,
            safe_to_apply=safe_to_apply,
            mergeable_paths=sorted(mergeable_paths),
            conflict_paths=sorted(conflict_paths),
            merged_content=(
                EpisodeScriptContent.model_validate(merged_content)
                if safe_to_apply and merged_content is not None
                else None
            ),
        )

    async def delete_episode_script_draft(self, episode_id: UUID) -> None:
        await self.get_episode(episode_id)
        await self._store.delete_episode_script_draft(episode_id)

    async def get_episode_script_impact(
        self,
        episode_id: UUID,
    ) -> EpisodeScriptImpactReport:
        """Return a read-only downstream impact report for the latest script."""

        script = await self.get_episode_script(episode_id)
        shot_list = await self._store.get_latest_shot_list(episode_id)
        if shot_list is None:
            return EpisodeScriptImpactReport(
                episode_id=episode_id,
                script_id=script.id,
                script_version=script.version,
                shot_list_state="missing",
                requires_shot_regeneration=False,
                video_generation_gate="blocked",
                requires_asset_review=False,
                affected_shot_count=0,
                unresolved_asset_requirement_count=0,
                asset_binding_warning_count=0,
                recommended_actions=["generate_shot_list"],
            )

        script_is_stale = shot_list.script_id != script.id
        impact_shots: list[EpisodeScriptImpactShot] = []
        asset_usage: dict[UUID, EpisodeScriptImpactAsset] = {}
        unresolved_count = 0
        warning_count = 0
        requires_asset_review = False

        for shot in shot_list.shots:
            unresolved_count += len(shot.unresolved_asset_requirements)
            warning_count += len(shot.asset_binding_warnings)
            has_non_ready_asset = any(
                reference.status != AssetStatus.READY for reference in shot.asset_refs
            )
            if shot.unresolved_asset_requirements:
                asset_gate = "blocked"
            elif shot.asset_binding_warnings or has_non_ready_asset:
                asset_gate = "needs_review"
            else:
                asset_gate = "ready"
            if asset_gate != "ready":
                requires_asset_review = True

            impact_shots.append(
                EpisodeScriptImpactShot(
                    shot_index=shot.shot_index,
                    scene_index=shot.scene_index,
                    requires_regeneration=script_is_stale,
                    asset_gate=asset_gate,
                    unresolved_asset_requirements=shot.unresolved_asset_requirements,
                    asset_binding_warnings=shot.asset_binding_warnings,
                )
            )

            for reference in shot.asset_refs:
                current = asset_usage.get(reference.asset_key)
                if current is None:
                    asset_usage[reference.asset_key] = EpisodeScriptImpactAsset(
                        asset_key=reference.asset_key,
                        asset_type=reference.asset_type,
                        name=reference.name,
                        version=reference.version,
                        status=reference.status,
                        shot_indexes=[shot.shot_index],
                        requires_review=reference.status != AssetStatus.READY,
                    )
                elif shot.shot_index not in current.shot_indexes:
                    current.shot_indexes.append(shot.shot_index)

        recommended_actions: list[
            Literal["generate_shot_list", "regenerate_shot_list", "review_asset_bindings"]
        ] = []
        if script_is_stale:
            recommended_actions.append("regenerate_shot_list")
        if requires_asset_review:
            recommended_actions.append("review_asset_bindings")

        return EpisodeScriptImpactReport(
            episode_id=episode_id,
            script_id=script.id,
            script_version=script.version,
            shot_list_id=shot_list.id,
            shot_list_version=shot_list.version,
            shot_list_script_id=shot_list.script_id,
            shot_list_state="stale" if script_is_stale else "current",
            requires_shot_regeneration=script_is_stale,
            video_generation_gate=(
                "blocked"
                if script_is_stale or requires_asset_review
                else "ready"
            ),
            requires_asset_review=requires_asset_review,
            affected_shot_count=len(impact_shots) if script_is_stale else 0,
            unresolved_asset_requirement_count=unresolved_count,
            asset_binding_warning_count=warning_count,
            shots=impact_shots,
            assets=list(asset_usage.values()),
            recommended_actions=recommended_actions,
        )

    async def save_episode_script_version(
        self,
        episode_id: UUID,
        request: EpisodeScriptVersionCreateRequest,
    ) -> EpisodeScriptRecord:
        episode = await self.get_episode(episode_id)
        current = await self.get_episode_script(episode_id)
        if (
            request.expected_version is not None
            and current.version != request.expected_version
        ):
            raise EpisodeScriptVersionConflictError(
                f"Episode script version changed from {request.expected_version} to {current.version}"
            )
        if request.content.episode_number != episode.episode_number:
            raise ValueError("Script episode_number must match the target episode")
        saved_script = await self._store.save_episode_script(
            EpisodeScriptRecord(
                project_id=episode.project_id,
                episode_id=episode.id,
                content=request.content,
                provider="manual",
                model="script-editor-v1",
                duration_ms=0,
            )
        )
        episode.status = EpisodeStatus.SCRIPTED
        episode.updated_at = utc_now()
        await self._store.update_episode(episode)
        await self._store.delete_episode_script_draft(episode_id)
        return saved_script

    async def generate_shot_list(self, episode_id: UUID) -> ShotListRecord:
        episode = await self.get_episode(episode_id)
        story_bible = await self.get_story_bible(episode.project_id)
        script = await self.get_episode_script(episode_id)
        shot_list = await self._novel_pipeline_provider.generate_shot_list(
            episode,
            script,
            story_bible,
        )
        shot_list = await self._bind_shot_assets(shot_list)
        saved_shot_list = await self._store.save_shot_list(shot_list)
        episode.status = EpisodeStatus.SHOTS_READY
        episode.updated_at = utc_now()
        await self._store.update_episode(episode)
        return saved_shot_list

    async def get_shot_list(self, episode_id: UUID) -> ShotListRecord:
        await self.get_episode(episode_id)
        shot_list = await self._store.get_latest_shot_list(episode_id)
        if shot_list is None:
            raise ShotListNotFoundError
        return shot_list

    async def _bind_shot_assets(self, shot_list: ShotListRecord) -> ShotListRecord:
        """Resolve provider asset names to the latest stable asset identities."""

        assets = await self._store.list_assets(shot_list.project_id)
        by_term: dict[tuple[AssetType, str], list[tuple[AssetRecord, str]]] = {}
        for asset in assets:
            by_term.setdefault(
                (asset.asset_type, _normalize_asset_term(asset.name)), []
            ).append((asset, "name"))
            for alias in asset.aliases:
                by_term.setdefault(
                    (asset.asset_type, _normalize_asset_term(alias)), []
                ).append((asset, "alias"))
        bound_shots = []
        for shot in shot_list.shots:
            references: list[ShotAssetReference] = []
            unresolved: list[str] = []
            warnings: list[str] = []
            seen_keys: set[tuple[AssetType, UUID]] = set()
            for requirement in shot.asset_requirements:
                asset_type_name, separator, asset_name = requirement.partition(":")
                asset_type: AssetType | None = None
                if separator:
                    try:
                        asset_type = AssetType(asset_type_name)
                    except ValueError:
                        unresolved.append(requirement)
                        continue
                    asset_name = asset_name.strip()
                    candidates = by_term.get(
                        (asset_type, _normalize_asset_term(asset_name)), []
                    )
                else:
                    asset_name = requirement.strip()
                    candidates = [
                        candidate
                        for (candidate_type, candidate_term), values in by_term.items()
                        if candidate_term == _normalize_asset_term(asset_name)
                        for candidate in values
                    ]
                candidates_by_key: dict[UUID, tuple[AssetRecord, str]] = {}
                for candidate, match_kind in candidates:
                    current = candidates_by_key.get(candidate.asset_key)
                    if current is None or match_kind == "name":
                        candidates_by_key[candidate.asset_key] = (candidate, match_kind)
                if not candidates_by_key:
                    unresolved.append(requirement)
                    continue
                if len(candidates_by_key) > 1:
                    warnings.append(f"{requirement}:ambiguous_asset_match")
                    continue
                asset, match_kind = next(iter(candidates_by_key.values()))
                if asset.status != AssetStatus.READY:
                    warnings.append(
                        f"{requirement}:asset_status={asset.status.value}"
                    )
                    continue
                key = (asset.asset_type, asset.asset_key)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                references.append(
                    ShotAssetReference(
                        asset_key=asset.asset_key,
                        asset_type=asset.asset_type,
                        name=asset.name,
                        version=asset.version,
                        status=asset.status,
                        match_kind=match_kind,
                        matched_text=asset_name,
                    )
                )
            bound_shots.append(
                shot.model_copy(
                    update={
                        "asset_refs": references,
                        "unresolved_asset_requirements": unresolved,
                        "asset_binding_warnings": warnings,
                    }
                )
            )
        return shot_list.model_copy(update={"shots": bound_shots})
