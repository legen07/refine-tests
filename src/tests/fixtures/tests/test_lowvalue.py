"""Low-value (§5.5): `test_price_smoke_only` is a strict subset of
`test_price_full` — same first assertion, strictly fewer covered lines, no
unique branch and no unique assertion."""

from prod import pricing


def test_price_smoke_only():
    assert pricing.price_order([50]) == 45.0


def test_price_full():
    price = pricing.price_order([50])
    assert price == 45.0
    assert pricing.apply_coupon(price, "SAVE10") == 40.5
