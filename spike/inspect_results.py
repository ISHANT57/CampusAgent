"""Dump a results JSONL readably. Throwaway helper.

    python inspect_results.py e1_connectivity [--full]
"""
import json
import pathlib
import sys

name = sys.argv[1] if len(sys.argv) > 1 else "e1_connectivity"
full = "--full" in sys.argv
path = pathlib.Path(__file__).parent / "results" / f"{name}.jsonl"

for line in path.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    print("=" * 72)
    print(f"{r['provider']} | {r['model']} | status {r['status']} | {r['latency_ms']} ms")
    if r.get("rate_headers"):
        print("rate_headers:", json.dumps(r["rate_headers"], indent=2))
    if r["status"] != 200 or full:
        print("BODY:", json.dumps(r["raw"], indent=2)[: (20000 if full else 1500)])
    if r.get("error"):
        print("ERROR:", r["error"])
