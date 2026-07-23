"""M0 fixture corpus: 6 tools + 12 labelled goals.

Every goal is grounded in a fact that actually exists in Project 1's Sitare
corpus (CollegeRag/resources/*.md) — CGPA >= 6.5 for scholarship retention,
hostel entry/exit 6:00 AM - 9:00 PM, JEE Mains percentile >= 85, 91.3%
placement rate. Invented goals would measure the model against a world the
knowledge base does not contain.

LABELS WERE WRITTEN BEFORE ANY MODEL OUTPUT WAS OBSERVED. That ordering is not
a formality: labelling after seeing responses means grading the model against
whatever it happened to do, which produces a flattering number and no signal.

This file graduates into the M39 golden set. Treat edits to it as edits to the
project's definition of "correct".
"""

# ---------------------------------------------------------------------------
# TOOLS
#
# Descriptions are the tool-selection algorithm — the model chooses by reading
# them and nothing else. Each states WHAT it does, WHEN to use it, and WHAT IT
# RETURNS. That third part is what stops the model reaching for
# knowledge_search on a whole-document summarisation task.
#
# Args are flat scalars, not nested objects: Gemini takes an OpenAPI 3.0
# subset rather than JSON Schema, so nested models ($ref/$defs) are a portability
# risk. E6 tests whether that caution was warranted.
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": (
                "Search the university's internal document corpus and return the most "
                "relevant PASSAGES, each with its source document, page number, and a "
                "relevance score. Use this for questions about official university "
                "policies, rules, curriculum, admissions, fees, placements, or campus "
                "information. Returns short fragments, NOT whole documents — do not use "
                "it to summarise or read an entire document."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query. Use specific terms from the question.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many passages to return. Default 5, maximum 20.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_list_documents",
            "description": (
                "List the documents available in the university's knowledge base, with "
                "each document's id, filename, and page count. Use this FIRST when you "
                "need to know what documents exist — for example before summarising or "
                "reading a whole document, or to check whether the corpus covers a topic "
                "at all. Returns an inventory, not any document content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional filter, e.g. 'processed'.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_read_document",
            "description": (
                "Read the FULL text of one document from the knowledge base, page by "
                "page. Use this when you need complete coverage of a document — "
                "summarising it, listing all of its rules, or comparing it in full "
                "against another source. Requires a document_id, which you get from "
                "knowledge_list_documents. Returns the entire document text, not fragments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "integer",
                        "description": "The document's id, from knowledge_list_documents.",
                    }
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public internet and return results with extracted page "
                "content. Use this ONLY for information that is not in the university's "
                "internal corpus: current events, information published after the corpus "
                "was created, external companies, job or internship openings elsewhere, "
                "or anything explicitly about 'the latest' or 'current' state of the "
                "outside world."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The web search query."},
                    "max_results": {
                        "type": "integer",
                        "description": "How many results to return. Default 5.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": (
                "Fetch one specific URL and return its readable text content. Use this "
                "when the user gives you a URL directly, or when a web_search result "
                "needs to be read in full. Requires a complete URL including https://."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL including https://."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate one arithmetic or comparison expression and return the result. "
                "Use this whenever a number must be computed or compared — differences, "
                "percentages, thresholds, eligibility checks. Accepts only arithmetic and "
                "comparison operators, e.g. '6.5 - 6.2' or '7.4 >= 6.5'. It cannot look "
                "anything up; you must already know the numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A single arithmetic or comparison expression.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": (
                "Give the final answer to the user and end the task. Use this as soon as "
                "you can answer completely — including when the question needs no tools "
                "at all, or when you must tell the user you cannot answer or need "
                "clarification from them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "The complete answer."}
                },
                "required": ["answer"],
            },
        },
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS]


# ---------------------------------------------------------------------------
# GOALS
#
# `expected` = the tool a competent agent should call FIRST.
# `acceptable` = other defensible first moves; scored as correct. Used only
#   where the goal is genuinely ambiguous — an agent evaluation that demands
#   one path when several are valid measures conformity, not competence.
# `why` = the reasoning being tested. If a model fails, this says what it
#   failed to understand.
# ---------------------------------------------------------------------------

GOALS = [
    {
        "id": "G01",
        "goal": "What is the minimum CGPA I need to keep my Sitare scholarship?",
        "expected": "knowledge_search",
        "acceptable": [],
        "kind": "simple_retrieval",
        "why": "Single-hop internal policy lookup. The baseline case — if this fails, nothing else matters.",
    },
    {
        "id": "G02",
        "goal": "Summarise all of the hostel rules for me — I want the complete list, not just highlights.",
        "expected": "knowledge_list_documents",
        "acceptable": ["knowledge_read_document"],
        "kind": "whole_document",
        "why": (
            "HARD. Whole-document task. top-k fragments cannot produce a complete summary, "
            "so the correct move is to find the document then read it. Tests whether the "
            "model actually read 'Returns short fragments, NOT whole documents' in "
            "knowledge_search's description. Expected failure: knowledge_search."
        ),
    },
    {
        "id": "G03",
        "goal": "My CGPA is 6.2. Do I still qualify for the scholarship, and if not, how far short am I?",
        "expected": "knowledge_search",
        "acceptable": [],
        "kind": "retrieve_then_compute",
        "why": (
            "HARD. Ordering test. The threshold (6.5) must be retrieved before anything "
            "can be computed. Expected failure: calling calculator first on a number it "
            "does not have yet."
        ),
    },
    {
        "id": "G04",
        "goal": "What is 6.5 minus 6.2?",
        "expected": "calculator",
        "acceptable": [],
        "kind": "pure_compute",
        "why": "No retrieval needed. Tests that the model does not reflexively search for everything.",
    },
    {
        "id": "G05",
        "goal": "Who founded Sitare University, and in what year?",
        "expected": "knowledge_search",
        "acceptable": [],
        "kind": "simple_retrieval",
        "why": "Factual internal lookup. A model with this in its pretraining may skip the tool — that is a WRONG_TOOL failure, since the corpus is the authority here.",
    },
    {
        "id": "G06",
        "goal": "Find the latest AI/ML internship openings at Indian startups posted this month.",
        "expected": "web_search",
        "acceptable": [],
        "kind": "external_current",
        "why": "Explicitly outside the corpus and time-sensitive. Expected failure: knowledge_search, because the word 'internship' appears heavily in the corpus.",
    },
    {
        "id": "G07",
        "goal": "Compare the hostel entry and exit timings in our documents against what Sitare's website currently says.",
        "expected": "knowledge_search",
        "acceptable": ["web_search", "knowledge_list_documents", "web_read"],
        "kind": "ambiguous_dual_source",
        "why": (
            "DELIBERATELY AMBIGUOUS. Both sources are required; either is a defensible "
            "first step. Scored as correct for any of them. This case exists to check "
            "the model does not freeze or hallucinate a combined tool when two are needed."
        ),
    },
    {
        "id": "G08",
        "goal": "Read https://www.sitare.org/ and tell me the current admission application deadline.",
        "expected": "web_read",
        "acceptable": [],
        "kind": "explicit_url",
        "why": "A URL is handed over directly. Expected failure: web_search, ignoring the URL it was given.",
    },
    {
        "id": "G09",
        "goal": "Hi! What kinds of things can you help me with?",
        "expected": "final_answer",
        "acceptable": [],
        "kind": "no_tool_needed",
        "why": (
            "HARD. Correct answer is NO TOOL. Tests over-triggering — an agent that "
            "searches the corpus for its own capabilities burns a step and looks broken. "
            "Expected failure: knowledge_search."
        ),
    },
    {
        "id": "G10",
        "goal": "Tell me about the fee structure at Sitare.",
        "expected": "knowledge_search",
        "acceptable": [],
        "kind": "simple_retrieval",
        "why": "Vague but clearly internal. Tests that a loosely-worded question still routes to the corpus.",
    },
    {
        "id": "G11",
        "goal": "How many documents are currently in the knowledge base, and what are they?",
        "expected": "knowledge_list_documents",
        "acceptable": [],
        "kind": "inventory",
        "why": "Asks about the corpus itself, not its content. Tests the three knowledge_* tools are actually distinguishable. Expected failure: knowledge_search.",
    },
    {
        "id": "G12",
        "goal": "I scored 84 percentile in JEE Mains. Am I eligible to apply to Sitare, and if not, by how many percentile points did I miss?",
        "expected": "knowledge_search",
        "acceptable": [],
        "kind": "retrieve_then_compute",
        "why": (
            "Multi-hop: retrieve the >=85 threshold, then compute the shortfall. This is "
            "the goal carried into E5 (multi-turn) — after a tool result arrives, does "
            "the model correctly take a SECOND action instead of stalling or repeating?"
        ),
    },
]


SYSTEM_PROMPT = (
    "You are an autonomous assistant for Sitare University students. You have tools "
    "available. Choose exactly ONE tool to call as your next action, and call it. "
    "Do not answer from your own knowledge when a tool can provide grounded "
    "information. If you can already answer completely, or the question needs no "
    "tool at all, call final_answer."
)
