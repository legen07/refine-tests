"""§5.11 — Implementation-detail: asserts on private methods, module-level
constants used only internally, or object identity of internals.

Primary remediation is replacement (§12): the test should express the same
intent through the public API, so a behavior-focused replacement proposal is
the right artifact.
"""

from __future__ import annotations

from ..models import Finding, SymbolTable, TestNode, Tier
from . import _util
from .base import CategoryDetector


def _private_call_targets(node: TestNode, table: SymbolTable) -> list[str]:
    alias = _util.alias_map(node)
    out: list[str] = []
    for call in node.call_sites:
        resolved = _util.resolve(alias, table, call.callee)
        if resolved is None:
            continue
        module, symbol = resolved
        if not symbol:
            continue
        head = symbol.split(".")[0]
        info = table.modules.get(module, {}).get(head)
        if info is not None and not info.is_public:
            out.append(f"{module}.{symbol}")
    return sorted(set(out))


def detect(tests, ctx) -> list[Finding]:
    table: SymbolTable = ctx.get("symbol_table") or SymbolTable()
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []
    for node in tests:
        reasons: list[str] = []
        priv_calls = _private_call_targets(node, table)
        if priv_calls:
            reasons.append(
                "Calls private production symbol(s): "
                + ", ".join(f"`{p}`" for p in priv_calls[:3]) + ".")
        private_reads = sorted({
            ref for ref in node.references
            for part in ref.split(".")
            if part.startswith("_") and not (part.startswith("__") and
                                             part.endswith("__"))})
        if private_reads:
            reasons.append(
                "Asserts on private names / internal constants: "
                + ", ".join(f"`{p}`" for p in private_reads[:3]) + ".")
        identity = [a for a in node.assertions
                    if a.kind == "compare" and a.comparator.startswith(("is",))]
        dict_reads = [a for a in node.assertions if a.reads_privates]
        if dict_reads:
            reasons.append(
                "Asserts on serialized/identity of internals "
                "(`__dict__` / private attributes).")
        if not reasons:
            continue
        base = _util.base_conf(ctx, "implementation_detail", 0.6)
        conf = _util.calibrate(base, [], deltas)
        out.append(Finding(
            test_id=node.id,
            category="implementation-detail",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="implementation_detail",
            recommended_action="replace",
            signals=[],
            evidence=reasons,
        ))
    return out


__detector__: CategoryDetector = detect
