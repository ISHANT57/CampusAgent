"""Probe which Gemini models this key can actually CALL (not just list).

ListModels tells you what exists. It does not tell you what has free-tier
quota — gemini-2.0-flash listed fine and returned `limit: 0` on use. The only
way to know is to call it.

One trivial call per model, sleeping between so we do not create the very
rate-limiting we are trying to measure.
"""
import time

from providers import call_gemini, peek, record

CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
]

MESSAGES = [{"role": "user", "content": "Reply with exactly the word: pong"}]

print(f"{'model':32s} {'status':>7} {'ms':>7} {'ptok':>6} {'ctok':>6}  note")
print("-" * 85)

for model in CANDIDATES:
    env = call_gemini(model, MESSAGES)
    view = peek(env)
    record("e1_gemini_probe", {**env, "peek": view})

    note = ""
    if not env["ok"]:
        raw = env.get("raw") or {}
        note = str((raw.get("error") or {}).get("message", env.get("error")))[:110]
    else:
        note = (view["text"] or "")[:40]

    print(
        f"{model:32s} {str(env['status']):>7} {env['latency_ms']:>7} "
        f"{str(view['usage'].get('prompt')):>6} {str(view['usage'].get('completion')):>6}  {note}"
    )
    time.sleep(5)
