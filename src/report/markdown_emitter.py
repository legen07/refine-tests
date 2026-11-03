"""Human-readable report (§10): the seven sections, in spec order.

Deterministic: sorted, no timestamps (§14.6).
"""

from __future__ import annotations

from pathlib import Path

from ..models import AuditResult, Decision

W = 100  # wrapping width for evidence cells is left to the renderer


def _fmt(n: float) -> str:
    return f"{n:.2f}"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _head(columns: list[str]) -> str:
    return "\n".join([
        _row(columns),
        _row(["---"] * len(columns)),
    ])


def emit_markdown(result: AuditResult, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(result), encoding="utf-8")
    return path


def _render(result: AuditResult) -> str:
    run = result.run
    parts: list[str] = []
    parts.append("# refine-tests report")
    parts.append("")

    # -------------------------------------------------------------- 1. header
    parts.append("## 1. Run header")
    parts.append("")
    parts.append(f"- Mode: **{run.get('mode', 'audit')}**")
    scope = run.get("scope", {})
    parts.append(f"- Scope: {scope.get('type', 'full')}"
                 + (f" `{scope.get('spec')}`" if scope.get("spec") else "")
                 + (f"; tiers: {', '.join(scope.get('tiers', []))}"
                    if scope.get("tiers") else ""))
    toolchain = run.get("toolchain", {})
    parts.append(
        f"- Toolchain: languages detected "
        f"{', '.join(toolchain.get('languages', []) or ['—'])}; "
        f"per-test coverage: "
        f"{'available (' + toolchain.get('coverage_note', '') + ')'
          if toolchain.get('coverage_available') else
          'UNAVAILABLE (' + toolchain.get('coverage_note', '') + ')'}")
    ci = run.get("ci", {})
    parts.append(f"- CI source: {ci.get('source', 'unavailable')} "
                 f"({ci.get('details', '')})")
    if run.get("mutation_run"):
        parts.append(f"- Mutation probe: enabled "
                     f"({run.get('mutation', {}).get('note', '')})")
    parts.append(f"- Assumptions: see [ASSUMPTIONS.md]"
                 f"({run.get('assumptions_ref', 'ASSUMPTIONS.md')})")
    parts.append("")

    # ------------------------------------------------- 2. executive summary
    summary = result.summary
    parts.append("## 2. Executive summary")
    parts.append("")
    parts.append(f"- Total tests analyzed: **{summary.get('total_tests', 0)}**")
    by_action = result.by_action()
    parts.append("- By action: " +
                 ", ".join(f"{k}={v}" for k, v in sorted(by_action.items()))
                 if by_action else "- By action: —")
    counts: dict[str, dict[str, int]] = {}
    for decision in result.decisions:
        tier = decision.tier
        for finding in decision.findings:
            counts.setdefault(tier, {})
            counts[tier][finding.category] = \
                counts[tier].get(finding.category, 0) + 1
    if counts:
        parts.append("")
        parts.append("Counts by tier × category (findings):")
        parts.append("")
        categories = sorted({c for row in counts.values() for c in row})
        parts.append(_head(["tier"] + categories))
        for tier in sorted(counts):
            parts.append(_row(
                [tier] + [str(counts[tier].get(c, 0)) for c in categories]))
        parts.append("")
    parts.append(f"- Quarantined (all states): {len(result.quarantine)}")
    parts.append(f"- Proposals staged: {len(result.proposals)}")
    parts.append("")

    # ----------------------------------------------------------- 3. before/after
    parts.append("## 3. Before / after")
    parts.append("")
    parts.append(_head([
        "test", "tier", "action", "confidence", "co-signals", "evidence"]))
    for decision in sorted(result.decisions, key=lambda d: (d.action,
                                                            d.test_id)):
        parts.append(_row([
            f"`{decision.test_id}`",
            decision.tier,
            f"**{decision.action}**",
            _fmt(decision.confidence) if decision.findings else "—",
            str(decision.co_signals) if decision.findings else "—",
            (decision.evidence_line.replace("|", "\\|")[:W] + "…")
            if len(decision.evidence_line) > W
            else decision.evidence_line.replace("|", "\\|") or "—",
        ]))
    parts.append("")

    # ----------------------------------------------------------- 4. quarantine
    parts.append("## 4. Quarantine log")
    parts.append("")
    removed = [d for d in result.decisions if d.action == "remove"]
    if removed:
        parts.append(_head(["test", "quarantine id", "evidence (one line)",
                            "restore command"]))
        for d in removed:
            qid = d.quarantine_id or "n/a"
            parts.append(_row([
                f"`{d.test_id}`",
                qid,
                (d.evidence_line.replace("|", "\\|")[:W] + "…")
                if len(d.evidence_line) > W
                else d.evidence_line.replace("|", "\\|"),
                f"`refine-tests restore {qid}`",
            ]))
        parts.append("")
    prior = [q for q in result.quarantine if q.state == "quarantined"]
    if prior:
        parts.append("Still quarantined from earlier runs (redetected):")
        parts.append("")
        parts.append(_head(["test", "id", "state", "restore command"]))
        for q in sorted(prior, key=lambda x: x.id):
            parts.append(_row([
                f"`{q.test_id}`", q.id, q.state,
                f"`refine-tests restore {q.id}`"]))
        parts.append("")
    approved = [q for q in result.quarantine if q.state == "approved-removed"]
    if approved:
        parts.append("Permanently removed with human sign-off:")
        parts.append("")
        parts.append(_head(["test", "id", "signed off by", "at"]))
        for q in sorted(approved, key=lambda x: x.id):
            sign = q.human_signoff or {}
            parts.append(_row([
                f"`{q.test_id}`", q.id,
                f"{sign.get('name', 'unknown')} <{sign.get('email', '')}>",
                sign.get("signed_at", "—")]))
        parts.append("")
    if not removed and not prior and not approved:
        parts.append("_No quarantined tests._")
        parts.append("")

    # ------------------------------------------------------ 5. proposed patches
    parts.append("## 5. Proposed patches")
    parts.append("")
    proposals = result.proposals
    if proposals:
        parts.append(_head(["test", "action", "patch", "production bug it guards"]))
        for p in sorted(proposals, key=lambda x: (x.get("kind", ""),
                                                  x.get("id", ""))):
            parts.append(_row([
                f"`{p.get('test_id', '')}`",
                p.get("kind", ""),
                f"[{p.get('path', '')}](proposed/{p.get('path', '')})",
                p.get("bug", "")[:W],
            ]))
    else:
        parts.append("_No proposals staged. In audit mode `proposed/` is never "
                     "written (§9); run `refine-tests --cleanup --yes` to "
                     "stage refine/strengthen patches and replace proposals._")
    parts.append("")

    # ------------------------------------------------------ 6. vetoed removals
    parts.append("## 6. Vetoed removals")
    parts.append("")
    vetoed = [d for d in result.decisions if d.vetoed]
    if vetoed:
        parts.append(_head(["test", "veto signal", "reason"]))
        for d in sorted(vetoed, key=lambda x: x.test_id):
            for v in d.vetoes:
                parts.append(_row([
                    f"`{d.test_id}`", v.signal,
                    (v.reason.replace("|", "\\|")[:W] + "…")
                    if len(v.reason) > W else v.reason.replace("|", "\\|")]))
            parts.append(
                _row([f"`{d.test_id}`", "downgrade",
                      "recommendation downgraded to **Keep (flagged)**; "
                      "detectors still reported: " +
                      ", ".join(sorted({f.category for f in d.findings}))]))
    else:
        parts.append("_No vetoed removals._")
    parts.append("")

    # --------------------------------------------- 7. what would this catch?
    parts.append('## 7. "What would this catch?"')
    parts.append("")
    catches = result.summary.get("what_would_this_catch", [])
    retained = [d for d in result.decisions
                if d.action in {"keep", "strengthen"} and not d.vetoed]
    if catches:
        parts.append("One sentence per retained/strengthened test naming the "
                     "production bug it guards:")
        parts.append("")
        for entry in catches:
            parts.append(f"- `**{entry.get('test_id')}**`: {entry.get('bug')}")
    elif retained:
        parts.append("- " + "\n- ".join(
            f"**{d.test_id}**: no production bug statement available "
            f"(detectors: "
            f"{', '.join(sorted({f.category for f in d.findings})) or 'none'})."
            for d in sorted(retained, key=lambda x: x.test_id)))
    else:
        parts.append("_No retained tests with a detector claim to name a bug "
                     "for._")
    parts.append("")
    return "\n".join(parts)
