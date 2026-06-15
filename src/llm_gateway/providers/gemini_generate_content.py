"""Gemini generateContent adapter for normalized text generation."""

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
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None, _MALFORMED_PAYLOAD

    first_candidate = candidates[0]
    if not isinstance(first_candidate, Mapping):
        return None, _MALFORMED_PAYLOAD
    content = first_candidate.get("content")
    if not isinstance(content, Mapping):
        return None, _MALFORMED_PAYLOAD
    parts = content.get("parts")
    if not isinstance(parts, list):
        return None, _MALFORMED_PAYLOAD

    output_parts: list[str] = []
    for item in parts:
        if not isinstance(item, Mapping):
            return None, _MALFORMED_PAYLOAD
        text = item.get("text")
        if not isinstance(text, str):
            continue
        output_parts.append(text)

    output = "".join(output_parts)
    if not output:
        return None, _NO_OUTPUT_RESPONSE

    usage = payload.get("usageMetadata")
    if not isinstance(usage, Mapping):
        return None, _MALFORMED_PAYLOAD
    input_tokens = _usage_int(usage.get("promptTokenCount"))
    output_tokens = _usage_int(usage.get("candidatesTokenCount"))
    total_tokens = _usage_int(usage.get("totalTokenCount"))
    cached_input_tokens = _usage_int(usage.get("cachedContentTokenCount")) or 0
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None, _MALFORMED_PAYLOAD

    return (
        GenerateProviderResult(
            output=output,
            usage=ProviderTokenUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            cache_status="hit" if cached_input_tokens > 0 else "miss",
        ),
        None,
    )


class GeminiGenerateContentProvider(GenerateProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        name: str = "gemini",
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
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": request.input}],
                }
            ]
        }
        generation_config: dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.top_p is not None:
            generation_config["topP"] = request.top_p
        if request.max_output_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_output_tokens
        if generation_config:
            payload["generationConfig"] = generation_config

        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

        try:
            response = await self._send_request(
                model_name=context.model_name,
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
        model_name: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> httpx.Response:
        url = f"{self._base_url}/models/{model_name}:generateContent"
        if self._client is not None:
            return await self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )

        async with httpx.AsyncClient() as client:
            return await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
