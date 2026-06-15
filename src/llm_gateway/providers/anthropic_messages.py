"""Anthropic Messages API adapter for normalized text generation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from llm_gateway.domain import GenerateRequest
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
    ProviderTokenUsage,
)

_MALFORMED_PAYLOAD = "Provider response payload was malformed."
_NO_OUTPUT_RESPONSE = "Provider response contained no text output."


def _usage_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


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


def _normalize_response(
    payload: Mapping[str, Any],
) -> tuple[GenerateProviderResult | None, str | None]:
    content = payload.get("content")
    if not isinstance(content, list):
        return None, _MALFORMED_PAYLOAD

    parts: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            return None, _MALFORMED_PAYLOAD
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            return None, _MALFORMED_PAYLOAD
        parts.append(text)

    output = "".join(parts)
    if not output:
        return None, _NO_OUTPUT_RESPONSE

    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None, _MALFORMED_PAYLOAD
    input_tokens = _usage_int(usage.get("input_tokens"))
    output_tokens = _usage_int(usage.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None, _MALFORMED_PAYLOAD
    total_tokens = input_tokens + output_tokens

    provider_request_id = payload.get("id")
    return (
        GenerateProviderResult(
            output=output,
            usage=ProviderTokenUsage(
                input_tokens=input_tokens,
                cached_input_tokens=0,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            provider_request_id=provider_request_id
            if isinstance(provider_request_id, str)
            else None,
            cache_status="miss",
        ),
        None,
    )


class AnthropicMessagesProvider(GenerateProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        name: str = "anthropic",
        api_version: str = "2023-06-01",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._name = name
        self._api_version = api_version

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
            "messages": [{"role": "user", "content": request.input}],
            "max_tokens": request.max_output_tokens or 256,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
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
        except ValueError:
            body = None
        if not isinstance(body, Mapping):
            raise ProviderResponseError(_MALFORMED_PAYLOAD)

        result, response_error = _normalize_response(body)
        if response_error is not None:
            raise ProviderResponseError(response_error)
        assert result is not None
        return result

    async def _send_request(
        self,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(
                f"{self._base_url}/messages",
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )

        async with httpx.AsyncClient() as client:
            return await client.post(
                f"{self._base_url}/messages",
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
