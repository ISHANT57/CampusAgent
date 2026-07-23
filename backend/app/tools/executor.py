"""Tool executor — the layer that guarantees no tool can break a run.

Everything a tool can do wrong is converted into a ToolResult here:

  unknown tool        -> failure, listing what IS available
  invalid arguments   -> failure, quoting the validation error so the model
                         can correct itself next turn
  timeout             -> unavailable (the tool may be fine, it was just slow)
  any exception       -> failure, with the exception type

The agent above this line never sees an exception, so a run only ends when the
budget says so or a terminal tool is called.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from pydantic import ValidationError

from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        # Per-run cache, keyed by (tool, args). Guards the most common loop
        # pathology — the agent calling the same tool with the same arguments
        # repeatedly — and saves free-tier quota, which M0 proved is the
        # binding constraint on this project.
        self._cache: dict[tuple[str, str], ToolResult] = {}
        # One pool, reused. Threads are how a hanging tool is bounded: Python
        # cannot kill a thread, so on timeout the worker is abandoned and its
        # result discarded.
        # ponytail: abandoned threads leak until they finish. Acceptable at
        # 15 steps/run and read-only tools; revisit if a tool can block forever.
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

    def reset(self) -> None:
        """Clear the cache between runs. Results are only valid within one run
        — the corpus or the web may have changed since the last one."""
        self._cache.clear()

    def execute(self, name: str, arguments: dict) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            # A recoverable observation, not an error. The model can read the
            # available names and pick again on the next turn.
            return ToolResult.failure(
                f"Unknown tool {name!r}. Available tools: {', '.join(self.registry.names())}."
            )

        try:
            args = tool.args_model.model_validate(arguments or {})
        except ValidationError as e:
            # The validation detail is deliberately included: it is the repair
            # instruction. "field required: query" tells the model exactly what
            # to fix, where a generic "invalid arguments" tells it nothing.
            return ToolResult.failure(f"Invalid arguments for {name}: {_brief(e)}")

        cache_key = (name, args.model_dump_json())
        if tool.idempotent and cache_key in self._cache:
            cached = self._cache[cache_key]
            return cached.model_copy(update={"meta": {**cached.meta, "cached": True}})

        result = self._run_with_timeout(tool, args)

        if tool.idempotent and result.ok:
            self._cache[cache_key] = result
        return result

    def _run_with_timeout(self, tool: Tool, args) -> ToolResult:
        started = time.perf_counter()
        future = self._pool.submit(tool.fn, args)
        try:
            result = future.result(timeout=tool.timeout_s)
        except FuturesTimeout:
            future.cancel()
            return ToolResult.down(
                f"{tool.name} timed out after {tool.timeout_s}s",
                latency_ms=int(tool.timeout_s * 1000),
            )
        except Exception as e:
            # The catch-all that makes the no-tool-may-raise guarantee true
            # even for a tool that forgot to handle something.
            return ToolResult.failure(
                f"{tool.name} raised {type(e).__name__}: {e}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                f"{tool.name} returned {type(result).__name__}, expected ToolResult"
            )

        result.meta.setdefault("latency_ms", int((time.perf_counter() - started) * 1000))
        return result


def _brief(e: ValidationError) -> str:
    """Pydantic's full error dump is verbose and would bloat every prompt.
    One short line per problem is what the model needs to correct itself."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}" for err in e.errors()[:4]
    )
