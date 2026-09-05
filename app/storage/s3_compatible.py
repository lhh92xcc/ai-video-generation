"""S3-compatible Artifact storage using the AWS Signature V4 protocol."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from typing import Callable
from urllib.parse import SplitResult, quote, unquote, urlsplit, urlunsplit

import httpx

from app.storage.protocol import StorageError, StoredArtifact, validate_storage_key


class S3CompatibleArtifactStorage:
    """Upload artifacts to MinIO or another path-style S3-compatible endpoint."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str | None,
        secret_key: str | None,
        public_endpoint: str | None = None,
        timeout_seconds: int = 120,
        signed_url_expire_seconds: int = 900,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._validate_endpoint(endpoint, "Artifact storage endpoint")
        if public_endpoint is not None:
            self._validate_endpoint(public_endpoint, "Artifact storage public endpoint")
        if not bucket or "/" in bucket or bucket in {".", ".."}:
            raise StorageError(
                "STORAGE_CONFIG_INVALID",
                "Artifact storage bucket must be a non-empty name without slash",
            )
        if not region:
            raise StorageError("STORAGE_CONFIG_INVALID", "Artifact storage region is required")
        if timeout_seconds <= 0:
            raise StorageError(
                "STORAGE_CONFIG_INVALID",
                "Artifact storage timeout must be greater than zero",
            )
        if not 1 <= signed_url_expire_seconds <= 604800:
            raise StorageError(
                "STORAGE_CONFIG_INVALID",
                "Artifact signed URL expiration must be between 1 and 604800 seconds",
            )

        self._endpoint = endpoint.rstrip("/")
        self._public_endpoint = (public_endpoint or endpoint).rstrip("/")
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._timeout_seconds = timeout_seconds
        self._signed_url_expire_seconds = signed_url_expire_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def put_bytes(
        self,
        storage_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredArtifact:
        safe_key = validate_storage_key(storage_key)
        if not content_type.strip():
            raise StorageError("STORAGE_INVALID_REQUEST", "Artifact content type is required")
        self._require_credentials()

        payload_hash = hashlib.sha256(content).hexdigest()
        request_time = self._clock().astimezone(timezone.utc)
        amz_date = request_time.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = request_time.strftime("%Y%m%d")
        object_url = self._object_url(safe_key)
        parsed_url = urlsplit(object_url)
        host = parsed_url.netloc
        headers = {
            "content-type": content_type,
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        authorization = self._authorization(
            method="PUT",
            parsed_url=parsed_url,
            headers=headers,
            payload_hash=payload_hash,
            amz_date=amz_date,
            date_stamp=date_stamp,
        )
        headers["authorization"] = authorization

        try:
            response = await self._client.put(
                object_url,
                content=content,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise StorageError(
                "STORAGE_TIMEOUT",
                "Artifact storage upload timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise StorageError(
                "STORAGE_UNAVAILABLE",
                "Artifact storage upload request failed",
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise self._response_error(response.status_code)

        return StoredArtifact(
            uri=f"s3://{self._bucket}/{safe_key}",
            storage_key=safe_key,
            content_type=content_type,
            size_bytes=len(content),
            sha256=payload_hash,
        )

    async def get_bytes(self, storage_key: str) -> bytes:
        safe_key = validate_storage_key(storage_key)
        download_url = self._create_download_url(
            safe_key,
            self._signed_url_expire_seconds,
            self._endpoint,
        )
        try:
            response = await self._client.get(
                download_url,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise StorageError(
                "STORAGE_TIMEOUT",
                "Artifact storage download timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise StorageError(
                "STORAGE_UNAVAILABLE",
                "Artifact storage download request failed",
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise self._download_response_error(response.status_code)
        return response.content

    def create_download_url(
        self,
        storage_key: str,
        expires_in_seconds: int | None = None,
    ) -> str:
        """Create an AWS Signature V4 presigned GET URL without network I/O."""

        safe_key = validate_storage_key(storage_key)
        self._require_credentials()
        expires = (
            self._signed_url_expire_seconds
            if expires_in_seconds is None
            else expires_in_seconds
        )
        if not 1 <= expires <= 604800:
            raise StorageError(
                "STORAGE_INVALID_REQUEST",
                "Artifact signed URL expiration must be between 1 and 604800 seconds",
            )

        return self._create_download_url(safe_key, expires, self._public_endpoint)

    def _create_download_url(
        self,
        safe_key: str,
        expires: int,
        endpoint: str,
    ) -> str:
        self._require_credentials()
        request_time = self._clock().astimezone(timezone.utc)
        amz_date = request_time.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = request_time.strftime("%Y%m%d")
        object_url = self._object_url(safe_key, endpoint)
        parsed_url = urlsplit(object_url)
        credential_scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self._access_key}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = self._canonical_query(query)
        canonical_headers = f"host:{parsed_url.netloc}\n"
        canonical_request = "\n".join(
            (
                "GET",
                self._canonical_uri(parsed_url.path),
                canonical_query,
                canonical_headers,
                "host",
                "UNSIGNED-PAYLOAD",
            )
        )
        query["X-Amz-Signature"] = self._signature(
            canonical_request=canonical_request,
            amz_date=amz_date,
            date_stamp=date_stamp,
        )
        return urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                self._canonical_query(query),
                "",
            )
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _validate_endpoint(endpoint: str, label: str) -> None:
        parsed_endpoint = urlsplit(endpoint)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
            raise StorageError(
                "STORAGE_CONFIG_INVALID",
                f"{label} must be an absolute HTTP(S) URL",
            )
        if parsed_endpoint.query or parsed_endpoint.fragment:
            raise StorageError(
                "STORAGE_CONFIG_INVALID",
                f"{label} must not contain a query or fragment",
            )

    def _object_url(self, storage_key: str, endpoint: str | None = None) -> str:
        parsed_endpoint = urlsplit(endpoint or self._endpoint)
        endpoint_path = parsed_endpoint.path.rstrip("/")
        bucket_path = quote(self._bucket, safe="-_.~")
        key_path = quote(storage_key, safe="/-_.~")
        object_path = f"{endpoint_path}/{bucket_path}/{key_path}"
        return urlunsplit(
            (parsed_endpoint.scheme, parsed_endpoint.netloc, object_path, "", "")
        )

    def _authorization(
        self,
        method: str,
        parsed_url: SplitResult,
        headers: dict[str, str],
        payload_hash: str,
        amz_date: str,
        date_stamp: str,
    ) -> str:
        canonical_headers = "".join(
            f"{name}:{' '.join(value.strip().split())}\n"
            for name, value in sorted(headers.items())
        )
        signed_headers = ";".join(sorted(headers))
        canonical_request = "\n".join(
            (
                method,
                self._canonical_uri(parsed_url.path),
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        signature = self._signature(canonical_request, amz_date, date_stamp)
        credential_scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        return (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

    def _signature(self, canonical_request: str, amz_date: str, date_stamp: str) -> str:
        credential_scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            )
        )
        return hmac.new(
            self._signing_key(date_stamp),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _canonical_query(params: dict[str, str]) -> str:
        encoded = sorted(
            (
                quote(key, safe="-_.~"),
                quote(value, safe="-_.~"),
            )
            for key, value in params.items()
        )
        return "&".join(f"{key}={value}" for key, value in encoded)

    def _require_credentials(self) -> None:
        if not self._access_key or not self._secret_key:
            raise StorageError(
                "STORAGE_AUTH_REQUIRED",
                "Artifact storage access key and secret key are required",
            )

    def _signing_key(self, date_stamp: str) -> bytes:
        assert self._secret_key is not None
        date_key = hmac.new(
            f"AWS4{self._secret_key}".encode("utf-8"),
            date_stamp.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        region_key = hmac.new(date_key, self._region.encode("utf-8"), hashlib.sha256).digest()
        service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
        return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()

    @staticmethod
    def _canonical_uri(path: str) -> str:
        return quote(unquote(path), safe="/-_.~") or "/"

    @staticmethod
    def _response_error(status_code: int) -> StorageError:
        if status_code in {401, 403}:
            return StorageError(
                "STORAGE_AUTH_FAILED",
                "Artifact storage rejected the configured credentials",
            )
        if status_code == 429:
            return StorageError(
                "STORAGE_RATE_LIMITED",
                "Artifact storage rate limit was reached",
            )
        if 500 <= status_code <= 599:
            return StorageError(
                "STORAGE_UNAVAILABLE",
                "Artifact storage service returned a server error",
            )
        return StorageError(
            "STORAGE_UPLOAD_FAILED",
            f"Artifact storage upload failed with HTTP {status_code}",
        )

    @staticmethod
    def _download_response_error(status_code: int) -> StorageError:
        if status_code == 404:
            return StorageError(
                "STORAGE_NOT_FOUND",
                "Artifact was not found in object storage",
            )
        if status_code in {401, 403}:
            return StorageError(
                "STORAGE_AUTH_FAILED",
                "Artifact storage rejected the download request",
            )
        if status_code == 429:
            return StorageError(
                "STORAGE_RATE_LIMITED",
                "Artifact storage rate limit was reached while downloading",
            )
        if 500 <= status_code <= 599:
            return StorageError(
                "STORAGE_UNAVAILABLE",
                "Artifact storage service returned a server error while downloading",
            )
        return StorageError(
            "STORAGE_DOWNLOAD_FAILED",
            f"Artifact storage download failed with HTTP {status_code}",
        )
