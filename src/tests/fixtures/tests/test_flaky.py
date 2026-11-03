"""Flaky tests (§5.2): these lookORDINARY but the fixture CI history records
pass/fail alternation (see .ci-history/runs.json)."""

from prod import calc


def test_flaky_timeouts():
    assert calc.add(4, 4) == 8


def test_flaky_race():
    assert calc.multiply(2, 3) == 6


def test_flaky_retry():
    assert calc.subtract(5, 2) == 3
