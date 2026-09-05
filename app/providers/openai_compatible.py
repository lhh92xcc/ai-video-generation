"""OpenAI-compatible chat-completions text provider.

The provider deliberately depends only on HTTP and Pydantic. This keeps the
application independent from a vendor SDK and allows DeepSeek or another
OpenAI-compatible endpoint to be selected by configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
from pydantic import ValidationError

from app.domain.models import ScriptContent, ScriptGenerationRequest, ScriptGenerationResult
from app.providers.errors import TextProviderError


class OpenAICompatibleTextProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
        prompt_path: Path | None = None,
        repair_prompt_path: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._prompt_path = prompt_path or Path(__file__).resolve().parents[2] / "prompts" / "script-generation.txt"
        self._repair_prompt_path = repair_prompt_path or (
            Path(__file__).resolve().parents[2] / "prompts" / "script-generation-repair.txt"
        )

    async def generate(self, request: ScriptGenerationRequest) -> ScriptGenerationResult:
        return await self._generate_from_prompt(
            request,
            system_prompt=self._render_prompt(request),
            user_content=request.topic,
        )

    async def repair(
        self,
        request: ScriptGenerationRequest,
        *,
        raw_output: str,
        validation_error: str,
    ) -> ScriptGenerationResult:
        """Request one bounded structural repair from the same configured model.

        The invalid output is passed as data. The repair prompt explicitly
        forbids following instructions embedded in that data and asks the model
        to preserve factual/content values wherever they are present.
        """

        repair_input = json.dumps(
            {
                "topic": request.topic,
                "target_duration_seconds": request.target_duration_seconds,
                "validation_error": validation_error,
                "invalid_output": raw_output,
            },
            ensure_ascii=False,
        )
        return await self._generate_from_prompt(
            request,
            system_prompt=self._render_repair_prompt(request),
            user_content=repair_input,
        )

    async def _generate_from_prompt(
        self,
        request: ScriptGenerationRequest,
        *,
        system_prompt: str,
        user_content: str,
    ) -> ScriptGenerationResult:
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
                {"role": "user", "content": user_content},
            ],
        }
        started = monotonic()
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

        response_json: dict[str, Any] = {}
        raw_output: str | None = None
        try:
            parsed_response = response.json()
            if not isinstance(parsed_response, dict):
                raise TypeError("response must be a JSON object")
            response_json = parsed_response
            raw_output = response_json["choices"][0]["message"]["content"]
            if not isinstance(raw_output, str):
                raise TypeError("message content must be a string")
            script = ScriptContent.model_validate(json.loads(raw_output))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise TextProviderError(
                "PROVIDER_INVALID_RESPONSE",
                "LLM response was not valid JSON",
                raw_output=raw_output if raw_output is not None else response.text,
                input_tokens=_usage_tokens(response_json, "prompt_tokens"),
                output_tokens=_usage_tokens(response_json, "completion_tokens"),
            ) from exc
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise TextProviderError(
                "SCRIPT_INVALID_OUTPUT",
                f"LLM script failed schema validation: {details}",
                raw_output=raw_output,
                input_tokens=_usage_tokens(response_json, "prompt_tokens"),
                output_tokens=_usage_tokens(response_json, "completion_tokens"),
            ) from exc

        if script.duration_seconds != request.target_duration_seconds:
            raise TextProviderError(
                "SCRIPT_DURATION_MISMATCH",
                "LLM script duration does not match the requested target duration "
                f"({script.duration_seconds}s returned, {request.target_duration_seconds}s requested)",
                raw_output=raw_output,
                input_tokens=_usage_tokens(response_json, "prompt_tokens"),
                output_tokens=_usage_tokens(response_json, "completion_tokens"),
            )

        return ScriptGenerationResult(
            content=script,
            provider="openai_compatible",
            model=self.model,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            input_tokens=_usage_tokens(response_json, "prompt_tokens"),
            output_tokens=_usage_tokens(response_json, "completion_tokens"),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _render_prompt(self, request: ScriptGenerationRequest) -> str:
        template = self._prompt_path.read_text(encoding="utf-8")
        replacements = {
            "{{topic}}": request.topic,
            "{{audience}}": "对该主题感兴趣的普通用户",
            "{{duration_seconds}}": str(request.target_duration_seconds),
            "{{aspect_ratio}}": request.aspect_ratio,
            "{{language}}": request.language,
            "{{tone}}": request.tone,
            "{{required_points}}": "、".join(request.required_points) or "无",
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        return template

    def _render_repair_prompt(self, request: ScriptGenerationRequest) -> str:
        template = self._repair_prompt_path.read_text(encoding="utf-8")
        replacements = {
            "{{topic}}": request.topic,
            "{{duration_seconds}}": str(request.target_duration_seconds),
            "{{language}}": request.language,
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        return template

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "PROVIDER_UNAVAILABLE"
        return "PROVIDER_HTTP_ERROR"


def _usage_tokens(response_json: dict[str, Any], key: str) -> int:
    """Read optional OpenAI-compatible usage without making it mandatory."""

    usage = response_json.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get(key, 0)
    return value if isinstance(value, int) and value >= 0 else 0
