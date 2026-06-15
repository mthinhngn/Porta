"""Ollama client construction and readiness probing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import httpx


def build_ollama_client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url.rstrip("/"))


async def missing_ollama_models(
    client: httpx.AsyncClient,
    required_models: Iterable[str],
) -> set[str]:
    response = await client.get("/api/tags", timeout=5.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("invalid Ollama tags response")
    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("invalid Ollama tags response")
    available = {
        name
        for item in models
        if isinstance(item, Mapping) and isinstance((name := item.get("name")), str)
    }
    return set(required_models) - available
