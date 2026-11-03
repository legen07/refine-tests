"""§5.2 — Flaky: from CI history; pass/fail alternation.

A test is flaky when its CI results show *interleaved* failures: single-run
failures sandwiched between passes. A genuine regression, by contrast, fails a
contiguous block of runs (the bug is present until it is fixed), so blocks of
length ≥2 do not count as flakiness. Confidence grows with the number of
isolated failures, the strongest evidence of nondeterminism.
"""

from __future__ import annotations

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector


def _isolated_failures(results: list[str]) -> int:
    """Count single-run failures with passes on both sides (P F P)."""
    n = 0
    for i, r in enumerate(results):
        if r != "failed":
            continue
        before = results[i - 1] if i > 0 else "passed"
        after = results[i + 1] if i + 1 < len(results) else "passed"
        if before == "passed" and after == "passed":
            n += 1
    return n


def _compact(results: list[str]) -> str:
    return "".join("P" if r == "passed" else "F" for r in results)


def detect(tests, ctx) -> list[Finding]:
    ci = ctx.get("ci")
    if ci is None or not ci.runs:
        return []
    out: list[Finding] = []
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    for node in tests:
        results = ci.results_for(node.coverage_key or node.id)
        if len(results) < 4:
            continue
        states = set(results)
        if "failed" not in states or "passed" not in states:
            continue
        isolated = _isolated_failures(results)
        if isolated < 2:
            continue
        base = _util.base_conf(ctx, "flaky", 0.35)
        conf = min(1.0, base + 0.12 * min(isolated, 4))
        out.append(Finding(
            test_id=node.id,
            category="flaky",
            confidence=round(conf, 3),
            tier=tier_of.get(node.id, node.tier),
            detector="flaky",
            recommended_action="refine",
            signals=[],
            evidence=[
                f"CI history: interleaved pass/fail "
                f"{_compact(results)} ({len(results)} runs, {isolated} "
                f"isolated failures, provider={ci.provider}).",
                "Genuine regressions fail a contiguous block of runs; these "
                "failures alternate, which is the signature of "
                "nondeterminism.",
            ],
        ))
    return out


__detector__: CategoryDetector = detect
