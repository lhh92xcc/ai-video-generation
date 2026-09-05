"""Trusted actor identity providers used by the HTTP layer.

The local provider is intentionally a development adapter. Production traffic
must use the signed-header provider behind a trusted gateway; ordinary actor
headers are never accepted in that mode.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from app.config import Settings


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    """Identity resolved from a configured authentication boundary."""

    actor_id: str
    actor_name: str
    source: str


class AuthenticationError(Exception):
    """Raised when a request does not contain a trusted identity."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class IdentityProvider(Protocol):
    def resolve(self, request: Request) -> ActorIdentity:
        ...


class LocalIdentityProvider:
    """Development-only identity provider.

    X-Actor-* overrides are accepted only when auth.mode=local. They are not
    authentication and must never be enabled as a production trust boundary.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, request: Request) -> ActorIdentity:
        actor_id = request.headers.get("X-Actor-Id", self._settings.auth_local_actor_id)
        actor_name = request.headers.get("X-Actor-Name", self._settings.auth_local_actor_name)
        return ActorIdentity(
            actor_id=actor_id.strip()[:120] or self._settings.auth_local_actor_id,
            actor_name=actor_name.strip()[:120] or self._settings.auth_local_actor_name,
            source="local",
        )


class SignedHeaderIdentityProvider:
    """Verify a gateway-signed identity envelope with HMAC-SHA256.

    The signature covers timestamp, method, path, subject and display name:
    ``timestamp\\nmethod\\npath\\nsubject\\nname``. The shared secret is loaded
    only from the environment and should be known by the gateway and API.
    """

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.auth_shared_secret
        self._ttl_seconds = settings.auth_identity_ttl_seconds

    def resolve(self, request: Request) -> ActorIdentity:
        if not self._secret:
            raise AuthenticationError(
                "AUTH_CONFIGURATION_INVALID",
                "Signed identity authentication is enabled but no shared secret is configured",
            )

        actor_id = request.headers.get("X-Auth-Subject", "").strip()[:120]
        actor_name = request.headers.get("X-Auth-Name", "").strip()[:120]
        timestamp_text = request.headers.get("X-Auth-Timestamp", "").strip()
        signature = request.headers.get("X-Auth-Signature", "").strip().lower()
        if not actor_id or not actor_name or not timestamp_text or not signature:
            raise AuthenticationError(
                "AUTHENTICATION_REQUIRED",
                "A trusted signed identity is required",
            )

        try:
            timestamp = int(timestamp_text)
        except ValueError as exc:
            raise AuthenticationError(
                "AUTHENTICATION_INVALID",
                "The signed identity timestamp is invalid",
            ) from exc

        if abs(int(time.time()) - timestamp) > self._ttl_seconds:
            raise AuthenticationError(
                "AUTHENTICATION_EXPIRED",
                "The signed identity has expired",
            )

        message = "\\n".join(
            (timestamp_text, request.method.upper(), request.url.path, actor_id, actor_name)
        ).encode("utf-8")
        expected = hmac.new(self._secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError(
                "AUTHENTICATION_INVALID",
                "The signed identity signature is invalid",
            )
        return ActorIdentity(actor_id=actor_id, actor_name=actor_name, source="signed_header")


def create_identity_provider(settings: Settings) -> IdentityProvider:
    if settings.auth_mode == "signed_header":
        return SignedHeaderIdentityProvider(settings)
    if settings.auth_mode == "local":
        return LocalIdentityProvider(settings)
    raise ValueError(f"Unsupported auth mode: {settings.auth_mode}")


def sign_identity_headers(
    *,
    secret: str,
    method: str,
    path: str,
    actor_id: str,
    actor_name: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Create gateway-compatible headers for integration tests and local tools."""

    signed_timestamp = int(time.time()) if timestamp is None else timestamp
    timestamp_text = str(signed_timestamp)
    message = "\\n".join(
        (timestamp_text, method.upper(), path, actor_id[:120], actor_name[:120])
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {
        "X-Auth-Subject": actor_id[:120],
        "X-Auth-Name": actor_name[:120],
        "X-Auth-Timestamp": timestamp_text,
        "X-Auth-Signature": signature,
    }
