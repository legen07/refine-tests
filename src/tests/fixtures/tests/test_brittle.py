"""Brittle / change-detector tests (§5.10): snapshots, exact log strings,
collaborator call order where order is unspecified.

Note: this whole FILE is given churn in the fixture CI history (it is
re-baselined in several runs), which is the §5 signal that weights the
brittleness upward."""

from unittest.mock import Mock, call

from prod import notifier, reporting


def test_brittle_snapshot():
    result = reporting.build_report([{"amount": 10}, {"amount": 20}])
    assert result == {
        "total": 30,
        "row_count": 2,
        "currency": "USD",
        "rounding": 2,
        "note": "",
    }


def test_brittle_log_string(caplog):
    import logging

    caplog.set_level(logging.INFO)
    reporting.emit_log("order-7")
    assert "event=order-7" in caplog.text


def test_brittle_call_order():
    m = Mock()
    m.send("a")
    m.send("b")
    assert m.mock_calls == [call.send("a"), call.send("b")]
