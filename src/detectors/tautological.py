"""§5.6 — Tautological: asserts a constant, asserts a mock, asserts the value
it just set, `assert True`, etc.

Signals (calibration rows): `assertion_is_constant` (+0.5),
`only_mocks_exercised` (+0.3, for asserting a value the test configured on a
mock).

A test with *some* tautological assertions but at least one genuine one still
has value: it is reported at LOW confidence only.
"""

from __future__ import annotations

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector


def _matches_mock_config(node, assertion) -> bool:
    """`m.return_value = 7; assert m() == 7` — the test asserts what it set."""
    if not assertion.on_mock:
        return False
    comparators = [c.strip() for c in assertion.comparator.split(",")]
    for base, literal in node.mock_configs:
        for c in comparators:
            stripped = c.removeprefix("==").removeprefix("is").strip()
            if stripped == literal:
                return True
    return False


def detect(tests, ctx) -> list[Finding]:
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []
    for node in tests:
        if not node.assertions:
            continue
        kinds: list[str] = []
        partial = False
        for a in node.assertions:
            # `assert m.assert_called_once()` is a behavioral claim about a
            # collaboration, not a value claim: not tautological (the brittle
            # and mock-heavy detectors own it).
            if a.pins_call_order and a.on_mock:
                continue
            if a.is_constant:
                kinds.append(f"asserts a compile-time constant (`{a.detail}`)")
            elif a.self_assigned:
                kinds.append("asserts the value the test itself just set "
                             f"(`{a.detail}`)")
            elif _matches_mock_config(node, a):
                kinds.append("asserts a value the test configured on a mock "
                             f"(`{a.detail}`)")
            elif a.on_mock:
                kinds.append(f"asserts a mock interaction (`{a.detail}`)")
            else:
                partial = True
        if not kinds:
            continue
        signals: list[str] = []
        if any("compile-time constant" in k or "just set" in k for k in kinds):
            signals.append("assertion_is_constant")
        if any("configured on a mock" in k for k in kinds):
            signals.append("only_mocks_exercised")
        if partial:
            conf = 0.3                       # genuine assertions remain: LOW only
        else:
            base = _util.base_conf(ctx, "tautological", 0.35)
            conf = _util.calibrate(base, signals, deltas)
        out.append(Finding(
            test_id=node.id,
            category="tautological",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="tautological",
            recommended_action="strengthen",
            signals=signals,
            evidence=sorted(set(kinds)),
        ))
    return out


__detector__: CategoryDetector = detect
