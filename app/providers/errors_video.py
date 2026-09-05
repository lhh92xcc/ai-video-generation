"""Stable errors raised by video-generation adapters."""

from __future__ import annotations


class VideoProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VideoArtifactValidationError(VideoProviderError):
    """Raised when generated video bytes fail the playback validation gate."""


class VideoDurationFitError(VideoProviderError):
    """Stable errors raised while extending a video to a narration budget."""
