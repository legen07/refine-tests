"""§5.10 — Brittle / change-detector: asserts on snapshots, serialized internal
state, exact log strings, or call order of collaborators where order is
unspecified.

Snapshot tests are counted once, but their brittleness is weighed against
snapshot-file churn from CI history (§5 note): a test that must be re-baselined
in a large fraction of runs is a change detector, not a regression test.
"""

from __future__ import annotations

from ..models import CiHistory, Finding, Tier
from . import _util
from .base import CategoryDetector

CHURN_CONFIDENCE_PER_RUN = 0.05
CHURN_MAX_BONUS = 0.25


def detect(tests, ctx) -> list[Finding]:
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    ci: CiHistory | None = ctx.get("ci")
    out: list[Finding] = []
    for node in tests:
        reasons: list[str] = []
        snapshots = [a for a in node.assertions if a.snapshot_like]
        if snapshots:
            reasons.append(
                f"Asserts serialized internal state / large inline literal "
                f"({len(snapshots)} snapshot-like assertion(s); counted once "
                f"per test — §5 note).")
        order = [a for a in node.assertions if a.pins_call_order]
        if order:
            reasons.append(
                "Pins the call order of collaborators where order is "
                "unspecified (`mock_calls` / `call_args_list` ordering).")
        logs = [a for a in node.assertions
                if any(ref.endswith(("caplog.text", "caplog.records",
                                    "record.message")) or
                       ref.startswith("caplog") for ref in a.referenced)]
        if logs:
            reasons.append(
                "Asserts an exact log string (caplog text equality); log "
                "formatting is presentation, not contract.")
        if not reasons:
            continue
        churn = 0
        if ci is not None and ci.runs:
            churn = ci.churn_for(node.file)
            if churn:
                reasons.append(
                    f"Churn signal: `{node.file}` changed in {churn} of "
                    f"{ci.run_count} CI runs.")
        base = _util.base_conf(ctx, "brittle", 0.65)
        bonus = min(CHURN_MAX_BONUS, CHURN_CONFIDENCE_PER_RUN * churn)
        conf = _util.calibrate(base, [], deltas)
        conf = round(min(1.0, conf + (bonus if churn else 0.0)), 3)
        out.append(Finding(
            test_id=node.id,
            category="brittle",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="brittle",
            recommended_action="refine",
            signals=[],
            evidence=reasons,
        ))
    return out


__detector__: CategoryDetector = detect
