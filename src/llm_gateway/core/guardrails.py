"""Minimal Phase 2 guardrails executed before quota, cache, and providers."""

from __future__ import annotations

from dataclasses import dataclass

from llm_gateway.core.errors import ApiError
from llm_gateway.domain import GenerateRequest, GuardrailDecision

_BLOCKED_TEST_REASON_CODE = "blocked_test_content"


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    version: str
    test_block_token: str


class GuardrailService:
    def __init__(self, *, policy: GuardrailPolicy) -> None:
        self._policy = policy

    def evaluate(self, request: GenerateRequest) -> GuardrailDecision:
        if self._policy.test_block_token.casefold() in request.input.casefold():
            return GuardrailDecision(
                outcome="block",
                reason_code=_BLOCKED_TEST_REASON_CODE,
                version=self._policy.version,
            )
        return GuardrailDecision(
            outcome="allow",
            reason_code=None,
            version=self._policy.version,
        )


def raise_for_blocked(decision: GuardrailDecision) -> None:
    if decision.outcome != "block":
        return
    raise ApiError(
        message="Request blocked by gateway guardrails.",
        type="invalid_request_error",
        status_code=400,
        code=decision.reason_code,
    )
