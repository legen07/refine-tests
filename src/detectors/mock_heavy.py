"""§5.7 — Mock-heavy / mock-only: ≥80% of exercised calls are mocks.

Signal (calibration row): `only_mocks_exercised` (+0.3), when literally every
exercised call resolves to a mock — mock construction and `patch(...)` setup
are excluded from the exercised-call denominator, so a test whose every
*behavioral* call is a mock is mock-only even when its assertions read
through local variables.
"""

from __future__ import annotations

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector

MOCK_FRACTION_THRESHOLD = 0.8


def detect(tests, ctx) -> list[Finding]:
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []
    for node in tests:
        calls = node.call_sites
        if not calls:
            continue
        mocks = [c for c in calls if c.on_mock]
        if not mocks:
            continue
        fraction = len(mocks) / len(calls)
        if fraction < MOCK_FRACTION_THRESHOLD:
            continue
        only = fraction >= 1.0
        names = sorted({c.base_name for c in mocks if c.base_name})
        signals = ["only_mocks_exercised"] if only else []
        base = _util.base_conf(ctx, "mock_heavy", 0.4)
        conf = _util.calibrate(base, signals, deltas)
        evidence = [
            f"{len(mocks)} of {len(calls)} exercised calls resolve to mocks "
            f"(bindings: {', '.join(names) or '—'}).",
        ]
        if only:
            evidence.append(
                "Every exercised call resolves to a mock — the test verifies "
                "the mocks, not the system.")
        else:
            evidence.append(
                "Some real I/O is exercised; this is mock-heavy rather than "
                "mock-only.")
        out.append(Finding(
            test_id=node.id,
            category="mock-heavy",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="mock_heavy",
            recommended_action="strengthen",
            signals=signals,
            evidence=evidence,
        ))
    return out


__detector__: CategoryDetector = detect
