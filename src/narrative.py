"""Deterministic narrative generation for the report (§10 sections 6–7).

Everything here is rule-based and sorted: no LLM, no wall-clock, no randomness
— running audit twice must produce byte-identical reports (§14.6).
"""

from __future__ import annotations

import re

from .models import CiHistory, Decision, TestNode


def _first_symbol(node: TestNode) -> str | None:
    for sym in node.covered_public_symbols:
        return sym
    return None


def _regression_hint(node: TestNode, ci: CiHistory | None) -> str:
    """Name the historical regression a retained test guarded (if known)."""
    if ci is None or not ci.runs:
        return ""
    results = ci.results_for(node.coverage_key or node.id)
    failures = sum(1 for r in results if r != "passed")
    if failures and len(results):
        return (f" — it already failed in {failures} of {len(results)} CI "
                f"runs, so this regression is not hypothetical")
    return ""


def what_would_this_catch(node: TestNode, ci: CiHistory | None) -> str:
    """One sentence naming the production bug a retained test guards (§10.7)."""
    symbol = _first_symbol(node)
    target = symbol or next(
        (c.callee for c in node.call_sites if c.callee != "<expr>"),
        "the code under test")
    assertion = next((a for a in node.assertions if not a.on_mock), None)
    if assertion is not None and assertion.comparator:
        claim = re.sub(r"\s+", " ", assertion.comparator.strip())
        claim = claim[:60] + ("…" if len(claim) > 60 else "")
        tail = f' would no longer hold the claim "{claim}"'
    elif assertion is not None and assertion.kind == "truthy":
        tail = " would silently stop returning a value"
    else:
        tail = " could break without this test noticing the behavior change"
    hint = _regression_hint(node, ci)
    return (f"Guards **{target}**: if a future change regressed {target}, "
            f"the test{tail}{hint}.")
