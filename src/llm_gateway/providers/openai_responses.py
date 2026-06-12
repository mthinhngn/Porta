"""OpenAI Responses API adapter for the R1 generate path."""

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
_MALFORMED_USAGE = "Provider response usage was malformed."
_INCOMPLETE_RESPONSE = "Provider response was not completed."
_REFUSAL_RESPONSE = "Provider response contained a refusal."
_NO_OUTPUT_RESPONSE = "Provider response contained no text output."


def _usage_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _extract_output_text(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    output_items = payload.get("output")
    if not isinstance(output_items, list):
        return None, _MALFORMED_PAYLOAD

    parts: list[str] = []
    for item in output_items:
        if not isinstance(item, Mapping):
            return None, _MALFORMED_PAYLOAD
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            return None, _MALFORMED_PAYLOAD
        for block in content:
            if not isinstance(block, Mapping):
                return None, _MALFORMED_PAYLOAD
            block_type = block.get("type")
            if block_type == "refusal":
                return None, _REFUSAL_RESPONSE
            if block_type == "output_text":
                text = block.get("text")
                if not isinstance(text, str):
                    return None, _MALFORMED_PAYLOAD
                parts.append(text)

    output = "".join(parts)
    if not output:
        return None, _NO_OUTPUT_RESPONSE
    return output, None


def _extract_usage(payload: Mapping[str, Any]) -> ProviderTokenUsage | None:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None

    input_tokens = _usage_int(usage.get("input_tokens"))
    output_tokens = _usage_int(usage.get("output_tokens"))
    total_tokens = _usage_int(usage.get("total_tokens"))
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None
    if total_tokens != input_tokens + output_tokens:
        return None

    if "input_tokens_details" not in usage:
        cached_input_tokens = 0
    else:
        input_details = usage["input_tokens_details"]
        if not isinstance(input_details, Mapping) or "cached_tokens" not in input_details:
            return None
        parsed_cached_input_tokens = _usage_int(input_details["cached_tokens"])
        if parsed_cached_input_tokens is None or parsed_cached_input_tokens > input_tokens:
            return None
        cached_input_tokens = parsed_cached_input_tokens

    return ProviderTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _normalize_response(
    payload: Mapping[str, Any],
) -> tuple[GenerateProviderResult | None, str | None]:
    status = payload.get("status")
    if not isinstance(status, str):
        return None, _MALFORMED_PAYLOAD
    if status != "completed":
        return None, _INCOMPLETE_RESPONSE

    output, output_error = _extract_output_text(payload)
    if output_error is not None:
        return None, output_error

    usage = _extract_usage(payload)
    if usage is None:
        return None, _MALFORMED_USAGE

    assert output is not None
    provider_request_id = payload.get("id")
    return (
        GenerateProviderResult(
            output=output,
            usage=usage,
            provider_request_id=provider_request_id
            if isinstance(provider_request_id, str)
            else None,
            cache_status="hit" if usage.cached_input_tokens > 0 else "miss",
        ),
        None,
    )


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

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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
            error = _map_error(response)
            del response
            raise error

        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, Mapping):
            del body
            del response
            raise ProviderResponseError(_MALFORMED_PAYLOAD)

        result, response_error = _normalize_response(body)
        if response_error is not None:
            del body
            del response
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
