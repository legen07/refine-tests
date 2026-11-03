"""Redundant/duplicate pair 2 (§5.3): gamma and delta duplicate each other on
`pricing.price_order`, identical modulo renames and literals."""

from prod import pricing


def test_price_pair_gamma():
    price = pricing.price_order([100])
    assert price == 90.0


def test_price_pair_delta():
    total = pricing.price_order([100])
    assert total == 90.0
