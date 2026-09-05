"""Stable errors raised by subtitle alignment Providers."""

from __future__ import annotations


class SubtitleAlignmentProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
