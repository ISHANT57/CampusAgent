"""The decision-making core: one LLM call -> what to do next.

Everything here descends from M0's central finding, that two completely
different failures look identical if you only ask "did I get a tool call?":

  FORMAT failure     the reply could not be turned into a call.
                     Cause: output format. Fixable by ENGINEERING.
  SELECTION failure  the reply parsed perfectly and chose wrong.
                     Cause: reasoning. NOT fixable by parsing.

This module handles the first kind explicitly and lets the second kind through
to be measured, because pretending a wrong choice is a parse problem would send
you debugging the wrong layer for weeks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.llm.base import (
    LLMError,
    LLMParseError,
    LLMPermanentError,
    LLMProvider,
    Message,
    ToolCall,
)
from app.tools.registry import ToolRegistry

# Applies ONLY before any tool has run. See the no-tool-call branch below for
# why the threshold cannot be applied unconditionally.
MIN_OPENING_ANSWER_CHARS = 40


class Outcome(str, Enum):
    ACT = "act"          # a tool call was produced
    DONE = "done"        # the model answered instead of calling a tool
    RETRY = "retry"      # recoverable: transient error, or an empty reply
    FAILED = "failed"    # unrecoverable: permanent provider error


@dataclass
class Decision:
    outcome: Outcome
    thought: str | None = None
    tool_call: ToolCall | None = None
    answer: str | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


def next_action(
    provider: LLMProvider,
    registry: ToolRegistry,
    messages: list[Message],
    *,
    has_evidence: bool = False,
) -> Decision:
    """Ask the model for one action.

    `has_evidence` — whether any tool has already produced an observation in
    this run. It changes how a text-only reply is interpreted; see the
    no-tool-call branch.

    Never raises. The loop above must always get a Decision it can act on,
    including when the provider is down — otherwise a run dies with a stack
    trace instead of a partial answer.
    """
    try:
        completion = provider.complete(messages, tools=registry.specs())
    except LLMPermanentError as e:
        # Bad key, retired model, structurally-zero quota. Retrying cannot
        # succeed and would burn the run's remaining budget (M0/F2).
        return Decision(outcome=Outcome.FAILED, error=f"LLM permanently unavailable: {e}")
    except LLMParseError as e:
        # A FORMAT failure, not an outage — the model answered, and what it
        # produced could not be turned into a call. Some providers (Groq)
        # validate tool arguments server-side and reject them with a 4xx; others
        # return malformed JSON we fail to parse ourselves.
        #
        # Retrying the identical prompt reproduces the identical bad generation,
        # which is exactly what used to happen: three retries, same failure,
        # give up. The fix is a REPAIR turn — tell the model what was wrong and
        # let it correct itself. This is M10's repair loop, which M0 deferred
        # because native tool calling looked reliable; Groq's server-side
        # validation is what made it necessary.
        #
        # Provider-agnostic on purpose: any provider that can produce a parse
        # error gets the same treatment.
        return Decision(
            outcome=Outcome.RETRY,
            error=(
                "Your previous tool call was rejected because its arguments did not "
                "match the tool's schema. Read the tool definition again and call it "
                "with exactly the fields it declares, using the correct types — "
                "numbers unquoted, strings quoted, and no invented parameters. "
                f"The provider reported: {e}"
            ),
        )
    except LLMError as e:
        # Transient — rate limit, 5xx, timeout. Worth another turn unchanged.
        return Decision(outcome=Outcome.RETRY, error=f"LLM temporarily unavailable: {e}")
    except Exception as e:  # noqa: BLE001
        # The catch-all that makes "the loop never raises" actually true. A
        # provider is third-party code; it can raise something outside our
        # hierarchy (a JSON decode error, an httpx internal, a bug of ours).
        # Without this the exception escapes the loop and the run dies with a
        # stack trace instead of returning whatever it had already established.
        # Treated as FAILED, not RETRY: an unrecognised error is not known to
        # be transient, and retrying an unknown fault burns budget blindly.
        return Decision(outcome=Outcome.FAILED, error=f"LLM call raised {type(e).__name__}: {e}")

    common = {
        "prompt_tokens": completion.usage.prompt_tokens,
        "completion_tokens": completion.usage.completion_tokens,
        "latency_ms": completion.latency_ms,
        "model": completion.model,
    }
    text = (completion.text or "").strip()
    call = completion.tool_call

    # --- a tool call was produced ------------------------------------------
    if call is not None:
        if registry.get(call.name) is None:
            # HALLUCINATED_TOOL. Never observed in M0's 180 samples, so this is
            # a guard rather than a workaround — and it is deliberately NOT a
            # fuzzy name match, which would mask a signal worth seeing.
            return Decision(
                outcome=Outcome.RETRY,
                thought=text or None,
                error=(
                    f"You called {call.name!r}, which does not exist. "
                    f"Available tools: {', '.join(registry.names())}."
                ),
                **common,
            )
        return Decision(outcome=Outcome.ACT, thought=text or None, tool_call=call, **common)

    # --- no tool call ------------------------------------------------------
    #
    # This branch replaces an explicit final_answer tool: completion is
    # inferred from "the model stopped requesting tools".
    #
    # M0 classified NO_CALL — answering in prose instead of acting — as a
    # FAILURE, measured at 1/36 on one model. Read every no-call reply as
    # "done" and each of those becomes a silent success: the agent abandons the
    # task and reports an answer it never grounded.
    #
    # A first attempt gated ALL text-only replies on a length threshold. That
    # was wrong, and a live run proved it: asked "what is 6.5 minus 6.2?", the
    # agent ran calculator, got 0.3, answered "0.3" — and the threshold
    # rejected it three times until the run failed. A correct answer is not
    # obliged to be long.
    #
    # The real signal is not length, it is whether the model DID THE WORK:
    #
    #   evidence gathered  -> any non-empty reply is a legitimate answer, at
    #                         any length. It followed the loop to a conclusion.
    #   no evidence yet    -> a very short reply on turn one is the NO_CALL
    #                         failure ("Okay."), while a substantial one is a
    #                         genuine direct answer to a question needing no
    #                         tools.
    if not text:
        return Decision(
            outcome=Outcome.RETRY,
            error="The model returned an empty response. Call a tool or give the final answer.",
            **common,
        )

    if has_evidence or len(text) >= MIN_OPENING_ANSWER_CHARS:
        return Decision(outcome=Outcome.DONE, answer=text, **common)

    return Decision(
        outcome=Outcome.RETRY,
        error=(
            "That reply neither called a tool nor gave a complete answer. "
            "Either call a tool to make progress, or give the full final answer."
        ),
        **common,
    )
