"""Organic, behavior-focused tests (the north-star, §16): true-negative
anchors for every detector. The fixture CI history records one or two real
failures for some of these — historical regressions these tests caught."""

import pytest

from prod import calc, pricing


def test_add_two_numbers():
    assert calc.add(2, 3) == 5


def test_add_negatives():
    assert calc.add(-2, -3) == -5


def test_subtract_works():
    assert calc.subtract(10, 4) == 6


def test_divide_by_zero_raises():
    with pytest.raises(ValueError):
        calc.divide(1, 0)


def test_price_order_discount():
    assert pricing.price_order([100, 200]) == 270.0


def test_price_order_no_discount():
    assert pricing.price_order([100], discount=0.0) == 100.0


@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (2, 3, 5),
    (10, -4, 6),
    (0, 0, 0),
    (-1, -1, -2),
])
def test_add_parametrized(a, b, expected):
    assert calc.add(a, b) == expected
