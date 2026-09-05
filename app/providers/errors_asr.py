"""Stable errors raised by subtitle ASR Providers."""

from __future__ import annotations


class SubtitleASRProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SubtitleASRInputError(Exception):
    """Raised when the source Artifact cannot be used for transcription."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
