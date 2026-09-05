"""Stable errors raised by text-to-speech and audio artifact validation."""

from __future__ import annotations


class TTSProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BGMProviderError(Exception):
    """Stable errors raised by background-music Providers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AudioNormalizationError(Exception):
    """Stable errors raised while normalizing audio before Artifact storage."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AudioDurationFitError(Exception):
    """Stable errors raised while fitting narration to a visual time budget."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AudioPacingError(Exception):
    """Stable errors raised while smoothing local narration pacing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AudioPauseCompactionError(Exception):
    """Stable errors raised while compacting excessive narration pauses."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AudioArtifactValidationError(TTSProviderError):
    """Raised when generated audio bytes fail the playback validation gate."""
