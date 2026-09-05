"""OpenAI-compatible long-context providers for the novel pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.domain.models import (
    ChapterRecord,
    EpisodeOutlineContent,
    EpisodeRecord,
    EpisodeScriptContent,
    EpisodeScriptRecord,
    EpisodeStatus,
    ShotContent,
    ShotListRecord,
    StoryBibleContent,
    StoryBibleRecord,
)
from app.providers.errors import TextProviderError
from app.providers.novel_pipeline import NovelDramaGenerationProvider
from app.providers.story_bible import StoryBibleGenerationProvider


class _OpenAICompatibleJSONClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> Any:
        if not self.api_key:
            raise TextProviderError(
                "PROVIDER_AUTH_FAILED",
                "AI_VIDEO_LLM_API_KEY is required for the OpenAI-compatible provider",
            )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TextProviderError("PROVIDER_TIMEOUT", "LLM request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise TextProviderError(
                self._status_error_code(exc.response.status_code),
                f"LLM request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise TextProviderError("PROVIDER_HTTP_ERROR", "LLM request failed") from exc

        try:
            response_json: dict[str, Any] = response.json()
            content = response_json["choices"][0]["message"]["content"]
            return self._parse_json_content(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise TextProviderError("PROVIDER_INVALID_RESPONSE", "LLM response was not valid JSON") from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _parse_json_content(content: Any) -> Any:
        if isinstance(content, (dict, list)):
            return content
        if not isinstance(content, str):
            raise TypeError("LLM content must be a JSON string")
        normalized = content.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            normalized = "\n".join(lines[1:-1]).strip()
        return json.loads(normalized)

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "PROVIDER_UNAVAILABLE"
        return "PROVIDER_HTTP_ERROR"


class OpenAICompatibleStoryBibleProvider(StoryBibleGenerationProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float,
        timeout_seconds: int,
        prompt_path: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = _OpenAICompatibleJSONClient(
            base_url, api_key, model, temperature, timeout_seconds, client
        )
        self._model = model
        self._prompt_path = prompt_path or Path(__file__).resolve().parents[2] / "prompts" / "story-bible-generation.txt"

    async def generate(
        self,
        project_id: UUID,
        project_title: str,
        chapters: list[ChapterRecord],
    ) -> StoryBibleRecord:
        if not chapters:
            raise TextProviderError("NOVEL_SOURCE_REQUIRED", "At least one chapter is required")
        started = monotonic()
        raw = await self._client.complete(
            self._prompt_path.read_text(encoding="utf-8"),
            {
                "project_title": project_title,
                "chapters": [chapter.model_dump(mode="json") for chapter in chapters],
            },
        )
        try:
            content = StoryBibleContent.model_validate(raw)
        except ValidationError as exc:
            raise TextProviderError(
                "STORY_BIBLE_INVALID_OUTPUT",
                "LLM output failed StoryBible schema validation",
            ) from exc
        return StoryBibleRecord(
            project_id=project_id,
            source_id=chapters[0].source_id,
            content=content,
            provider="openai_compatible",
            model=self._model,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
        )

    async def close(self) -> None:
        await self._client.close()


class OpenAICompatibleNovelDramaProvider(NovelDramaGenerationProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float,
        timeout_seconds: int,
        prompt_directory: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = _OpenAICompatibleJSONClient(
            base_url, api_key, model, temperature, timeout_seconds, client
        )
        self._model = model
        self._prompt_directory = prompt_directory or Path(__file__).resolve().parents[2] / "prompts"

    async def plan_episodes(
        self,
        project_id: UUID,
        story_bible: StoryBibleRecord,
        target_episode_count: int,
        target_duration_seconds: int,
    ) -> list[EpisodeRecord]:
        started = monotonic()
        raw = await self._complete(
            "episode-planning.txt",
            {
                "story_bible": story_bible.content.model_dump(mode="json"),
                "target_episode_count": target_episode_count,
                "target_duration_seconds": target_duration_seconds,
            },
        )
        outlines = self._validate_list(raw, EpisodeOutlineContent, "EPISODE_PLAN_INVALID_OUTPUT")
        if len(outlines) != target_episode_count:
            raise TextProviderError(
                "EPISODE_PLAN_INVALID_OUTPUT",
                "LLM episode count does not match target_episode_count",
            )
        if [outline.episode_number for outline in outlines] != list(range(1, target_episode_count + 1)):
            raise TextProviderError(
                "EPISODE_PLAN_INVALID_OUTPUT",
                "LLM episode numbers are not sequential",
            )
        duration_ms = max(1, int((monotonic() - started) * 1000))
        return [
            EpisodeRecord(
                project_id=project_id,
                story_bible_id=story_bible.id,
                episode_number=outline.episode_number,
                status=EpisodeStatus.PLANNED,
                outline=outline,
                provider="openai_compatible",
                model=self._model,
                duration_ms=duration_ms,
            )
            for outline in outlines
        ]

    async def generate_episode_script(
        self,
        episode: EpisodeRecord,
        story_bible: StoryBibleRecord,
    ) -> EpisodeScriptRecord:
        started = monotonic()
        raw = await self._complete(
            "scene-script-generation.txt",
            {
                "episode_outline": episode.outline.model_dump(mode="json"),
                "story_bible": story_bible.content.model_dump(mode="json"),
                "target_duration_seconds": episode.outline.target_duration_seconds,
            },
        )
        try:
            content = self._parse_episode_script(raw, episode.episode_number)
        except TextProviderError as validation_error:
            repaired_raw = await self._complete(
                "scene-script-generation-repair.txt",
                {
                    "episode_outline": episode.outline.model_dump(mode="json"),
                    "story_bible": story_bible.content.model_dump(mode="json"),
                    "target_duration_seconds": episode.outline.target_duration_seconds,
                    "validation_error": validation_error.message,
                    "invalid_output": raw,
                },
            )
            try:
                content = self._parse_episode_script(repaired_raw, episode.episode_number)
            except TextProviderError as repair_error:
                raise TextProviderError(
                    "EPISODE_SCRIPT_INVALID_OUTPUT",
                    "LLM output failed EpisodeScript schema validation after one repair",
                ) from repair_error
        return EpisodeScriptRecord(
            project_id=episode.project_id,
            episode_id=episode.id,
            content=content,
            provider="openai_compatible",
            model=self._model,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
        )

    @staticmethod
    def _parse_episode_script(raw: Any, episode_number: int) -> EpisodeScriptContent:
        try:
            content = EpisodeScriptContent.model_validate(
                OpenAICompatibleNovelDramaProvider._unwrap(raw, "script")
            )
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise TextProviderError(
                "EPISODE_SCRIPT_INVALID_OUTPUT",
                f"LLM output failed EpisodeScript schema validation: {details}",
            ) from exc
        if content.episode_number != episode_number:
            raise TextProviderError(
                "EPISODE_SCRIPT_INVALID_OUTPUT",
                "LLM episode number does not match the requested episode",
            )
        return content

    async def generate_shot_list(
        self,
        episode: EpisodeRecord,
        script: EpisodeScriptRecord,
        story_bible: StoryBibleRecord,
    ) -> ShotListRecord:
        started = monotonic()
        raw = await self._complete(
            "shot-list-generation.txt",
            {
                "episode_script": script.content.model_dump(mode="json"),
                "story_bible": story_bible.content.model_dump(mode="json"),
                "aspect_ratio": "9:16",
            },
        )
        try:
            shots = self._parse_shot_list(raw)
        except TextProviderError as validation_error:
            repaired_raw = await self._complete(
                "shot-list-generation-repair.txt",
                {
                    "episode_script": script.content.model_dump(mode="json"),
                    "story_bible": story_bible.content.model_dump(mode="json"),
                    "aspect_ratio": "9:16",
                    "validation_error": validation_error.message,
                    "invalid_output": raw,
                },
            )
            try:
                shots = self._parse_shot_list(repaired_raw)
            except TextProviderError as repair_error:
                raise TextProviderError(
                    "SHOT_LIST_INVALID_OUTPUT",
                    "LLM output failed ShotList schema validation after one repair: "
                    f"{repair_error.message}",
                ) from repair_error
        return ShotListRecord(
            project_id=episode.project_id,
            episode_id=episode.id,
            script_id=script.id,
            shots=shots,
            provider="openai_compatible",
            model=self._model,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
        )

    async def close(self) -> None:
        await self._client.close()

    async def _complete(self, prompt_filename: str, user_payload: dict[str, Any]) -> Any:
        return await self._client.complete(
            (self._prompt_directory / prompt_filename).read_text(encoding="utf-8"),
            user_payload,
        )

    @staticmethod
    def _unwrap(raw: Any, key: str) -> Any:
        if isinstance(raw, dict) and key in raw:
            return raw[key]
        return raw

    @staticmethod
    def _validate_list(raw: Any, model: Any, error_code: str) -> list[Any]:
        raw = OpenAICompatibleNovelDramaProvider._unwrap(raw, "items")
        if not isinstance(raw, list):
            raise TextProviderError(error_code, "LLM output must be a JSON array")
        try:
            return [model.model_validate(item) for item in raw]
        except ValidationError as exc:
            raise TextProviderError(error_code, "LLM output failed schema validation") from exc

    @staticmethod
    def _parse_shot_list(raw: Any) -> list[ShotContent]:
        raw = OpenAICompatibleNovelDramaProvider._unwrap(raw, "shots")
        if not isinstance(raw, list):
            raise TextProviderError("SHOT_LIST_INVALID_OUTPUT", "LLM output must contain a shots array")
        try:
            shots = [ShotContent.model_validate(item) for item in raw]
        except ValidationError as exc:
            details = "; ".join(
                f"shots[{'.'.join(str(item) for item in error['loc'])}]: {error['msg']}"
                for error in exc.errors()
            )
            raise TextProviderError(
                "SHOT_LIST_INVALID_OUTPUT",
                f"LLM output failed ShotContent schema validation: {details}",
            ) from exc
        if not shots:
            raise TextProviderError("SHOT_LIST_INVALID_OUTPUT", "LLM returned an empty shot list")
        if [shot.shot_index for shot in shots] != list(range(1, len(shots) + 1)):
            raise TextProviderError("SHOT_LIST_INVALID_OUTPUT", "LLM shot indexes are not sequential")
        return [
            shot.model_copy(
                update={
                    "characters": [
                        OpenAICompatibleNovelDramaProvider._strip_shot_prefix(
                            character,
                            "character",
                        )
                        for character in shot.characters
                    ],
                    "location": OpenAICompatibleNovelDramaProvider._strip_shot_prefix(
                        shot.location,
                        "location",
                    ),
                }
            )
            for shot in shots
        ]

    @staticmethod
    def _strip_shot_prefix(value: str, expected_prefix: str) -> str:
        prefix = f"{expected_prefix}:"
        return value[len(prefix):].strip() if value.startswith(prefix) else value
