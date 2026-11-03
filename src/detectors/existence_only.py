"""§5.9 — Existence-only: only asserts `is not None`, `to exist`, `to be
truthy`, or framework equivalents.

Such tests cannot fail for a behavioral reason: they pass whenever the code
under test returns *anything*. They are Strengthen candidates (§6): the
covered production call has an observable output worth asserting on.
"""

from __future__ import annotations

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector


def detect(tests, ctx) -> list[Finding]:
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []
    for node in tests:
        if not node.assertions:
            continue
        existence = [a for a in node.assertions if a.is_existence]
        if not existence or len(existence) != len(node.assertions):
            continue
        if any(a.is_constant for a in node.assertions):
            # Constant truthiness is handled by the tautology detector.
            continue
        shapes = sorted({a.detail for a in existence})[:3]
        base = _util.base_conf(ctx, "existence_only", 0.6)
        conf = _util.calibrate(base, [], deltas)
        out.append(Finding(
            test_id=node.id,
            category="existence-only",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="existence_only",
            recommended_action="strengthen",
            signals=[],
            evidence=[
                "Only existence/truthiness assertions present: "
                + "; ".join(f"`{s}`" for s in shapes) + ".",
                "Passes as long as the code under test returns any object; "
                "no observable behavior is verified.",
            ],
        ))
    return out


__detector__: CategoryDetector = detect
