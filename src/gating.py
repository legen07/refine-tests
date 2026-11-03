"""Confidence & gating (§7): calibration application, action gates, vetoes.

Rank order when multiple actions apply (§6):
    Replace > Strengthen > Refine > Remove > Keep
(i.e. prefer preserving coverage of the underlying behavior.)

Vetoes are hard: if any veto applies, the recommendation is downgraded to
"Keep (flagged)" and MUST appear in the report with an explanation (§10.6).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .detectors._util import calibrate
from .models import (
    Decision,
    Finding,
    MutationResult,
    TestNode,
    Tier,
    Veto,
)

REMOVE_ACTIONS: set[str] = {"remove", "replace"}


def apply_mutation_signals(findings: list[Finding],
                           mutation: MutationResult | None,
                           calibration: dict[str, float]) -> list[Finding]:
    """Fold a mutation result (§5, §7) into finding confidence.

    Mutation score 0 on sampled mutants is the +0.4 calibration signal for the
    "cannot fail" claim; an over-budget probe degrades to static heuristics
    (§2), i.e. leaves confidence untouched.
    """
    if mutation is None or mutation.over_budget or not mutation.mutants:
        return findings
    if mutation.score > 0:
        return findings
    out = []
    for f in findings:
        signals = f.signals if "mutation_score_zero" in f.signals else \
            f.signals + ["mutation_score_zero"]
        out.append(replace(
            f,
            signals=signals,
            confidence=calibrate(f.confidence, signals, calibration)))
    return out


def _gate_remove(findings: list[Finding], cosignal_min: float,
                 remove_requires: int, remove_single: float,
                 mutation: MutationResult | None) -> tuple[bool, list[str]]:
    support = [f for f in findings
               if f.recommended_action == "remove" and f.confidence >= cosignal_min]
    evidence = [
        f"{f.category}@{f.confidence:.2f} ({f.detector})" for f in
        sorted(support, key=lambda f: (-f.confidence, f.category))]
    if len(support) >= remove_requires:
        return True, [f"≥{remove_requires} removal detectors agree: " +
                      ", ".join(evidence)]
    if len(support) == 1:
        single = support[0]
        if single.confidence >= remove_single and mutation is not None and \
                mutation.mutants and mutation.score == 0:
            return True, [
                f"single removal detector {single.category}@"
                f"{single.confidence:.2f} plus mutation verification "
                f"(score 0 on {len(mutation.mutants)} sampled mutants)"]
    return False, []


def decide(node: TestNode,
           findings: list[Finding],
           ctx: dict[str, Any],
           mutation: MutationResult | None = None) -> Decision:
    """Aggregate findings into one gated, veto-aware Decision (§7)."""
    mutation = mutation or ctx.get("mutation", {}).get(node.id)
    calibration: dict[str, float] = ctx["calibration"]
    findings = apply_mutation_signals(findings, mutation, calibration)

    gating_cfg: dict[str, Any] = ctx.get("gating", {}) or {}
    features: dict[str, bool] = ctx.get("features", {}) or {}
    cosignal_min = float(gating_cfg.get("cosignal_min_confidence", 0.5))
    remove_requires = int(gating_cfg.get("remove_requires", 2))
    remove_single = float(gating_cfg.get("remove_single_conf", 0.9))
    refine_conf = float(gating_cfg.get("refine_conf", 0.7))
    strengthen_conf = float(gating_cfg.get("strengthen_conf", 0.6))

    remove_ok, remove_reasons = _gate_remove(
        findings, cosignal_min, remove_requires, remove_single, mutation)
    refine_ok = any(f.recommended_action == "refine" and
                    f.confidence >= refine_conf for f in findings)
    strengthen_ok = any(f.recommended_action == "strengthen" and
                        f.confidence >= strengthen_conf for f in findings)
    replace_support = [f for f in findings
                       if f.recommended_action == "replace" and
                       f.confidence >= cosignal_min]
    replace_ok = (features.get("replace", False) and
                  len(replace_support) >= 1 and
                  len([f for f in findings if f.confidence >= cosignal_min]) >= 2 and
                  node.tier in {"core", "extended"})

    action = "keep"
    reasons: list[str] = []
    if features.get("replace", False) and replace_ok:
        action, reasons = "replace", replace_reasons(findings)
    elif features.get("strengthen", False) and strengthen_ok:
        action = "strengthen"
        reasons = [f"{f.category}@{f.confidence:.2f}: {f.evidence[0]}"
                   for f in findings
                   if f.recommended_action == "strengthen" and
                   f.confidence >= strengthen_conf and f.evidence][:1]
    elif features.get("refine", False) and refine_ok:
        action = "refine"
        reasons = [f"{f.category}@{f.confidence:.2f}: {f.evidence[0]}"
                   for f in findings
                   if f.recommended_action == "refine" and
                   f.confidence >= refine_conf and f.evidence][:1]
    elif remove_ok:
        action = "remove"
        reasons = remove_reasons

    # -- vetoes (§7): hard, unconditional downgrades to Keep (flagged) -------
    tier: Tier = node.tier
    vetoes: list[Veto] = []
    if action in REMOVE_ACTIONS:
        if (ctx.get("vetoes", {}).get("sole_coverage", True) and
                ctx.get("sole_covered_symbols", {}).get(node.id)):
            covered = sorted(ctx["sole_covered_symbols"][node.id])
            vetoes.append(Veto(
                signal="sole_coverage_of_public_symbol",
                reason=(
                    f"Test is the sole coverage of public symbol(s) "
                    f"{', '.join(covered)}; removing it would leave that "
                    f"symbol unguarded (−0.5 veto).")))
        if ctx.get("vetoes", {}).get("smoke_tier", True) and tier == "smoke":
            vetoes.append(Veto(
                signal="tier_is_smoke",
                reason=("Test is in tier 'smoke'; smoke tests are the "
                        "run-the-system floor and are never auto-removed "
                        "(−0.3 veto, §13: a second human sign-off would be "
                        "required).")))
    if vetoes and action != "keep":
        action = "keep"

    confidence = max((f.confidence for f in findings), default=0.0)
    co_signals = len({f.category for f in findings
                      if f.confidence >= cosignal_min})
    evidence_line = "; ".join(
        sorted({f"{f.category} ({f.confidence:.2f}): {f.evidence[0]}"
                for f in findings if f.evidence})) or "no detector fired"
    if reasons and action in REMOVE_ACTIONS:
        evidence_line = " | ".join(reasons) + " | " + evidence_line

    return Decision(
        test_id=node.id,
        file=node.file,
        tier=tier,
        action=action,
        findings=findings,
        confidence=round(confidence, 3),
        co_signals=co_signals,
        vetoes=vetoes,
        evidence_line=evidence_line,
        mutation=mutation,
    )


def replace_reasons(findings: list[Finding]) -> list[str]:
    return [f"{f.category}@{f.confidence:.2f}: {f.evidence[0]}"
            for f in sorted(findings, key=lambda f: (-f.confidence, f.category))
            if f.evidence][:3]
