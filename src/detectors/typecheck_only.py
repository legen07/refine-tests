"""§5.8 — Typecheck-only: every assertion is a type check the compiler /
typechecker already enforces.

For `assert isinstance(add(1, 2), int)` where `add` is annotated `-> int`, a
typechecker (mypy/pyright in a typed project) already proves the claim; the
test fails only if the annotation is violated, which is not a runtime-behavior
bug. We detect the assertion pattern (all `isinstance`) and report; whether
the typechecker actually enforces it is reported as an assumption (A4).
"""

from __future__ import annotations

from ..models import Finding, SymbolTable, Tier
from . import _util
from .base import CategoryDetector

_COMPARATOR_OPS = ("==", "!=", "<=", ">=", "<", ">", "is not", "not in",
                   "is", "in")


def _bare(comparator: str) -> str:
    """Strip the comparison operator prefix from a stored comparator.

    Comparators from `_fill_compare` carry the operator (``== int``); the
    isinstance path stores the bare type (``int``). Only strip an operator
    when it is followed by a space, so ``in`` never eats the ``in`` of
    ``int``.
    """
    stripped = comparator.strip()
    for op in _COMPARATOR_OPS:
        if stripped.startswith(op + " ") and len(stripped) > len(op) + 1:
            return stripped[len(op) + 1:].strip()
    return stripped


def _annotated_like_typecheck(node, table: SymbolTable) -> bool:
    """True when an asserted type comes from a function whose return
    annotation matches the checked type (annotation-enforced claim).

    The value-producing call usually sits *outside* the assertion (it is
    assigned first), so we resolve the test's call sites and compare each
    return annotation to the asserted type literal (the isinstance second
    argument, stored as the assertion comparator).
    """
    alias = _util.alias_map(node)
    asserted_types = {_bare(a.comparator) for a in node.assertions
                      if a.is_typecheck and a.comparator}
    for site in node.call_sites:
        resolved = _util.resolve(alias, table, site.callee)
        if resolved is None:
            continue
        module, symbol = resolved
        info = table.modules.get(module, {}).get(symbol.split(".")[0])
        if info is None or not info.return_annotation:
            continue
        if info.return_annotation.strip() in asserted_types:
            return True
    return False


def detect(tests, ctx) -> list[Finding]:
    table: SymbolTable = ctx.get("symbol_table") or SymbolTable()
    deltas = ctx["calibration"]
    tier_of: dict[str, Tier] = ctx.get("tier_of", {})
    out: list[Finding] = []
    for node in tests:
        if not node.assertions:
            continue
        typechecks = [a for a in node.assertions if a.is_typecheck]
        if not typechecks or len(typechecks) != len(node.assertions):
            continue
        annotated = _annotated_like_typecheck(node, table)
        base = _util.base_conf(ctx, "typecheck_only", 0.6 if annotated else 0.4)
        conf = _util.calibrate(base, [], deltas)
        out.append(Finding(
            test_id=node.id,
            category="typecheck-only",
            confidence=conf,
            tier=tier_of.get(node.id, node.tier),
            detector="typecheck_only",
            recommended_action="strengthen",
            signals=[],
            evidence=[
                "Every assertion is an isinstance/type guard "
                f"({len(typechecks)} of {len(node.assertions)}).",
                ("At least one guarded value comes from a function with a "
                 "matching return annotation, so a typechecker already "
                 "enforces the claim.") if annotated else
                ("No matching return annotation was resolved; the finding is "
                 "reported at reduced confidence (see ASSUMPTIONS A4)."),
            ],
        ))
    return out


__detector__: CategoryDetector = detect
