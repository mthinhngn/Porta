"""Deterministic provider test double with no network behavior."""

from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from inspect import isawaitable

from llm_gateway.domain import ChatCompletionRequest, ChatCompletionResponse
from llm_gateway.providers.errors import ProviderError, ProviderUnavailableError
from llm_gateway.providers.protocol import ProviderContext

type ProviderStepCallable = Callable[
    [ChatCompletionRequest, ProviderContext],
    ChatCompletionResponse | Awaitable[ChatCompletionResponse],
]
type ProviderStep = ChatCompletionResponse | ProviderError | ProviderStepCallable


@dataclass(frozen=True, slots=True)
class ProviderCall:
    request: ChatCompletionRequest
    context: ProviderContext


class ScriptedProvider:
    """Consumes configured responses, errors, or callables in FIFO order."""

    def __init__(self, name: str, steps: Iterable[ProviderStep]) -> None:
        self._name = name
        self._steps = deque(steps)
        self.calls: list[ProviderCall] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def remaining_steps(self) -> int:
        return len(self._steps)

    async def complete(
        self,
        request: ChatCompletionRequest,
        context: ProviderContext,
    ) -> ChatCompletionResponse:
        self.calls.append(ProviderCall(request=request, context=context))
        if not self._steps:
            raise ProviderUnavailableError(
                f"Scripted provider {self.name!r} has no remaining steps"
            )

        step = self._steps.popleft()
        if isinstance(step, ProviderError):
            raise step
        if isinstance(step, ChatCompletionResponse):
            return step

        result = step(request, context)
        if isawaitable(result):
            return await result
        return result
