"""Never-failing tests (§5.1): trivially-passing guards; the fixture CI
history records 0 failures over 20+ runs (see .ci-history/runs.json)."""

from prod import warehouse


def test_neverfailing_lookup_smoke():
    assert warehouse.lookup("widget") is not None


def test_neverfailing_pricing_picks():
    picked = warehouse.lookup("gadget")
    assert picked is not None
