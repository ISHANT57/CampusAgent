"""M14 security tests.

The calculator's safety claim is that its grammar is CLOSED — dangerous
constructs are not blocked, they cannot be expressed. These tests are the
evidence for that claim, and they are the reason M14 was not simplified for
the MVP: security is not an MVP trade-off.
"""

import pytest

from app.tools.calculator import CalculatorArgs, UnsafeExpression, calculator, evaluate


# --- it actually computes ---------------------------------------------------

@pytest.mark.parametrize(
    "expr,expected",
    [
        ("6.5 - 6.2", pytest.approx(0.3)),
        ("7.4 >= 6.5", True),
        ("6.2 >= 6.5", False),          # the real Sitare scholarship case
        ("85 - 84", 1),
        ("(2 + 3) * 4", 20),
        ("100 / 8", 12.5),
        ("17 % 5", 2),
        ("2 ** 10", 1024),
        ("round(7.456, 2)", pytest.approx(7.46)),
        ("max(6.2, 6.5)", 6.5),
        ("abs(84 - 85)", 1),
        ("sqrt(16)", 4.0),
        ("6.2 >= 6.5 or 6.2 >= 6.0", True),
    ],
)
def test_evaluates_correctly(expr, expected):
    assert evaluate(expr) == expected


# --- the escapes that matter ------------------------------------------------

@pytest.mark.parametrize(
    "expr,why",
    [
        ("__import__('os').system('ls')", "import"),
        ("().__class__", "the classic sandbox escape's first hop"),
        ("().__class__.__bases__[0].__subclasses__()", "the full escape chain"),
        ("(1).__class__.__mro__", "attribute walk from a literal"),
        ("open('/etc/passwd')", "filesystem"),
        ("exec('x=1')", "nested exec"),
        ("eval('1+1')", "nested eval"),
        ("[x for x in range(10)]", "comprehension"),
        ("lambda: 1", "lambda"),
        ("x + 1", "bare name / variable"),
        ("globals()", "namespace access"),
        ("'abc' * 3", "string literal"),
        ("[1,2,3]", "list literal"),
        ("{'a': 1}", "dict literal"),
        ("print(1)", "non-allow-listed function"),
        ("round(1.5, ndigits=2)", "keyword arguments"),
    ],
)
def test_rejects_everything_outside_the_grammar(expr, why):
    with pytest.raises((UnsafeExpression, ValueError)):
        evaluate(expr)


def test_rejects_resource_exhaustion_via_exponent():
    # 2 ** 10_000_000 is a one-line memory exhaustion. The AST cannot infer
    # intent, so the exponent is bounded structurally.
    with pytest.raises(UnsafeExpression):
        evaluate("2 ** 10000000")


def test_rejects_non_literal_exponent():
    with pytest.raises(UnsafeExpression):
        evaluate("2 ** (5 * 100)")


def test_rejects_overlong_expression():
    with pytest.raises(UnsafeExpression):
        evaluate("1 + " * 100 + "1")


# --- the tool wrapper never raises ------------------------------------------

def test_tool_returns_failure_not_exception_on_attack():
    r = calculator(CalculatorArgs(expression="().__class__.__bases__"))
    assert r.ok is False and "rejected" in r.error.lower()


def test_tool_handles_division_by_zero():
    r = calculator(CalculatorArgs(expression="1 / 0"))
    assert r.ok is False and "zero" in r.error.lower()


def test_tool_success_shape():
    r = calculator(CalculatorArgs(expression="7.4 - 6.5"))
    assert r.ok is True
    assert r.data == pytest.approx(0.9)
    assert r.meta["expression"] == "7.4 - 6.5"


def test_args_model_caps_length_before_evaluation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CalculatorArgs(expression="1" * 500)
