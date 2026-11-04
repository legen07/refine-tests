"""§5.4 — Zombie / obsolete: test imports or references symbols that are absent
from production source (AST + symbol table).

Signals (calibration row): `symbol_referenced_missing` (+0.6).
Primary remediation is replacement (§12): the test's premise is dead, so a
behavior-focused replacement proposal is the right artifact.
"""

from __future__ import annotations

from ..models import Finding, SymbolTable, TestNode, Tier
from . import _util
from .base import CategoryDetector


def _missing_from_imports(node: TestNode, table: SymbolTable) -> list[str]:
    # Module-level imports are shared by every test in the file (all of
    # JS/TS, most of Python): a dead symbol that the test body never touches
    # is file-level plumbing, not this test's decay. Only report an import
    # when the test's own body actually uses the dead name — the reference
    # scan below covers the same case, this keeps the evidence precise.
    used = {r.split(".")[0] for r in node.references}
    missing: list[str] = []
    for module, symbol, exists in _util.test_module_aliases(node, table):
        if not exists and symbol in used:
            missing.append(f"{module}.{symbol}")
    return missing


def _missing_from_references(node: TestNode, table: SymbolTable) -> list[str]:
    alias = _util.alias_map(node)
    missing: list[str] = []
    for ref in sorted(node.references):
        resolved = _util.resolve(alias, table, ref)
        if resolved is None:
            continue
        module, symbol = resolved
        if module and symbol:
            parts = symbol.split(".")
            head = parts[0]
            syms = table.modules.get(module, {})
            if head not in syms:
                missing.append(f"{module}.{symbol}")
    return missing


def _missing_from_getattr(node: TestNode, table: SymbolTable) -> list[str]:
    alias = _util.alias_map(node)
    missing: list[str] = []
    for local, literal in node.getattr_calls:
        target = alias.get(local)
        if target is None:
            continue
        module = target if target in table.modules else target.split(".")[0]
        syms = table.modules.get(module, {})
        if literal not in syms:
            missing.append(f"{module}.{literal}")
    return missing


def detect(tests, ctx) -> list[Finding]:
    table: SymbolTable = ctx.get("symbol_table") or SymbolTable()
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []
    for node in tests:
        missing: list[str] = []
        missing += _missing_from_imports(node, table)
        missing += _missing_from_references(node, table)
        missing += _missing_from_getattr(node, table)
        missing = sorted(set(missing))
        if not missing:
            continue
        sample = ", ".join(f"`{m}`" for m in missing[:3])
        base = _util.base_conf(ctx, "zombie", 0.4)
        conf = _util.calibrate(
            base, ["symbol_referenced_missing"], deltas)
        out.append(Finding(
            test_id=node.id,
            category="zombie",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="zombie",
            recommended_action="replace",
            signals=["symbol_referenced_missing"],
            evidence=[
                f"References production symbol(s) absent from the symbol "
                f"table: {sample}.",
                "The production API moved; this test cannot fail for the "
                "reason its author intended (it errors at import/use).",
            ],
        ))
    return out


__detector__: CategoryDetector = detect
