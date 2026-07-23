"""Tool registry — one decorator, no discovery machinery.

Registration happens at import. There is no plugin loader, no YAML, no
directory scan: six tools do not need a framework, and a decorator plus an
import in __init__.py is the whole mechanism.
"""

from __future__ import annotations

import inspect
import re
from typing import Callable, get_type_hints

from pydantic import BaseModel

from app.llm.base import ToolSpec
from app.tools.base import Tool, ToolResult

# M0/F7, enforced in code rather than left in a document.
#
# Every one of the 15 wrong tool choices across 180 calls traced to one word:
# knowledge_list_documents' description opened "Use this FIRST when...", and
# models read "FIRST" as "before anything else", so it won unrelated goals.
#
# A description should state the TRIGGER CONDITION ("when you need an inventory
# of what exists"), never a position in an imagined ordering. The model decides
# the ordering; the description only says what the tool is for.
_ORDERING_WORDS = re.compile(r"\b(FIRST|BEFORE ANYTHING|ALWAYS USE THIS)\b")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        *,
        description: str,
        timeout_s: float = 30.0,
        idempotent: bool = True,
        terminal: bool = False,
        name: str | None = None,
    ) -> Callable:
        """Register a function as a tool.

        The tool name defaults to the function name and the args model is read
        from the single parameter's type annotation — so the schema the model
        sees and the validation the executor runs come from one declaration and
        cannot drift.
        """

        def decorator(fn: Callable[[BaseModel], ToolResult]) -> Callable:
            tool_name = name or fn.__name__

            params = list(inspect.signature(fn).parameters.values())
            if len(params) != 1:
                raise TypeError(
                    f"{tool_name}: a tool takes exactly one argument (its args model), got {len(params)}"
                )

            # get_type_hints, not param.annotation. Tool modules use
            # `from __future__ import annotations`, under which every
            # annotation is a STRING at runtime — so param.annotation gives
            # "CalculatorArgs", not the class. get_type_hints resolves it
            # against the defining module's namespace.
            try:
                hints = get_type_hints(fn)
            except Exception as e:
                raise TypeError(f"{tool_name}: could not resolve type hints: {e}") from e

            args_model = hints.get(params[0].name)
            if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
                raise TypeError(
                    f"{tool_name}: argument must be annotated with a Pydantic model, "
                    f"got {args_model!r}"
                )

            if match := _ORDERING_WORDS.search(description):
                raise ValueError(
                    f"{tool_name}: description contains ordering language {match.group(0)!r}. "
                    "M0 measured this causing the tool to be selected for unrelated goals. "
                    "State the trigger condition instead ('when you need X'), not a position."
                )
            if tool_name in self._tools:
                raise ValueError(f"duplicate tool name: {tool_name}")

            self._tools[tool_name] = Tool(
                name=tool_name,
                description=description,
                args_model=args_model,
                fn=fn,
                timeout_s=timeout_s,
                idempotent=idempotent,
                terminal=terminal,
            )
            return fn

        return decorator

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        """What the LLM sees. The only bridge between the tool layer and the
        provider layer, and it goes one way."""
        return [
            ToolSpec(name=t.name, description=t.description, parameters=t.json_schema())
            for t in self._tools.values()
        ]


registry = ToolRegistry()
