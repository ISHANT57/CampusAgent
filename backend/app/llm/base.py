"""The LLM provider contract.

Every type here traces to a difference MEASURED in M0, not one anticipated.
See spike/PROVIDER_EVALUATION.md. The differences that forced this design:

  |                | OpenRouter                  | Gemini                      |
  |----------------|-----------------------------|-----------------------------|
  | tool call args | JSON **string** (parse it)  | real JSON **object**        |
  | response path  | choices[0].message          | candidates[0].content       |
  | content        | .content, a string          | .parts[], text+calls mixed  |
  | usage          | usage.prompt_tokens         | usageMetadata.promptTokenCount |
  | finish reason  | "stop"                      | "STOP"                      |
  | system prompt  | a message with role=system  | separate systemInstruction  |
  | assistant role | "assistant"                 | "model"                     |
  | schema dialect | JSON Schema                 | OpenAPI 3.0 subset          |

The abstraction's job is to make all of that invisible above this module.

Why an abstraction for one provider at all: M0 proved free-tier quota
exhaustion is routine — both vendors hit it during the spike. When it blocks
development, adding a second provider behind this Protocol is ~40 lines.
Without it, it is a refactor of the agent loop.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """One conversation turn.

    Only three roles. There is deliberately no `tool` role: M0/E5 proved a
    multi-turn tool exchange works when the result is fed back as a plain
    user turn, and every provider accepts these three. A native tool-result
    role would be provider-specific — the exact thing this module removes.
    """

    role: Role
    content: str


class ToolSpec(BaseModel):
    """A tool as the MODEL sees it. Produced by the registry at M12.

    `description` is not documentation — it is the selection algorithm. M0
    traced all 15 wrong tool choices to a single word in one description.
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema; providers translate as needed


class ToolCall(BaseModel):
    """A parsed, validated tool invocation.

    `arguments` is ALWAYS a dict by the time it reaches here. OpenRouter hands
    back a JSON string and Gemini hands back an object; normalising that is the
    provider's job, so nothing above this line ever calls json.loads.
    """

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Completion(BaseModel):
    """One model response, provider-agnostic."""

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    finish_reason: str | None = None
    latency_ms: int = 0

    # Escape hatch. Persisted on the step so a confusing run can be diagnosed
    # against what the provider ACTUALLY returned rather than our reading of
    # it. M0 was only solvable because raw bodies were kept: the decisive
    # finding (OpenRouter proxying Google) lived in an error body, not a
    # status code.
    raw: dict[str, Any] | None = None

    @property
    def tool_call(self) -> ToolCall | None:
        """The single call, or None. The loop selects one tool per turn —
        M0 observed zero multi-call responses in 180 samples."""
        return self.tool_calls[0] if self.tool_calls else None


# --- Errors ---------------------------------------------------------------
#
# The transient/permanent split is not tidiness. M0/F2 found two 429s that
# demand OPPOSITE responses:
#
#   "temporarily rate-limited upstream"     -> back off and retry
#   "limit: 0, free_tier_requests"          -> this model will NEVER work on
#                                              this key; retrying wastes the
#                                              run's whole budget
#
# Same status code. Deciding from the status alone is guaranteed to be wrong
# half the time, so providers must parse the body and raise the right class.


class LLMError(Exception):
    """Base. Carries the provider and model so logs identify the culprit."""

    def __init__(self, message: str, *, provider: str = "", model: str = "", status: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status = status


class LLMTransientError(LLMError):
    """Worth retrying: ordinary rate limits, 5xx, timeouts, network blips."""


class LLMPermanentError(LLMError):
    """Not worth retrying with this model: bad key, model retired, quota
    structurally zero, malformed request. Retrying burns budget for nothing;
    the correct response is to fail over or stop."""


class LLMParseError(LLMError):
    """The model answered, but the answer could not be turned into a call.
    Distinct from the above because the fix is a repair prompt, not a retry."""


@runtime_checkable
class LLMProvider(Protocol):
    """What the agent loop depends on. Nothing more.

    A Protocol, not an ABC: providers stay plain classes with no import of
    ours, so structural conformance is checked without inheritance. Adding
    OpenAI or Ollama later means writing a class with this shape — no
    registration, no base class, no changes here.
    """

    name: str
    model: str

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        """One turn. Raises LLMTransientError / LLMPermanentError / LLMParseError.

        Note what is NOT here: no streaming, no async, no retries, no
        fallback. Retry and failover are the router's job (deferred to M11),
        and a provider that owned them could not be composed with one that did
        not.
        """
        ...
