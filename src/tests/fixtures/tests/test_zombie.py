"""Zombie tests (§5.4): reference production symbols that no longer exist."""

from prod import pricing
from prod.warehouse import lookup  # re-export shim, see prod/warehouse.py


def test_zombie_removed_helper():
    # Production helper was deleted; the import errors at run time, so this
    # test fails (wrongly) in every CI run rather than guarding anything.
    from prod.calc import old_helper

    assert old_helper is not None


def test_zombie_getattr_ghost():
    assert getattr(pricing, "legacy_rate", None) is None


def test_zombie_missing_class():
    from prod.warehouse import LegacyWarehouse

    assert LegacyWarehouse is not None


def test_zombie_with_impl_detail():
    # Two replace-primary signals (§7 gating for Replace): a ghost symbol
    # plus an internal constant.
    assert getattr(pricing, "legacy_rate", None) is None
    assert pricing._MAX_ITEMS == 100


def test_warehouse_lookup_still_works():
    # True-negative anchor: an import of a symbol that DOES exist.
    assert lookup("widget") == {"item": "widget", "qty": 7}
