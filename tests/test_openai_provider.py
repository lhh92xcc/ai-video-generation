from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.domain.models import ScriptGenerationRequest
from app.providers.errors import TextProviderError
from app.providers.openai_compatible import OpenAICompatibleTextProvider
from tests.test_script_schema import valid_script


def test_openai_compatible_provider_parses_structured_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "必须包含的信息：关键点" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(valid_script(), ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34},
            },
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleTextProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="test-model",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate(
            ScriptGenerationRequest(
                topic="Provider test",
                language="zh-CN",
                target_duration_seconds=15,
                aspect_ratio="9:16",
                tone="清晰",
                required_points=["关键点"],
            )
        )
        await provider.close()

        assert result.provider == "openai_compatible"
        assert result.model == "test-model"
        assert result.content.title == "测试脚本"
        assert result.input_tokens == 12
        assert result.output_tokens == 34

    asyncio.run(exercise())


def test_openai_compatible_provider_requires_api_key() -> None:
    async def exercise() -> None:
        provider = OpenAICompatibleTextProvider(
            base_url="https://example.test",
            api_key=None,
            model="test-model",
            temperature=0.2,
            timeout_seconds=5,
        )
        with pytest.raises(TextProviderError) as error:
            await provider.generate(
                ScriptGenerationRequest(
                    topic="No key",
                    language="zh-CN",
                    target_duration_seconds=15,
                    aspect_ratio="9:16",
                    tone="清晰",
                )
            )
        await provider.close()
        assert error.value.code == "PROVIDER_AUTH_FAILED"

    asyncio.run(exercise())


def test_openai_compatible_provider_rejects_duration_mismatch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = valid_script()
        payload["duration_seconds"] = 16
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleTextProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="test-model",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(TextProviderError) as error:
            await provider.generate(
                ScriptGenerationRequest(
                    topic="Duration mismatch",
                    language="zh-CN",
                    target_duration_seconds=15,
                    aspect_ratio="9:16",
                    tone="清晰",
                )
            )
        await provider.close()
        assert error.value.code == "SCRIPT_DURATION_MISMATCH"

    asyncio.run(exercise())


def test_openai_compatible_provider_repairs_schema_output_and_keeps_raw_failure() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        if calls == 1:
            invalid = valid_script()
            invalid["scenes"][0]["voiceover"] = ""
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(invalid, ensure_ascii=False)}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 22},
                },
            )

        assert "script-generation-repair.v1" in payload["messages"][0]["content"]
        assert "invalid_output" in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(valid_script(), ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 33, "completion_tokens": 44},
            },
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleTextProvider(
            base_url="https://example.test",
            api_key="test-key",
            model="test-model",
            temperature=0.2,
            timeout_seconds=5,
            client=client,
        )
        request = ScriptGenerationRequest(
            topic="Repair test",
            language="zh-CN",
            target_duration_seconds=15,
            aspect_ratio="9:16",
            tone="清晰",
        )
        with pytest.raises(TextProviderError) as error:
            await provider.generate(request)
        assert error.value.code == "SCRIPT_INVALID_OUTPUT"
        assert error.value.raw_output is not None
        assert '"voiceover": ""' in error.value.raw_output
        assert error.value.input_tokens == 11
        assert error.value.output_tokens == 22

        repaired = await provider.repair(
            request,
            raw_output=error.value.raw_output or "",
            validation_error=error.value.message,
        )
        await provider.close()

        assert calls == 2
        assert repaired.content.scenes[0].voiceover
        assert repaired.input_tokens == 33
        assert repaired.output_tokens == 44

    asyncio.run(exercise())
