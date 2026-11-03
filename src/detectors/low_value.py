"""§5.5 — Low-value: coverage fingerprint ⊆ another test's, no unique
assertion, no unique branch.

Signal (calibration row): `coverage_subset_of_other` (+0.3).
This is the strictest coverage-driven detector: coverage alone is never a
verdict (§16) — the test must also add no unique assertion and no unique
branch.
"""

from __future__ import annotations

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector


def _skeleton_subseq(sub: tuple[str, ...], full: tuple[str, ...]) -> bool:
    it = iter(full)
    return all(any(s == f for f in it) for s in sub)


def detect(tests, ctx) -> list[Finding]:
    coverage: dict = ctx.get("coverage", {}) or {}
    coverage_available: bool = bool(ctx.get("coverage_available", False))
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []

    for node in tests:
        if not coverage_available:
            continue  # §2: never guess coverage overlap from static analysis
        cov = coverage.get(node.coverage_key or node.id) or node.coverage
        if cov is None or not cov.lines:
            continue
        subsumer: str | None = None
        for other in tests:
            if other.id == node.id:
                continue
            cov_o = coverage.get(other.coverage_key or other.id) or other.coverage
            if cov_o is None or not cov_o.lines:
                continue
            if not cov.is_subset_of(cov_o):
                continue
            # Unique assertion? skeleton must be covered by the other test.
            if node.assertion_skeleton and not _skeleton_subseq(
                    node.assertion_skeleton, other.assertion_skeleton):
                continue
            # Unique branch?
            if cov.branches - cov_o.branches:
                continue
            subsumer = other.id
            break
        if subsumer is None:
            continue
        base = _util.base_conf(ctx, "low_value", 0.45)
        conf = _util.calibrate(base, ["coverage_subset_of_other"], deltas)
        out.append(Finding(
            test_id=node.id,
            category="low-value",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="low_value",
            recommended_action="remove",
            signals=["coverage_subset_of_other"],
            evidence=[
                f"Coverage fingerprint ⊆ {subsumer} "
                f"({len(cov.lines)} lines covered, 0 unique; 0 unique branches; "
                f"no unique assertion).",
            ],
        ))
    return out


__detector__: CategoryDetector = detect
