from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMCall:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class LLMProvider(ABC):
    """Abstract LLM provider.

    Subclasses implement `complete()`. The base class tracks aggregate call
    count and cost so the supervisor can enforce guardrail limits.
    """

    def __init__(self) -> None:
        self.call_count: int = 0
        self.total_cost_usd: float = 0.0
        self._call_log: list[LLMCall] = []

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str = "",
        use_fast: bool = True,
    ) -> T:
        """Call the LLM and parse the response into `schema`.

        Args:
            prompt: User-turn content (document text, extracted signals, etc.)
            schema: Pydantic model class the response must conform to.
            system: Optional system-turn instruction prepended to the prompt.
            use_fast: True → fast/cheap model; False → smart/expensive model.

        Returns:
            A validated instance of `schema`.
        """
        ...

    def reset_counters(self) -> None:
        self.call_count = 0
        self.total_cost_usd = 0.0
        self._call_log.clear()

    def _record(self, call: LLMCall) -> None:
        self.call_count += 1
        self.total_cost_usd += call.cost_usd
        self._call_log.append(call)
