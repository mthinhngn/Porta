"""Per-actor Redis response cache for Phase 2."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from llm_gateway.core.errors import ApiError
from llm_gateway.core.redis import RedisClient
from llm_gateway.domain import GenerateRequest, GenerateResponse

_CACHE_VALUE_VERSION = b"v1"
_LOCK_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def _request_fingerprint(request: GenerateRequest) -> str:
    payload = request.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_key(
    *,
    actor_id: UUID,
    resolved_model: str,
    guardrail_version: str,
    request: GenerateRequest,
) -> str:
    fingerprint = _request_fingerprint(request)
    return (
        "llm-gateway:cache:"
        f"actor:{actor_id}:"
        f"model:{resolved_model}:"
        f"guardrail:{guardrail_version}:"
        f"req:{fingerprint}"
    )


@dataclass(frozen=True, slots=True)
class CachePolicy:
    ttl_seconds: int
    guardrail_version: str
    encryption_key: str
    lock_ttl_seconds: int
    wait_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class CacheReservation:
    key: str
    lock_key: str
    owner_token: str


@dataclass(frozen=True, slots=True)
class CacheLookup:
    response: GenerateResponse | None = None
    reservation: CacheReservation | None = None


class RedisResponseCache:
    def __init__(self, redis_client: RedisClient, *, policy: CachePolicy) -> None:
        self._redis_client = redis_client
        self._policy = policy
        self._cipher = AESGCM(self._decode_encryption_key(policy.encryption_key))

    @staticmethod
    def _decode_encryption_key(value: str) -> bytes:
        try:
            key = base64.urlsafe_b64decode(value.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("cache encryption key must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("cache encryption key must decode to exactly 32 bytes")
        return key

    def _encrypt(self, *, key: str, response: GenerateResponse) -> str:
        plaintext = response.model_copy(update={"served_from_cache": False}).model_dump_json()
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), key.encode("utf-8"))
        return base64.urlsafe_b64encode(_CACHE_VALUE_VERSION + nonce + ciphertext).decode("ascii")

    def _decrypt(self, *, key: str, value: str) -> GenerateResponse:
        encrypted = base64.urlsafe_b64decode(value.encode("ascii"))
        if not encrypted.startswith(_CACHE_VALUE_VERSION) or len(encrypted) <= 14:
            raise ValueError("unsupported cache value")
        nonce = encrypted[2:14]
        ciphertext = encrypted[14:]
        plaintext = self._cipher.decrypt(nonce, ciphertext, key.encode("utf-8"))
        return GenerateResponse.model_validate_json(plaintext)

    async def _get_by_key(self, key: str) -> GenerateResponse | None:
        try:
            cached = await self._redis_client.get(key)
        except Exception as exc:
            raise ApiError(
                message="Cache service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            ) from exc
        if cached is None:
            return None
        if not isinstance(cached, str):
            raise ApiError(
                message="Cache service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            )
        try:
            response = self._decrypt(key=key, value=cached)
        except (InvalidTag, UnicodeDecodeError, ValueError, ValidationError):
            with suppress(Exception):
                await self._redis_client.delete(key)
            return None
        return response.model_copy(update={"served_from_cache": True})

    async def get_or_reserve(
        self,
        *,
        actor_id: UUID,
        resolved_model: str,
        request: GenerateRequest,
    ) -> CacheLookup:
        key = cache_key(
            actor_id=actor_id,
            resolved_model=resolved_model,
            guardrail_version=self._policy.guardrail_version,
            request=request,
        )
        lock_key = f"{key}:lock"
        owner_token = secrets.token_urlsafe(24)
        wait_deadline = perf_counter() + self._policy.wait_timeout_seconds

        while True:
            cached = await self._get_by_key(key)
            if cached is not None:
                return CacheLookup(response=cached)
            try:
                acquired = await self._redis_client.set(
                    lock_key,
                    owner_token,
                    ex=self._policy.lock_ttl_seconds,
                    nx=True,
                )
            except Exception as exc:
                raise ApiError(
                    message="Cache service is unavailable.",
                    type="server_error",
                    status_code=503,
                    code="service_unavailable",
                ) from exc
            if acquired:
                return CacheLookup(
                    reservation=CacheReservation(
                        key=key,
                        lock_key=lock_key,
                        owner_token=owner_token,
                    )
                )
            if perf_counter() >= wait_deadline:
                raise ApiError(
                    message="Cache request is still in progress.",
                    type="server_error",
                    status_code=503,
                    code="service_unavailable",
                )
            await asyncio.sleep(0.02)

    async def put(
        self,
        *,
        reservation: CacheReservation,
        response: GenerateResponse,
    ) -> None:
        payload = self._encrypt(key=reservation.key, response=response)
        try:
            await self._redis_client.set(
                reservation.key,
                payload,
                ex=self._policy.ttl_seconds,
            )
        except Exception:
            # A cache write failure should not convert a successful provider
            # response into a client-visible error.
            return

    async def release(self, reservation: CacheReservation) -> None:
        try:
            await self._redis_client.eval(
                _LOCK_RELEASE_SCRIPT,
                1,
                reservation.lock_key,
                reservation.owner_token,
            )
        except Exception:
            return
