"""§5.1 — Never-failing: from CI history; 0 failures over N runs.

Signal (calibration row): `ci_never_failing_20plus_runs` (+0.4).

Note on the §7 calibration wording ("0 passes over ≥20 runs"): the detector is
*named* never-failing, i.e. 0 **failures**; a test with 0 passes would be an
always-failing test, which is a different (and much rarer) smell not listed in
§5. We implement 0-failures and record the reading in ASSUMPTIONS.md (A2).
"""

from __future__ import annotations

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector


def detect(tests, ctx) -> list[Finding]:
    ci = ctx.get("ci")
    if ci is None or not ci.runs:
        # §2: no CI history → this detector reports nothing (the report header
        # states the degradation instead).
        return []
    deltas = ctx["calibration"]
    min_runs = int(ctx.get("ci_min_runs", 20))
    out: list[Finding] = []
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    for node in tests:
        results = ci.results_for(node.coverage_key or node.id)
        if len(results) < min_runs:
            continue
        failures = sum(1 for r in results if r != "passed")
        if failures > 0:
            continue
        base = _util.base_conf(ctx, "never_failing", 0.3)
        conf = _util.calibrate(base, ["ci_never_failing_20plus_runs"], deltas)
        out.append(Finding(
            test_id=node.id,
            category="never-failing",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="never_failing",
            recommended_action="remove",
            signals=["ci_never_failing_20plus_runs"],
            evidence=[
                f"CI history: {len(results)} runs, 0 failures "
                f"(provider={ci.provider}).",
                "Never-failing tests are candidates for the 'cannot fail' "
                "claim; they are a removal signal only with co-signals (§7).",
            ],
        ))
    return out


__detector__: CategoryDetector = detect
