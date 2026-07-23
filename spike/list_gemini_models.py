"""Which Gemini models does THIS key actually have access to?

The E1 429 said `limit: 0` for gemini-2.0-flash — not "throttled", but "no
free-tier quota exists for this model on this project". Worth enumerating what
the key can actually reach before assuming the whole provider is unusable.
"""
import os
import pathlib

import httpx
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent / ".env")

r = httpx.get(
    "https://generativelanguage.googleapis.com/v1beta/models",
    headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", "")},
    timeout=30,
)
print("status", r.status_code)
data = r.json()

if "models" not in data:
    print(data)
    raise SystemExit(1)

for m in data["models"]:
    if "generateContent" not in (m.get("supportedGenerationMethods") or []):
        continue
    name = m["name"].replace("models/", "")
    print(f"{name:45s} in={m.get('inputTokenLimit'):>9} out={m.get('outputTokenLimit'):>7}")
