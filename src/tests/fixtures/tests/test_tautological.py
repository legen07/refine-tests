"""Tautological tests (§5.6): constants, self-assigned values, configured
mock returns."""

from unittest.mock import Mock


def test_taut_true():
    assert True


def test_taut_self_assign():
    x = 5
    assert x == 5


def test_taut_constant_expr():
    assert 2 + 2 == 4


def test_taut_mock_configured():
    m = Mock()
    m.return_value = 7
    assert m() == 7
