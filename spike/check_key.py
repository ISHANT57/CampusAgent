"""Throwaway: is a given key live? Usage: python check_key.py <key>

Uses GET /v1/models — the cheapest possible auth probe. It costs nothing,
consumes no tokens, and distinguishes the three cases that matter:
  401 = key is invalid/revoked
  429 = key is VALID but out of quota   <- easy to misread as "broken"
  200 = key works, and we learn what it can reach
"""
import sys

import httpx

key = sys.argv[1] if len(sys.argv) > 1 else ""

r = httpx.get(
    "https://api.openai.com/v1/models",
    headers={"Authorization": f"Bearer {key}"},
    timeout=30,
)
print("status:", r.status_code)
body = r.text
print("body:", body[:900])

if r.status_code == 200:
    ids = sorted(m["id"] for m in r.json().get("data", []))
    print(f"\n{len(ids)} models reachable. Sample:")
    for i in ids[:25]:
        print(" ", i)
