"""Deterministic text provider used before a real LLM is connected."""

from __future__ import annotations

import asyncio
from time import monotonic

from pydantic import ValidationError

from app.domain.models import ScriptGenerationRequest, ScriptGenerationResult
from app.providers.errors import TextProviderError


class MockTextProvider:
    """Generate a predictable script so the API can be developed without API keys."""

    async def generate(self, request: ScriptGenerationRequest) -> ScriptGenerationResult:
        started = monotonic()
        await asyncio.sleep(0.01)

        scene_duration = request.target_duration_seconds // 3
        last_duration = request.target_duration_seconds - scene_duration * 2
        scenes = [
            {
                "scene_index": 1,
                "duration_seconds": scene_duration,
                "voiceover": f"今天用一分钟了解：{request.topic}。先说最关键的一点。",
                "caption": f"一分钟了解：{request.topic}",
                "visual_keywords": [request.topic, "opening shot"],
                "transition": "cut",
                "risk_notes": [],
            },
            {
                "scene_index": 2,
                "duration_seconds": scene_duration,
                "voiceover": "接着看三个实用要点，先从最容易被忽略的细节开始。",
                "caption": "三个实用要点，先看关键细节",
                "visual_keywords": ["hands-on demonstration", "detail shot"],
                "transition": "cut",
                "risk_notes": [],
            },
            {
                "scene_index": 3,
                "duration_seconds": last_duration,
                "voiceover": "最后把重点记住，再根据自己的情况做选择。",
                "caption": "记住重点，再按需选择",
                "visual_keywords": ["summary", "happy ending"],
                "transition": "fade",
                "risk_notes": [],
            },
        ]
        content = {
            "title": request.topic,
            "hook": f"别急着做决定，先看懂{request.topic}。",
            "summary": f"围绕{request.topic}整理一份入门说明。",
            "duration_seconds": request.target_duration_seconds,
            "scenes": scenes,
            "cta": "收藏这条内容，之后按自己的需求核对。",
            "risk_notes": ["当前内容由 Mock Provider 生成，仅用于开发联调。"],
        }
        try:
            return ScriptGenerationResult(
                content=content,
                provider="mock",
                model="mock-script-v1",
                duration_ms=max(1, int((monotonic() - started) * 1000)),
            )
        except ValidationError as exc:
            raise TextProviderError("SCRIPT_INVALID_OUTPUT", "Mock script failed schema validation") from exc

    async def repair(
        self,
        request: ScriptGenerationRequest,
        *,
        raw_output: str,
        validation_error: str,
    ) -> ScriptGenerationResult:
        """Return deterministic valid output for offline repair-path tests."""

        del raw_output, validation_error
        return await self.generate(request)
