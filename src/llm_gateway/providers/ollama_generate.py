"""Native non-streaming Ollama generate adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from llm_gateway.domain import GenerateRequest
from llm_gateway.providers.errors import (
    ProviderBadRequestError,
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
    if response.status_code == 400:
        return ProviderBadRequestError("Provider rejected the request.", status_code=400)
    if response.status_code in {404, 429} or response.status_code >= 500:
        return ProviderUnavailableError("Provider is unavailable.", status_code=503)
    return ProviderUnavailableError("Provider request failed.", status_code=503)


def _normalize_response(payload: Mapping[str, Any]) -> GenerateProviderResult:
    output = payload.get("response")
    done = payload.get("done")
    input_tokens = _usage_int(payload.get("prompt_eval_count"))
    output_tokens = _usage_int(payload.get("eval_count"))
    if not isinstance(done, bool) or done is not True:
        raise ProviderResponseError(_MALFORMED_PAYLOAD)
    if not isinstance(output, str) or not output:
        raise ProviderResponseError(_NO_OUTPUT_RESPONSE)
    if input_tokens is None or output_tokens is None:
        raise ProviderResponseError(_MALFORMED_PAYLOAD)
    return GenerateProviderResult(
        output=output,
        usage=ProviderTokenUsage(
            input_tokens=input_tokens,
            cached_input_tokens=0,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        cache_status="miss",
    )


class OllamaGenerateProvider(GenerateProvider):
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        name: str,
    ) -> None:
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
        options: dict[str, int | float] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.top_p is not None:
            options["top_p"] = request.top_p
        if request.max_output_tokens is not None:
            options["num_predict"] = request.max_output_tokens
        payload: dict[str, Any] = {
            "model": context.model_name,
            "prompt": request.input,
            "stream": False,
        }
        if options:
            payload["options"] = options
        try:
            response = await self._client.post(
                "/api/generate",
                json=payload,
                timeout=context.timeout_seconds,
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
            raise ProviderResponseError(_MALFORMED_PAYLOAD) from exc
        if not isinstance(body, Mapping):
            raise ProviderResponseError(_MALFORMED_PAYLOAD)
        return _normalize_response(body)
