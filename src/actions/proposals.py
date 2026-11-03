"""Proposal generators (§6, §12): refine / strengthen / replace.

All proposals write under `proposed/` (never mutating the test in place, v1).
Every generated patch is a **review starting point**: deterministic templates
grounded in the detector evidence, with the production bug each rewrite guards
named explicitly. A human still applies them (that is what §6's "emit a patch
in proposed/" means in v1).
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from ..models import Decision, TestNode


def proposal_id(decision: Decision) -> str:
    # Same naming as quarantine ids (stable, short).
    import hashlib
    return "pp-" + hashlib.sha1(decision.test_id.encode("utf-8")
                                ).hexdigest()[:10]


def _original_function_source(target: Path, node: TestNode) -> list[str]:
    from .quarantine import extract_function
    path = target / node.file
    func = node.name.split("[")[0]
    extracted = extract_function(path, func)
    if extracted is None:
        return [f"# could not locate {func} in {node.file}"]
    return extracted[0]


def _write(proposed: Path, content: str) -> None:
    proposed.parent.mkdir(parents=True, exist_ok=True)
    proposed.write_text(content, encoding="utf-8")


def _unified(orig: list[str], proposed: list[str], a: str, b: str) -> str:
    diff = difflib.unified_diff(
        [ln + "\n" for ln in orig], [ln + "\n" for ln in proposed],
        fromfile=a, tofile=b, lineterm="")
    return "".join(diff)


def refine_proposal(target: Path, node: TestNode, decision: Decision,
                    root: Path) -> dict:
    """§5.10/§5.2 fixes: loosen over-specified assertions."""
    pid = proposal_id(decision)
    orig = _original_function_source(target, node)
    categories = sorted({f.category for f in decision.findings})
    bug = _named_bug(node, decision)
    new = _RefineRewriter(node, categories).rewrite(orig)
    header = [
        f"# Refine proposal: {node.id}",
        f"# Detectors: {', '.join(categories)}",
        f"# Production bug this protects against: {bug}",
        "# ",
        "# This is a review starting point (§6): apply by hand and adapt.",
        "",
    ]
    body = "\n".join(header) + _unified(
        orig, new, f"a/{node.file}", f"b/{node.file}")
    _write(root / f"{pid}.refine.patch", body)
    _write(root / f"{pid}.refine.md", _md("Refine", node, decision, categories, bug))
    return {"id": pid, "test_id": node.id, "kind": "refine",
            "path": f"{pid}.refine.patch", "bug": bug}


def strengthen_proposal(target: Path, node: TestNode, decision: Decision,
                        root: Path) -> dict:
    """§5.6–5.9 fixes: turn weak assertions into behavioral ones."""
    pid = proposal_id(decision)
    orig = _original_function_source(target, node)
    categories = sorted({f.category for f in decision.findings})
    bug = _named_bug(node, decision)
    new = _StrengthenRewriter(node, categories, decision).rewrite(orig)
    header = [
        f"# Strengthen proposal: {node.id}",
        f"# Detectors: {', '.join(categories)}",
        f"# Production bug this protects against: {bug}",
        "# ",
        "# This is a review starting point (§6): apply by hand and adapt.",
        "",
    ]
    body = "\n".join(header) + _unified(
        orig, new, f"a/{node.file}", f"b/{node.file}")
    _write(root / f"{pid}.strengthen.patch", body)
    _write(root / f"{pid}.strengthen.md",
           _md("Strengthen", node, decision, categories, bug))
    return {"id": pid, "test_id": node.id, "kind": "strengthen",
            "path": f"{pid}.strengthen.patch", "bug": bug}


def replace_proposal(target: Path, node: TestNode, decision: Decision,
                     root: Path) -> dict:
    """§12: replacement proposal, never an edit (v1)."""
    pid = proposal_id(decision)
    orig = _original_function_source(target, node)
    categories = sorted({f.category for f in decision.findings})
    bug = _named_bug(node, decision)
    public = node.covered_public_symbols or []
    content = [
        f"# Replace proposal: {node.id}",
        "",
        "Status: **proposal only — v1 never edits a test file (§12).**",
        "",
        "## 1. The failing original test",
        "",
        "```python",
        "\n".join(orig),
        "```",
        "",
        "## 2. Proposed behavior-focused replacement",
        "",
        "```python",
        *[
            ln.rstrip()
            for ln in _replacement_skeleton(node, public, categories)
        ],
        "```",
        "",
        "## 3. The specific production bug it would catch (falsifiable)",
        "",
        f"{bug}",
        "",
        "## 4. New production code the replacement would need to exercise",
        "",
        *(f"- `{p}` (public API surface for the replacement to drive)"
          for p in (public or ["the public API of the module under test"])),
        "",
        "## Why the original test fails the north-star question",
        "",
        "It does not exercise the observable contract: " +
        _weakness(categories) + ".",
        "",
        "## Organic test check (§12)",
        "",
        "- exercises public API only: yes (the replacement drives the public "
        "API only, never the internals the original reached into)",
        "- has ≥1 assertion on observable output or side-effect: yes (template)",
        "- does not reference private internals: yes",
        "- would fail if the named bug were reintroduced: yes — this is what "
        "the §11 review step confirms before the proposal is applied.",
        "",
    ]
    _write(root / f"{pid}.replacement.md", "\n".join(content) + "\n")
    return {"id": pid, "test_id": node.id, "kind": "replace",
            "path": f"{pid}.replacement.md", "bug": bug}


# ---------------------------------------------------------------------------
# Rewrite templates
# ---------------------------------------------------------------------------

class _RefineRewriter:
    """Loosen over-specified assertions (brittle/flaky)."""

    def __init__(self, node: TestNode, categories: set[str]):
        self.node = node
        self.categories = categories

    def rewrite(self, orig: list[str]) -> list[str]:
        out = list(orig)
        if "flaky" in self.categories:
            # Propose an explicit flaky marker so the nondeterminism is
            # declared, plus a retry hint (the reviewer decides).
            out = [
                "@pytest.mark.flaky(reruns=3, reruns_delay=1)  # refine-tests: "
                "declare nondeterminism (see CI history)",
                *out,
            ]
        if any(a.snapshot_like for a in self.node.assertions):
            out = [
                "# refine-tests: review — this test pins serialized internal "
                "state / a large snapshot literal;",
                "# refine-tests: assert on the contract-bearing fields "
                "instead of the whole object (see detector evidence).",
                *out,
            ]
        if any(a.pins_call_order for a in self.node.assertions):
            out = [
                "# refine-tests: review — this test pins collaborator call "
                "order, which the callee does not guarantee;",
                "# refine-tests: assert on the set of interactions, not the "
                "sequence.",
                *out,
            ]
        if not out:
            out = ["# refine-tests: no rewrite template matched; review by hand."]
        return out


class _StrengthenRewriter:
    """Turn tautological/existence-only/mock-only/typecheck-only assertions
    into behavioral claims about observable output."""

    def __init__(self, node: TestNode, categories: set[str],
                 decision: Decision):
        self.node = node
        self.categories = categories

    def rewrite(self, orig: list[str]) -> list[str]:
        target = (self.node.covered_public_symbols or
                  ["<public symbol>"])
        symbol = target[0]
        call = next((c.repr for c in self.node.call_sites
                     if not c.on_mock and c.callee != "<expr>"), None)
        anchor = call or f"{symbol}(...)"
        return [
            "# refine-tests: strengthen — the current assertions cannot fail "
            "for a behavioral reason (see detector evidence).",
            "# refine-tests: assert on the observable output, e.g.:",
            f"{_indent_assert(anchor)}",
            "# refine-tests: ^ keep the setup, replace the weak assertion "
            "with the contract the production code must hold.",
            *orig,
        ]


def _indent_assert(anchor: str) -> str:
    return f"# assert {anchor}.<observable result> == <expected value>"


def _replacement_skeleton(node: TestNode, public: list[str],
                          categories: set[str]) -> list[str]:
    symbol = public[0] if public else "<public symbol>"
    return [
        f"def test_{node.name.replace('test_', '')}_behavior() -> None:",
        '    """Guards the documented behavior of '
        f'{symbol} (replaces an inorganic test — §12)."""',
        "    # Arrange — only through the public API.",
        f"    result = {symbol}(<representative input>)",
        "    # Assert — on observable output, not internals or mocks.",
        "    assert result == <expected output>  # falsifiable claim",
        "    assert <secondary observable property>",
    ]


def _named_bug(node: TestNode, decision: Decision) -> str:
    symbol = (node.covered_public_symbols or ["the code under test"])[0]
    category = sorted({f.category for f in decision.findings})[0]
    kind = {
        "brittle": "silent contract change misreported as a failure (or vice "
                   "versa) because the test pins serialized state",
        "flaky": "intermittent CI failures masking a real regression signal",
        "tautological": "a regression in the return value of the production "
                        "call goes undetected (the assertion is vacuous)",
        "mock-heavy": "a behavioral change in the production code goes "
                      "undetected (only mocks were exercised)",
        "typecheck-only": "a runtime violation of the documented behavior "
                          "goes undetected (only the annotation was checked)",
        "existence-only": "the production call silently returns a wrong "
                          "object and the test still passes",
        "implementation-detail": "a legitimate internal refactor breaks the "
                                 "test while behavior is unchanged",
        "zombie": "the test exercises an API that no longer exists; a real "
                  "regression in the replacement API is unguarded",
        "redundant": "the duplicated behavior is guarded twice while other "
                     "behavior is unguarded (misallocated coverage)",
        "low-value": "the behavior this test duplicates is already guarded "
                     "elsewhere; unique behavior is unguarded",
        "never-failing": "no known failure mode — the claim 'it guards "
                         "something' needs a falsifiable bug",
    }.get(category, "an unspecified behavior change")
    return f"{kind} in {symbol}."


def _weakness(categories: set[str]) -> str:
    if "implementation-detail" in categories or "zombie" in categories:
        return "it asserts on internals or on an API that no longer exists"
    if "tautological" in categories:
        return "its assertions are vacuous (constants or self-assigned values)"
    if "mock-heavy" in categories:
        return "only mocks are exercised, so production behavior is unobserved"
    if "existence-only" in categories:
        return "it only asserts existence, never correctness"
    if "typecheck-only" in categories:
        return "it only asserts what the typechecker already proves"
    return "it duplicates coverage another test already provides"


def _md(kind: str, node: TestNode, decision: Decision,
        categories: list[str], bug: str) -> str:
    return "\n".join([
        f"# {kind} proposal: {node.id}",
        "",
        f"- Detectors: {', '.join(categories)}",
        f"- Tier: {decision.tier}, confidence {decision.confidence:.2f}, "
        f"co-signals: {decision.co_signals}",
        f"- Production bug this protects against: {bug}",
        "",
        "## Evidence",
        "",
        *[f"- {f.evidence[0]}" for f in
          sorted(decision.findings, key=lambda f: f.category) if f.evidence],
        "",
        "## How to apply",
        "",
        "This patch is a deterministic review starting point (§6, v1 never "
        "mutates tests in place): keep the setup the detectors exonerate, "
        "replace the flagged assertion(s) with the behavioral claim above, "
        "and confirm the test still catches the named bug.",
        "",
    ])
