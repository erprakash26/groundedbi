"""Provider-neutral types: what any planner (rule-based today, an LLM later) consumes and returns.

The orchestrator only knows these types. A provider never touches the database: it can only
ask for tools by name, and it sees tool results exactly as an LLM would (plain JSON dicts).

Conversation, in order:
  UserMessage            the question
  AssistantToolCalls     the provider asked for one or more tools
  ToolResultMessage      one per tool call, matched by call_id
  ... repeated until the provider returns a FinalAnswer or a Clarify turn.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str  # unique within one question; links the call to its ToolResultMessage
    name: str
    arguments: dict[str, Any]


# --- conversation messages -------------------------------------------------


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantToolCalls:
    calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class ToolResultMessage:
    call_id: str
    result: dict[str, Any]  # ToolResult.model_dump(): plain JSON, exactly what an LLM would be sent


Message = UserMessage | AssistantToolCalls | ToolResultMessage


# --- provider turns --------------------------------------------------------


@dataclass(frozen=True)
class ToolCalls:
    calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class FinalAnswer:
    text: str
    out_of_scope: bool = False  # True: a polite refusal (the data can never answer this question)


@dataclass(frozen=True)
class Clarify:
    question: str  # asked back to the user when a different period or name would make it answerable


Turn = ToolCalls | FinalAnswer | Clarify


# --- usage and cost ----------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens. Zero for the rule-based provider."""

    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0


def estimate_cost_usd(usage: Usage, pricing: Pricing) -> float:
    cost = (
        usage.input_tokens * pricing.input_usd_per_million + usage.output_tokens * pricing.output_usd_per_million
    ) / 1_000_000
    return round(cost, 6)


@dataclass(frozen=True)
class ProviderResponse:
    turn: Turn
    usage: Usage = field(default_factory=Usage)


class Provider(Protocol):
    """Anything with these attributes is a provider; no inheritance needed (structural typing)."""

    name: str
    pricing: Pricing

    def next_turn(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> ProviderResponse:
        """Decide the next step from the conversation so far and the available tool definitions."""
        ...
