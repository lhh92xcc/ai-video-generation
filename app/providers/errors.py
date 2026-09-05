"""Provider errors with stable application error codes."""

from __future__ import annotations


class TextProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        raw_output: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # Kept for controlled structured-repair/evaluation flows. Callers must
        # not put this value in ordinary logs or API error responses.
        self.raw_output = raw_output
        self.input_tokens = max(0, input_tokens)
        self.output_tokens = max(0, output_tokens)


class ImageProviderError(Exception):
    """Stable error raised by an image-generation adapter."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
