# Integration Contract — Project 2 → Project 1

**Project 1:** CampusBrain Enterprise RAG Platform (`D:\Goqii-Scan\CollegeRag`)
**Project 2:** CampusBrain Agent (this repo)

Project 1 remains an independent Knowledge Service. This document is the
complete, minimal set of changes Project 2 requires from it — each one
justified before it is proposed, per Decision 1.

---

## 1. What Project 2 consumes today, unchanged

| P1 endpoint | Used by | Notes |
|---|---|---|
| `POST /api/v1/search` | `knowledge_search` tool | Returns `hits[]` with `score, chunk_id, document_id, page_number, text` — exactly the primitive an agent needs |
| `GET /health` | dependency probe | Unchanged |

## 2. What Project 2 deliberately does NOT consume

| P1 endpoint | Why not |
|---|---|
| `POST /api/v1/chat` | It is a **complete second LLM agent** — it retrieves, prompts a model, and returns prose. Calling it from an agent means LLM-over-LLM: double latency, double cost, and the evidence (scores, page numbers) is discarded before P2's planner ever sees it. P2 wants passages and does its own reasoning. |
| `POST /api/v1/documents` (upload) | P2 never ingests. The service key it holds must not carry write access. |
| `POST /api/v1/auth/login` | P2 is not a human. See P1-CHANGE-1. |
| P1's Qdrant / Postgres / Supabase, directly | The whole point of the boundary. P2 reaches P1 over HTTP or not at all. |

**Consequence:** five gaps identified in the original review — `chunk_id` in
`Citation`, a structured no-evidence flag, collections CRUD, `/chat` rate-limit
exemption, and streaming — are **deferred, not requested**. They only matter to
a consumer of `/chat`, and P2 is not one.

---

## 3. Requested changes

Four changes. All additive. All backward-compatible. None alters an existing
response shape. Estimated total: ~65 lines.

---

### P1-CHANGE-1 — Service authentication on `/search`

**Priority:** blocker · **Needed by:** M15

**Why this is necessary.**
P1's only credential today is a human JWT from `POST /auth/login`
([`app/api/v1/auth.py:21`](../CollegeRag/backend/app/api/v1/auth.py#L21)),
which requires an email and password, expires in 60 minutes
([`app/core/config.py:28`](../CollegeRag/backend/app/core/config.py#L28)), and
is rate-limited to 5/min. `/search` is gated behind
`require_role(ADMIN, SUPER_ADMIN)`
([`app/api/v1/search.py:22`](../CollegeRag/backend/app/api/v1/search.py#L22)).

For P2 to search, it would have to store an administrator's password and
re-login hourly. That is wrong on three counts:

1. **Credential class mismatch.** A machine holding a human password means the
   audit log cannot distinguish a person from a process.
2. **Over-privilege.** An admin JWT also authorizes `POST /documents` — P2
   would hold document-upload rights it must never have.
3. **Fragility.** Hourly re-login against a 5/min limiter, on a service that
   cold-starts, is an availability problem invented for no reason.

**Proposed change.** A read-only service credential, checked as an alternative
to the JWT on `/search` only.

```python
# app/core/dependencies.py  (additive)
def require_search_access(
    api_key: str | None = Header(None, alias="X-API-Key"),
    credentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> int:
    """Return the org_id allowed to search. Accepts either a service API key
    (read-only, machine callers) or an admin JWT (humans, unchanged)."""
    if api_key and settings.service_api_key and secrets.compare_digest(
        api_key, settings.service_api_key
    ):
        return settings.service_api_key_org_id
    return require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)(credentials, db).org_id
```

`/search` changes from `current_user: User = Depends(require_role(...))` to
`org_id: int = Depends(require_search_access)` — and it already only uses
`current_user.org_id`, so the body is unchanged.

New settings: `SERVICE_API_KEY` (unset = feature off), `SERVICE_API_KEY_ORG_ID`
(default 1).

**Security notes.**
- Constant-time comparison (`secrets.compare_digest`), not `==`.
- Grants **read on `/search` only**. Not documents, not auth, not upload.
- Unset by default — existing deployments are unaffected.
- Rate limiting: the service key gets its own bucket via `rate_limit_key`,
  which already prefers an identity over an IP
  ([`app/core/rate_limit.py:11`](../CollegeRag/backend/app/core/rate_limit.py#L11)).
  Add `api_key:<hash>` as a key type. Without this, P2 shares one IP bucket
  with everything else behind its egress address.

**Backward compatibility:** total. Admin JWTs keep working identically.

---

### P1-CHANGE-2 — `GET /api/v1/documents` (list)

**Priority:** blocker · **Needed by:** M17

**Why this is necessary.**
P1 exposes `GET /documents/{id}` but no listing
([`app/api/v1/documents.py`](../CollegeRag/backend/app/api/v1/documents.py)).
An agent cannot plan against knowledge it cannot enumerate.

Concretely: given *"summarize the hostel rules,"* the agent's first decision is
**which document to work from**. Without a listing it can only fire a semantic
search and hope the top-k fragments came from the right file. With a listing it
can see `hostel_rules_2026.pdf` exists, then read it directly. This is the
difference between an agent that plans and one that gropes.

It also enables honest refusal: if no document plausibly covers the goal, the
agent should say so and pivot to web search rather than returning weak
fragments.

**Proposed change.**
```
GET /api/v1/documents?status=processed&collection_id=&limit=100&offset=0
→ { "documents": [DocumentRead, ...], "total": int }
```
Same auth dependency as P1-CHANGE-1. Uses the existing `DocumentRepository`
and the existing `DocumentRead` schema — no new response model.

**Effort:** ~15 lines. **Backward compatibility:** new route, nothing touched.

---

### P1-CHANGE-3 — `GET /api/v1/documents/{id}/text`

**Priority:** blocker · **Needed by:** M17

**Why this is necessary.**
This is the gap that makes Vision Goal A impossible as stated.

> *"Summarize the hostel rules from the uploaded documents…"*

Summarization is a **whole-document** operation. P1's retrieval returns at most
20 chunks ranked by similarity to a query
([`app/schemas/chat.py`](../CollegeRag/backend/app/schemas/chat.py), `top_k ≤ 20`).
A summary built from the 5 fragments most similar to the phrase "hostel rules"
is not a summary of the hostel rules — it is a summary of the parts that happen
to repeat that phrase. Sections about visitor policy or fee timelines, which any
correct summary must include, may never rank.

There is no workaround on P2's side. Iterating `knowledge_search` with varied
queries to reconstruct a document is guesswork, burns quota, and has no
termination condition.

**Proposed change.**
```
GET /api/v1/documents/{id}/text?page_from=&page_to=
→ { "document_id": int, "filename": str, "page_count": int,
    "pages": [ { "page_number": int, "text": str } ] }
```

The data already exists and is already assembled — `chunks` rows carry
`document_id`, `page_number`, `chunk_index`, and `text`
([`app/models/chunk.py`](../CollegeRag/backend/app/models/chunk.py)). This is
one ordered query plus a group-by. **No re-extraction, no storage read, no OCR.**

**Bound it:** cap the response (e.g. 200 pages or ~1 MB) and require the page
range beyond that. An unbounded full-text endpoint on a 512 Mi instance is an
OOM waiting to happen — P1 already learned this lesson with uvicorn workers
([`DEPLOYMENT_JOURNAL.md`](../CollegeRag/DEPLOYMENT_JOURNAL.md)).

**Effort:** ~20 lines. **Backward compatibility:** new route.

---

### P1-CHANGE-4 — Optional scope filters on `SearchRequest`

**Priority:** nice-to-have · **Needed by:** Phase 4 (M27) · **Defer until then**

**Why this would help.**
Once the agent knows a document exists (P1-CHANGE-2), the natural next action is
*"search within it."* Today `SearchRequest` carries only `query`, `top_k`, and
`mode` ([`app/schemas/search.py`](../CollegeRag/backend/app/schemas/search.py)),
so every search hits the whole corpus. The agent must over-fetch and filter
client-side — wasteful, and it can silently drop the right passage below the
`top_k` cut.

**Proposed change.** Two optional fields, both defaulting to `None`:
```python
document_id: int | None = None
collection_id: int | None = None
```
- Keyword path: an extra `AND` in the existing SQL.
- Semantic path: a Qdrant payload filter — the payload **already carries**
  `document_id` and `org_id`
  ([`app/services/document_processing_service.py`](../CollegeRag/backend/app/services/document_processing_service.py)),
  so no re-indexing is needed.

**Effort:** ~15 lines. **Backward compatibility:** total — omitted fields
preserve current behaviour exactly.

**Why deferred:** P2 can function without it through Phase 3. Requesting it now
would violate "minimal and justified." Request it when M27 proves it's needed.

---

## 4. Change summary

| ID | Change | Priority | Lines | Breaks anything? |
|---|---|---|---|---|
| P1-CHANGE-1 | `X-API-Key` service auth on `/search` + own rate bucket | Blocker | ~25 | No |
| P1-CHANGE-2 | `GET /documents` list | Blocker | ~15 | No |
| P1-CHANGE-3 | `GET /documents/{id}/text` | Blocker | ~20 | No |
| P1-CHANGE-4 | Optional search scope filters | Deferred | ~15 | No |

Nothing here changes an existing response shape, removes a field, or alters an
existing auth path. P1's frontend, its student chat, and its admin flows are
untouched.

---

## 5. The boundary, restated

P1 gains **one new capability class**: it can now be read by a machine as well
as by a browser. That is the entire extent of the coupling.

P1 does not know Project 2 exists. It has no agent code, no agent dependency,
no awareness of runs, plans, or tools. If Project 2 were deleted tomorrow, the
four changes above would remain a strictly better read API for any future
consumer.

**The test for any future request against P1:** *does this make P1 a better
Knowledge Service on its own terms?* If the honest answer is "no, but Project 2
needs it," the feature belongs in Project 2 instead.

All four changes above pass that test — a listing endpoint, a full-text
endpoint, machine authentication, and scoped search are things any read API
should have had.

---

## 6. Contract stability

| Guarantee | Owner |
|---|---|
| P1's response shapes are additive-only | P1 |
| P2 maps every P1 type into its own (`SearchHit` → `Passage`) at the client boundary | P2 |
| P2 never imports P1 code, models, or database | P2 |
| P2 tolerates P1 being cold, slow, throttled, or down — degraded answer, never a 500 | P2 |
| P1 never depends on P2 in any direction | both |

The mapping layer ([`app/tools/knowledge.py`](backend/app/tools/knowledge.py),
per `ARCHITECTURE.md §B7`) is the single file that knows P1's wire format. That
is deliberate: swapping P1 for any other retrieval service is a one-file change.
