"""§5.3 — Redundant / duplicate: AST clustering + coverage fingerprint overlap.

A test B is redundant with respect to test A when *all* hold:

* AST similarity ≥ 0.9 (normalized structure digests, identifiers stripped),
* coverage fingerprint Jaccard ≥ 0.9,
* B's assertion skeleton is a subsequence of A's — same assertions modulo
  rename (literals kept, variables abstracted).

Per §2, when the per-test coverage mechanism is unavailable this detector
refuses findings above LOW confidence and never guesses coverage overlap from
static analysis.
"""

from __future__ import annotations

import difflib

from ..models import Finding, Tier
from . import _util
from .base import CategoryDetector

AST_SIM_THRESHOLD = 0.9
COV_JACCARD_THRESHOLD = 0.9


def _skeleton_subseq(sub: tuple[str, ...], full: tuple[str, ...]) -> bool:
    """True iff every assertion of `sub` appears in `full` (modulo renames)."""
    it = iter(full)
    return all(any(s == f for f in it) for s in sub)


def detect(tests, ctx) -> list[Finding]:
    coverage: dict = ctx.get("coverage", {}) or {}
    coverage_available: bool = bool(ctx.get("coverage_available", False))
    deltas = ctx["calibration"]
    low_cap = 0.3 if not coverage_available else None
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})

    skeletons = {node.id: node.assertion_skeleton for node in tests}

    out: list[Finding] = []
    for b in tests:
        if not b.assertions:
            continue
        if not skeletons[b.id]:
            continue
        cov_b = coverage.get(b.coverage_key or b.id) or b.coverage
        if coverage_available and (cov_b is None or not cov_b.lines):
            cov_b = None
        best: tuple[float, str, str] | None = None
        for a in tests:
            if a.id == b.id or not a.assertions:
                continue
            if not skeletons[a.id]:
                continue
            sim = difflib.SequenceMatcher(
                None, a.structure_digest, b.structure_digest).ratio()
            if sim < AST_SIM_THRESHOLD:
                continue
            if not _skeleton_subseq(skeletons[b.id], skeletons[a.id]):
                continue
            if coverage_available:
                cov_a = coverage.get(a.coverage_key or a.id) or a.coverage
                if cov_a is None or not cov_a.lines or cov_b is None:
                    continue
                jac = cov_b.jaccard(cov_a)
                if jac < COV_JACCARD_THRESHOLD:
                    continue
                overlap = f"; coverage Jaccard {jac:.2f}"
            else:
                overlap = ""
            if best is None or sim > best[0]:
                best = (sim, a.id, overlap)
        if best is None:
            continue
        sim, other, overlap = best
        base = _util.base_conf(ctx, "redundant", 0.45)
        conf = _util.calibrate(base, ["ast_similarity_ge_0_9"], deltas,
                               low_cap=low_cap)
        out.append(Finding(
            test_id=b.id,
            category="redundant",
            confidence=conf,
            tier=tier_of.get(b.id, b.tier),
            detector="redundant",
            recommended_action="remove",
            signals=["ast_similarity_ge_0_9"] if coverage_available else [],
            evidence=[
                f"Near-duplicate of {other}: AST similarity {sim:.2f}"
                f"{overlap}; assertions are a subset modulo rename.",
            ],
        ))
    return out


__detector__: CategoryDetector = detect
