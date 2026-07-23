"""Arithmetic evaluator. NOT a Python sandbox, and deliberately not named like one.

What the use case actually needs is arithmetic over numbers the agent has
already retrieved: "is 6.2 >= 6.5", "84 - 85". That is a tiny, closed language,
and a closed language can be made genuinely safe by allow-listing the AST.

Why not the obvious alternatives:

  eval(expr, {"__builtins__": {}})
      Still escapable. With no builtins at all you can reach the interpreter
      through any literal's type:
          ().__class__.__bases__[0].__subclasses__()
      walks to every loaded class, including subprocess.Popen. Emptying
      __builtins__ removes the front door and leaves the window open.

  RestrictedPython
      Better, still escapable, and worse in one specific way: it FEELS safe.
      A half-secure sandbox invites you to widen its inputs.

  Docker / E2B per execution
      The correct answer for general Python. Deferred until a task provably
      needs more than arithmetic — and if that day comes, this tool is not
      extended, a differently-named one is added.

The security here is that the language is closed, not that dangerous things are
blocked. Nothing outside the allow-list can be expressed at all.
"""

from __future__ import annotations

import ast
import math

from pydantic import BaseModel, Field

from app.tools.base import ToolResult
from app.tools.registry import registry

# Node types the grammar permits. Anything absent cannot be expressed:
# no Name (so no variables or attribute walks), no Attribute, no Subscript,
# no comprehension, no lambda, no import, no assignment, no f-string.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.And, ast.Or,
    ast.Call, ast.Load,
    # Name is permitted ONLY so a call can name its callee (`abs(x)` parses the
    # callee as a Name). Every Name is then checked against _ALLOWED_FUNCTIONS
    # below, so variables (`x + 1`) and namespace access (`globals()`) are
    # still rejected — the identifier simply is not in the allow-list.
    ast.Name,
)

# The only callables reachable. Each takes numbers and returns a number.
_ALLOWED_FUNCTIONS = {
    "abs": abs, "min": min, "max": max, "round": round, "sum": sum,
    "floor": math.floor, "ceil": math.ceil, "sqrt": math.sqrt,
}

MAX_EXPRESSION_LENGTH = 200
# 2 ** 10_000_000 is a one-line memory exhaustion. The AST cannot see intent,
# so the exponent is bounded structurally.
MAX_EXPONENT = 100


class CalculatorArgs(BaseModel):
    expression: str = Field(
        max_length=MAX_EXPRESSION_LENGTH,
        description="A single arithmetic or comparison expression, e.g. '7.4 - 6.5' or '7.4 >= 6.5'.",
    )


class UnsafeExpression(ValueError):
    """The expression is outside the permitted grammar."""


def _validate(node: ast.AST) -> None:
    """Walk the tree and reject anything not explicitly permitted.

    Allow-list, never deny-list. A deny-list is a bet that you enumerated every
    dangerous construct; an allow-list is a statement about what the language
    contains. Only one of those survives a construct you did not think of.
    """
    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES):
            raise UnsafeExpression(f"{type(child).__name__} is not allowed in an expression")

        # The rule that keeps Name safe: an identifier may only ever be one of
        # the allow-listed functions. Nothing else in this grammar has a name,
        # so `x`, `globals`, `__import__` and friends all fail here.
        if isinstance(child, ast.Name) and child.id not in _ALLOWED_FUNCTIONS:
            raise UnsafeExpression(
                f"unknown identifier {child.id!r}; expressions may only reference "
                f"the functions {', '.join(sorted(_ALLOWED_FUNCTIONS))}"
            )

        if isinstance(child, ast.Call):
            # func must be a bare name from the allow-list. This is what blocks
            # `().__class__...` — the callee is an Attribute, not a Name, and
            # ast.Attribute is not in _ALLOWED_NODES anyway.
            if not isinstance(child.func, ast.Name):
                raise UnsafeExpression("only direct calls to allowed functions are permitted")
            if child.func.id not in _ALLOWED_FUNCTIONS:
                raise UnsafeExpression(
                    f"unknown function {child.func.id!r}; allowed: {', '.join(sorted(_ALLOWED_FUNCTIONS))}"
                )
            if child.keywords:
                raise UnsafeExpression("keyword arguments are not permitted")

        if isinstance(child, ast.Constant) and not isinstance(child.value, (int, float, bool)):
            raise UnsafeExpression("only numeric literals are permitted")

        if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Pow):
            exponent = child.right
            if isinstance(exponent, ast.Constant) and isinstance(exponent.value, (int, float)):
                if abs(exponent.value) > MAX_EXPONENT:
                    raise UnsafeExpression(f"exponent above {MAX_EXPONENT} is not permitted")
            else:
                raise UnsafeExpression("exponent must be a numeric literal")


def evaluate(expression: str) -> float | int | bool:
    """Parse, validate, evaluate. Raises UnsafeExpression or ValueError."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise UnsafeExpression(f"expression exceeds {MAX_EXPRESSION_LENGTH} characters")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"could not parse expression: {e.msg}") from e

    _validate(tree)
    # Safe only because _validate already proved the tree contains nothing but
    # literals, operators, and allow-listed calls. The empty __builtins__ is
    # belt-and-braces, not the actual control.
    return eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, _ALLOWED_FUNCTIONS)


@registry.register(
    description=(
        "Evaluate one arithmetic or comparison expression and return the result. "
        "Use this whenever a number must be computed or compared: differences, "
        "percentages, thresholds, or eligibility checks. Accepts arithmetic and "
        "comparison operators and the functions abs, min, max, round, sum, floor, "
        "ceil, sqrt. Examples: '7.4 - 6.5', '7.4 >= 6.5'. It cannot look anything "
        "up, so you must already know the numbers."
    ),
    timeout_s=5.0,
)
def calculator(args: CalculatorArgs) -> ToolResult:
    try:
        value = evaluate(args.expression)
    except UnsafeExpression as e:
        return ToolResult.failure(f"Expression rejected: {e}")
    except ZeroDivisionError:
        return ToolResult.failure("Division by zero.")
    except (ValueError, OverflowError, TypeError) as e:
        return ToolResult.failure(f"Could not evaluate {args.expression!r}: {e}")
    return ToolResult.success(value, expression=args.expression)
