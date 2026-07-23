# Deployment

**Mode: BYOK-only.** Users bring their own AI provider. Hosted Trial Mode is
deferred and **disabled by default** — leave `HOSTED_API_KEY` unset and
`resolve()` refuses trial mode cleanly with `reason="hosted_unconfigured"`,
which a client renders as "connect a provider". There is a test for it.

## What this project actually needs

| Service | Needed | Note |
|---|---|---|
| **Render** | ✅ | the backend |
| **Neon Postgres** | ✅ | runs + steps. **A different database from Project 1's.** |
| Project 1 on Render | ✅ | reached over HTTPS with `X-API-Key` |
| Tavily | optional | only for `web_search` |
| **Vercel** | ❌ not yet | there is no frontend. That is M45. |
| **Supabase Storage** | ❌ never | this project stores no blobs |
| **Qdrant** | ❌ never | Project 1 owns the vectors; P2 reaches it over HTTP only |

Adding Supabase or Qdrant here would break the service boundary the whole
two-repo design exists to maintain.

---

## 1. Render

**New → Web Service → connect `ISHANT57/CampusAgent`.**

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Runtime | Docker |
| Instance Type | Free |
| Health Check Path | `/health` |

`/health` is deliberately dependency-free — it must not check Postgres, or a
30-second Neon blip would make Render kill and restart a healthy process,
turning a wobble into a cold start on top of it. Use `/health/deps` to check
dependencies by hand.

## 2. Environment variables

```bash
# --- Required ---------------------------------------------------------------
DATABASE_URL=postgresql+psycopg://...   # scheme is +psycopg, NOT +psycopg2
APP_SECRET=                             # secrets.token_urlsafe(32)
KNOWLEDGE_BASE_URL=https://campusbrain.onrender.com
KNOWLEDGE_BASE_API_KEY=                 # must equal P1's SERVICE_API_KEY

# --- CORS: set the REAL origin once a frontend exists ------------------------
# "*" and credentialed CORS are mutually exclusive — browsers reject the
# combination, the identity cookie is never sent, and every request looks like
# a brand-new visitor. app/main.py detects "*" and disables credentials rather
# than shipping a broken pairing.
CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app

# --- Optional ---------------------------------------------------------------
TAVILY_API_KEY=
LOG_LEVEL=INFO

# --- Hosted trial: LEAVE UNSET -----------------------------------------------
# Setting this enables trial mode, which currently has NO QUOTA ENFORCEMENT.
# Anyone could exhaust the key. Do not set it until P3 ships.
# HOSTED_API_KEY=
```

⚠️ `DATABASE_URL` must use `postgresql+psycopg://`. Neon's dashboard copies
plain `postgresql://`, which SQLAlchemy resolves to psycopg2 — not installed
here. `config.py` rejects a psycopg2 URL by name rather than letting it fail
deep inside the dialect loader.

## 3. Verify

```bash
curl https://<app>.onrender.com/health          # {"status":"ok"}
curl https://<app>.onrender.com/health/deps     # database ok

curl -i -X POST https://<app>.onrender.com/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"goal":"What is the minimum CGPA for a Sitare scholarship?",
       "byok":{"provider":"gemini","api_key":"YOUR_KEY"}}'
# 202 + run_id, Set-Cookie: cb_identity=...

curl -N -b "cb_identity=<from above>" \
  https://<app>.onrender.com/api/v1/runs/<id>/events
```

Without `HOSTED_API_KEY`, omitting `byok` returns **400
`hosted_unconfigured`** — the intended BYOK-only behaviour.

---

## What is protected

| Control | Where |
|---|---|
| Rate limiting | `10/min` create, `120/min` read, keyed per browser not per IP |
| Ownership (IDOR) | run reads require the identity that created them; 404 not 403 |
| Credential redaction | on **write**, so keys never reach Postgres |
| SSRF | provider base URLs and `web_read` resolve hosts and reject non-public addresses |
| Code execution | `calculator` is an AST allow-list, not a sandbox |
| Abandoned runs | reaped at startup |

## Known limits

**One worker, deliberately.** Project 1 OOM'd on 512 Mi with four. Rate-limit
counters and background runs are both per-process, so a second worker would
halve the effective limit and split run state.

**Free tier sleeps after ~15 min idle.** The first request cold-starts (~50 s).
`POST /runs` returns 202 before any provider call, so acceptance is fast once
awake.

**In-process execution.** A restart loses running work. The trace survives and
the reaper marks it failed at next startup — nothing is left permanently in
`running`, but the run does not resume.

**No frontend.** The API is usable with curl or the CLI. M45.

## Deferred, with the trigger

| Item | Ships when |
|---|---|
| Hosted Trial Mode + quota | after the product is stable — needs global ceiling, per-identity limits, abuse protection, monitoring |
| Frontend | M45 |
| Identity-scoped repository | the next run-reading endpoint — currently ownership is enforced at the endpoint, not structurally |
| Run resumption | a restart losing work becomes painful |
| Shared rate-limit store | instance #2 |
