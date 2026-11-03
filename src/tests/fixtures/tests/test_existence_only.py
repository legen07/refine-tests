"""Existence-only tests (§5.9): only `is not None` / truthiness."""

from prod import calc, reporting


def test_existence_calc_add():
    assert calc.add is not None


def test_existence_report():
    assert reporting.build_report([]) is not None


def test_existence_truthy_report():
    assert reporting.build_report([{"amount": 5}])
