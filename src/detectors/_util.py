"""Shared helpers for category detectors.

Kept language-agnostic: everything operates on the normalized `TestNode`
structures, never on Python/JS syntax.
"""

from __future__ import annotations

from typing import Any

from ..models import ImportInfo, SymbolTable, TestNode


def calibrate(base: float, signals: list[str], deltas: dict[str, float],
              low_cap: float | None = None) -> float:
    """Apply the §7 calibration table to a base confidence.

    `low_cap` implements §2: when a mechanism (e.g. per-test coverage) is
    unavailable, a detector must refuse findings above LOW confidence.
    """
    conf = base + sum(deltas.get(s, 0.0) for s in signals)
    conf = max(0.0, min(1.0, conf))
    if low_cap is not None:
        conf = min(conf, low_cap)
    return round(conf, 3)


def base_conf(ctx: Any, detector: str, default: float) -> float:
    """Base confidence for a detector, overridable via config.toml
    ([detector_confidence].<name>)."""
    overrides = ctx.get("detector_confidence", {}) or {}
    return float(overrides.get(detector, default))


def alias_map(node: TestNode) -> dict[str, str]:
    """Map a test-local name to the production dotted path it refers to.

    * `from prod import calc`      -> local `calc`  == module `prod.calc`
    * `from prod.calc import add`  -> local `add`   == symbol `prod.calc.add`
    * `import prod.calc`           -> local `prod`  == module `prod.calc`
    """
    out: dict[str, str] = {}
    for imp in node.imports:
        if not imp.local:
            continue
        if imp.symbol and imp.module:
            out[imp.local] = f"{imp.module}.{imp.symbol}"
        elif imp.symbol and not imp.module:
            out[imp.local] = imp.symbol
        elif imp.module:
            # `import prod.calc` binds the *top* name, but we also record the
            # full module for dotted references written through it.
            out[imp.local] = imp.module
            top = imp.module.split(".")[0]
            out.setdefault(top, imp.module)
    return out


def resolve(alias: dict[str, str], table: SymbolTable,
            dotted_ref: str) -> tuple[str, str] | None:
    """Resolve a dotted reference against the production symbol table.

    Returns (module, symbol) when the reference names a production symbol —
    whether or not that symbol actually exists (callers check existence).
    Returns None when the reference does not point into production code at all
    (test helpers, fixtures, mocks, builtins).
    """
    if not dotted_ref or dotted_ref.startswith("("):
        return None
    # Case 1: reference starts at a local import alias.
    head = dotted_ref.split(".")[0]
    if head in alias:
        rest = dotted_ref[len(head):].lstrip(".")
        target = alias[head]
        if not rest:
            # Reference is exactly the alias: symbol is the bound thing.
            parts = target.split(".")
            for i in range(len(parts), 0, -1):
                mod = ".".join(parts[:i])
                sym = ".".join(parts[i:])
                if mod in table.modules and (not sym or sym in table.modules[mod]):
                    return (mod, sym)
            return None
        # Rebuild the full dotted path, then split into module + symbol.
        full = f"{target}.{rest}" if target else rest
        for i in range(len(full.split(".")), 0, -1):
            mod = ".".join(full.split(".")[:i])
            sym = ".".join(full.split(".")[i:])
            if sym and mod in table.modules:
                return (mod, sym)
        return None
    # Case 2: the reference is already a full production path, e.g. when a
    # test does `import prod.calc` and writes `prod.calc.add`.
    for i in range(len(dotted_ref.split(".")), 0, -1):
        mod = ".".join(dotted_ref.split(".")[:i])
        sym = ".".join(dotted_ref.split(".")[i:])
        if sym and mod in table.modules:
            return (mod, sym)
    return None


def test_module_aliases(node: TestNode, table: SymbolTable) -> list[tuple[str, str, bool]]:
    """(module, symbol, exists) for every `from <module> import <symbol>`."""
    out: list[tuple[str, str, bool]] = []
    for imp in node.imports:
        if not imp.symbol or imp.module not in table.modules:
            # Import of a test-only helper or stdlib: skip, it is not
            # production-code coupling.
            continue
        out.append((imp.module, imp.symbol,
                    imp.symbol in table.modules[imp.module]))
    return out
