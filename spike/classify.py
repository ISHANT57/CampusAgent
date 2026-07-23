"""Failure taxonomy for M0.

The single most important design decision in this spike: FORMAT failure and
SELECTION failure are classified separately.

  format failure    -> the reply could not be mechanically turned into a call.
                       Cause: the model was not trained on this output format.
                       Fixable by ENGINEERING (parsers, repair loops, prompting).

  selection failure -> the reply parsed perfectly and chose the wrong tool.
                       Cause: the model did not understand the task or the tools.
                       NOT fixable by parsing. Needs better descriptions, or a
                       better model.

A model at 70% format / 95% selection is salvageable. A model at 99% format /
50% selection is useless. Measuring only "did the tool call succeed" makes
those two look identical, and sends you debugging the wrong layer for weeks.

Pure functions over recorded responses — no network. That is deliberate: a new
failure class can be added later and the whole corpus re-scored for free.
"""

from __future__ import annotations

import json
import re

from fixtures import TOOL_NAMES

# Format classes, worst-to-best ordering is not implied — these are disjoint.
OK = "OK"
NO_CALL = "NO_CALL"                    # answered in prose, no tool invoked
MULTI_CALL = "MULTI_CALL"              # emitted several calls when one was asked for
MALFORMED_JSON = "MALFORMED_JSON"      # arguments string would not parse
SCHEMA_VIOLATION = "SCHEMA_VIOLATION"  # parsed, but required arg missing / wrong type
HALLUCINATED_TOOL = "HALLUCINATED_TOOL"
HALLUCINATED_ARG = "HALLUCINATED_ARG"
TRUNCATED = "TRUNCATED"                # hit max_tokens mid-output
REFUSED = "REFUSED"                    # safety refusal
API_ERROR = "API_ERROR"                # never reached the model

FENCED = "FENCED"        # ```json wrapper       — prompted-JSON path only
PREAMBLE = "PREAMBLE"    # "Sure! Here's: {...}" — prompted-JSON path only

RECOVERABLE = {MALFORMED_JSON, SCHEMA_VIOLATION, HALLUCINATED_ARG, FENCED, PREAMBLE, TRUNCATED}

_REQUIRED = {
    "knowledge_search": {"query"},
    "knowledge_list_documents": set(),
    "knowledge_read_document": {"document_id"},
    "web_search": {"query"},
    "web_read": {"url"},
    "calculator": {"expression"},
    "final_answer": {"answer"},
}

_ALLOWED = {
    "knowledge_search": {"query", "top_k"},
    "knowledge_list_documents": {"status"},
    "knowledge_read_document": {"document_id"},
    "web_search": {"query", "max_results"},
    "web_read": {"url"},
    "calculator": {"expression"},
    "final_answer": {"answer"},
}

_REFUSAL = re.compile(
    r"\b(i (can'?t|cannot|am unable to)|i'?m not able to|as an ai)\b", re.IGNORECASE
)


def classify_format(envelope: dict, view: dict) -> tuple[str, str]:
    """Return (format_class, detail). Never raises."""
    if not envelope.get("ok"):
        raw = envelope.get("raw") or {}
        msg = (raw.get("error") or {}).get("message") or envelope.get("error") or ""
        return API_ERROR, f"{envelope.get('status')}: {str(msg)[:160]}"

    calls = view.get("tool_calls") or []
    text = view.get("text") or ""
    finish = (view.get("finish_reason") or "").lower()

    if not calls:
        if finish in {"length", "max_tokens"}:
            return TRUNCATED, f"finish_reason={finish}, no call emitted"
        if _REFUSAL.search(text):
            return REFUSED, text[:160]
        return NO_CALL, text[:160]

    if len(calls) > 1:
        return MULTI_CALL, f"{len(calls)} calls: {[c.get('name') for c in calls]}"

    call = calls[0]

    if call.get("parse_error") and call["parse_error"] != "NON_SPEC_OBJECT_ARGS":
        return MALFORMED_JSON, f"{call['parse_error']} | raw={str(call.get('arguments_raw'))[:120]}"

    name = call.get("name")
    if name not in TOOL_NAMES:
        return HALLUCINATED_TOOL, f"called '{name}'"

    args = call.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return SCHEMA_VIOLATION, f"arguments not an object: {type(args).__name__}"

    missing = _REQUIRED[name] - set(args)
    if missing:
        return SCHEMA_VIOLATION, f"missing required: {sorted(missing)}"

    extra = set(args) - _ALLOWED[name]
    if extra:
        return HALLUCINATED_ARG, f"invented args: {sorted(extra)}"

    if name == "knowledge_read_document" and not isinstance(args.get("document_id"), int):
        return SCHEMA_VIOLATION, f"document_id not an integer: {args.get('document_id')!r}"

    return OK, name


def classify_selection(view: dict, goal: dict) -> tuple[bool | None, str]:
    """Was the RIGHT tool chosen? Returns (correct, detail).

    None means "not scored" — selection is only meaningful on a reply that
    parsed. Scoring an unparseable reply as a selection failure would blend the
    two failure types back together, which is exactly what this file exists to
    prevent.
    """
    calls = view.get("tool_calls") or []
    if len(calls) != 1:
        return None, "not scored (no single parseable call)"

    chosen = calls[0].get("name")
    ok_set = {goal["expected"], *goal.get("acceptable", [])}
    if chosen in ok_set:
        return True, chosen
    return False, f"chose {chosen}, expected {goal['expected']}"


def classify(envelope: dict, view: dict, goal: dict) -> dict:
    fmt, fmt_detail = classify_format(envelope, view)
    if fmt == OK:
        sel, sel_detail = classify_selection(view, goal)
    else:
        sel, sel_detail = None, "not scored"
    return {
        "format_class": fmt,
        "format_detail": fmt_detail,
        "selection_ok": sel,
        "selection_detail": sel_detail,
        "recoverable": fmt in RECOVERABLE,
    }


def extract_json_loosely(text: str) -> tuple[dict | None, str]:
    """Best-effort JSON recovery from free text — the core of the prompted-JSON
    path (M10). Returns (parsed, how). Used by E7 to measure how much of the
    failure rate is recoverable WITHOUT another LLM call.
    """
    if not text:
        return None, "empty"

    try:
        return json.loads(text), "clean"
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1)), "fenced"
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate), "braces"
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(repaired), "braces+trailing_comma"
            except json.JSONDecodeError:
                pass

    return None, "unrecoverable"
