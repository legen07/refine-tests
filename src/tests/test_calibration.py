"""Unit tests for the calibration helpers (§7 table, §2 LOW cap)."""

from src.detectors import _util


def test_calibrate_sums_signals_and_clamps():
    deltas = {"a": 0.4, "b": 0.3}
    assert _util.calibrate(0.2, ["a"], deltas) == 0.6
    # Clamped at 1.0.
    assert _util.calibrate(0.9, ["a", "b"], deltas) == 1.0
    # Never below 0.
    assert _util.calibrate(0.0, [], deltas) == 0.0


def test_calibrate_unknown_signal_contributes_nothing():
    deltas = {"a": 0.5}
    assert _util.calibrate(0.1, ["nope"], deltas) == 0.1


def test_calibrate_low_cap_implements_the_unavailable_mechanism():
    # §2: without per-test coverage, a detector must refuse > LOW confidence.
    deltas = {"a": 0.9}
    assert _util.calibrate(0.45, ["a"], deltas, low_cap=0.3) == 0.3


def test_base_conf_honors_config_override():
    ctx = {"detector_confidence": {"zombie": 0.9}}
    assert _util.base_conf(ctx, "zombie", 0.4) == 0.9
    assert _util.base_conf({}, "zombie", 0.4) == 0.4
