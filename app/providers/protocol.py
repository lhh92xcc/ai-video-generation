"""Protocols implemented by text-generation providers."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import ScriptGenerationRequest, ScriptGenerationResult


class TextProvider(Protocol):
    async def generate(self, request: ScriptGenerationRequest) -> ScriptGenerationResult:
        ...


class StructuredRepairProvider(Protocol):
    async def repair(
        self,
        request: ScriptGenerationRequest,
        *,
        raw_output: str,
        validation_error: str,
    ) -> ScriptGenerationResult:
        """Repair JSON/schema shape without silently changing source facts."""

        ...
