"""Implementation-detail tests (§5.11): private methods, module-level
constants used only internally, object identity of internals."""

from prod import calc, notifier, pricing


def test_impl_private_method():
    assert calc._validate_operands(1, 2) is True


def test_impl_module_constant():
    assert pricing._MAX_ITEMS == 100


def test_impl_object_identity():
    n = notifier.Notifier()
    n.notify("ops", "deploy done")
    assert n.__dict__["_sent"] == ["to=ops;body=deploy done"]
