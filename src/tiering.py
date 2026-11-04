"""Risk-based tiering (§4 BASE): smoke | core | extended.

* smoke    — explicitly marked (`pytest.mark.smoke`, `smoke` in the file name).
* core     — exercises at least one *public* production symbol (derived from
             the per-test coverage fingerprint + the symbol table).
* extended — everything else.

Tier drives a §7 veto (tier "smoke" removals are downgraded) and the scoped
mode default (smoke+core only).
"""

from __future__ import annotations

from pathlib import Path

from .models import CoverageSet, SymbolTable, TestNode, Tier


def _is_smoke(node: TestNode, smoke_markers: list[str]) -> bool:
    lowered = {m.lower() for m in smoke_markers}
    if any(m.lower() in lowered for m in node.markers):
        return True
    return any(part.lower() in lowered
               for part in Path(node.file).parts)


def symbols_by_line(table: SymbolTable) -> dict[tuple[str, int], str]:
    """(module, lineno) -> owning symbol, for both public and private symbols."""
    owners: dict[tuple[str, int], str] = {}
    for module, syms in table.modules.items():
        for name, info in syms.items():
            for lineno in range(info.lineno, (info.end_lineno or info.lineno) + 1):
                owners[(module, lineno)] = name
            for mname, minfo in info.members.items():
                for lineno in range(minfo.lineno,
                                    (minfo.end_lineno or minfo.lineno) + 1):
                    owners[(module, lineno)] = f"{name}.{mname}"
    return owners


def module_of_file(file: str) -> str:
    if not file:
        return ""                # malformed coverage entry: callers skip
    p = Path(file)
    return ".".join(p.with_suffix("").parts)


def _owner_of(owners: dict[tuple[str, int], str], entry: str
              ) -> tuple[str, str] | None:
    """Resolve a ``file:lineno`` coverage entry to ``(module, owner)``.

    Coverage documents are user-supplied (the JS/TS one is built by a
    project script); a malformed key must never crash the audit — it is
    skipped, and the audit reports the coverage it could attribute.
    """
    file, _, lineno = entry.rpartition(":")
    if not file or not lineno.lstrip("-").isdigit():
        return None
    module = module_of_file(file)
    if not module:
        return None
    owner = owners.get((module, int(lineno)))
    if owner is None:
        return None
    return module, owner


def assign_tiers(
    tests: list[TestNode],
    table: SymbolTable,
    coverage: dict[str, CoverageSet],
    cfg: dict,
) -> tuple[dict[str, Tier], dict[str, list[str]]]:
    """Returns (tier_by_test_id, public_symbols_by_test_id)."""
    smoke_markers = (cfg.get("tiers", {}) or {}).get("smoke_markers", ["smoke"])
    owners = symbols_by_line(table)
    tier_by_id: dict[str, Tier] = {}
    pub_by_id: dict[str, list[str]] = {}
    for node in tests:
        cov = coverage.get(node.coverage_key or node.id) or CoverageSet()
        public: set[str] = set()
        for entry in sorted(cov.lines):
            resolved = _owner_of(owners, entry)
            if resolved is None:
                continue
            module, owner = resolved
            head = owner.split(".")[0]
            info = table.modules.get(module, {}).get(head)
            if info is not None and info.is_public:
                public.add(f"{module}.{owner}")
        tier: Tier = "extended"
        if _is_smoke(node, smoke_markers):
            tier = "smoke"
        elif public and (cfg.get("tiers", {}) or {}).get(
                "core_if_covers_public_symbol", True):
            tier = "core"
        tier_by_id[node.id] = tier
        node.tier = tier
        node.covered_public_symbols = sorted(public)
        pub_by_id[node.id] = sorted(public)
    return tier_by_id, pub_by_id


def symbol_coverage(
    tests: list[TestNode],
    table: SymbolTable,
    coverage: dict[str, CoverageSet],
) -> dict[str, set[str]]:
    """public symbol -> set of test ids covering it (feeds the §7 veto)."""
    owners = symbols_by_line(table)
    covered: dict[str, set[str]] = {}
    for node in tests:
        cov = coverage.get(node.coverage_key or node.id) or CoverageSet()
        for entry in cov.lines:
            resolved = _owner_of(owners, entry)
            if resolved is None:
                continue
            module, owner = resolved
            head = owner.split(".")[0]
            info = table.modules.get(module, {}).get(head)
            if info is not None and info.is_public:
                covered.setdefault(f"{module}.{owner}", set()).add(node.id)
    return {k: set(sorted(v)) for k, v in sorted(covered.items())}
