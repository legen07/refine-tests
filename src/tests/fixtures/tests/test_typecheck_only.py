"""Typecheck-only tests (§5.8): every assertion is an isinstance guard the
return annotation of the production function already promises."""

from prod import calc


def test_typecheck_only_add():
    result = calc.add(1, 2)
    assert isinstance(result, int)


def test_typecheck_annotation():
    total: int = calc.add(3, 4)
    assert isinstance(total, int)


def test_typecheck_divide_returns():
    outcome = calc.divide(10, 2)
    assert isinstance(outcome, float)
