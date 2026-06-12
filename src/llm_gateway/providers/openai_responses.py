"""OpenAI Responses API adapter for the R1 generate path."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from llm_gateway.domain import GenerateRequest, GenerateTokenUsage
from llm_gateway.providers.errors import (
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from llm_gateway.providers.protocol import (
    GenerateProvider,
    GenerateProviderContext,
    GenerateProviderResult,
)


def _usage_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderResponseError("Provider response usage was malformed.")
    if value < 0:
        raise ProviderResponseError("Provider response usage was malformed.")
    return value


def _extract_output_text(payload: Mapping[str, Any]) -> str:
    output_items = payload.get("output")
    if not isinstance(output_items, list):
        raise ProviderResponseError("Provider response payload was malformed.")

    parts: list[str] = []
    for item in output_items:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "output_text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    if not parts:
        raise ProviderResponseError("Provider response payload was malformed.")
    return "".join(parts)


def _extract_usage(payload: Mapping[str, Any]) -> GenerateTokenUsage:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        raise ProviderResponseError("Provider response usage was malformed.")

    input_tokens = _usage_int(usage.get("input_tokens"))
    output_tokens = _usage_int(usage.get("output_tokens"))
    total_tokens = _usage_int(usage.get("total_tokens"))
    if total_tokens != input_tokens + output_tokens:
        raise ProviderResponseError("Provider response usage was malformed.")

    return GenerateTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _cache_status(payload: Mapping[str, Any]) -> str:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return "miss"
    usage_details = usage.get("input_tokens_details")
    if not isinstance(usage_details, Mapping):
        return "miss"
    cached_tokens = usage_details.get("cached_tokens")
    if isinstance(cached_tokens, int) and cached_tokens > 0:
        return "hit"
    return "miss"


def _map_error(response: httpx.Response) -> Exception:
    if response.status_code in {400, 404, 409, 422}:
        return ProviderBadRequestError("Provider rejected the request.", status_code=400)
    if response.status_code in {401, 403}:
        return ProviderAuthenticationError("Provider authentication failed.", status_code=502)
    if response.status_code == 429:
        return ProviderRateLimitError("Provider rate limit exceeded.", status_code=429)
    if response.status_code >= 500:
        return ProviderUnavailableError("Provider is unavailable.", status_code=503)
    return ProviderUnavailableError("Provider request failed.", status_code=response.status_code)


class OpenAIResponsesProvider(GenerateProvider):
    """Minimal OpenAI Responses adapter for text generation."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        name: str = "openai",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        if not self._api_key:
            raise ProviderAuthenticationError("Provider authentication failed.")

        payload: dict[str, Any] = {
            "model": context.model_name,
            "input": request.input,
            "store": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        if request.user is not None:
            payload["user"] = request.user

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=v1",
        }

        try:
            response = await self._send_request(
                headers=headers,
                payload=payload,
                timeout_seconds=context.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Provider request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Provider is unavailable.") from exc

        if response.status_code >= 400:
            raise _map_error(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Provider response payload was malformed.") from exc
        if not isinstance(body, Mapping):
            raise ProviderResponseError("Provider response payload was malformed.")

        return GenerateProviderResult(
            output=_extract_output_text(body),
            usage=_extract_usage(body),
            provider_request_id=body.get("id") if isinstance(body.get("id"), str) else None,
            cache_status=_cache_status(body),
        )

    async def _send_request(
        self,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(
                f"{self._base_url}/responses",
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )

        async with httpx.AsyncClient() as client:
            return await client.post(
                f"{self._base_url}/responses",
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
