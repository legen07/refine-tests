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
    p = Path(file)
    return ".".join(p.with_suffix("").parts)


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
            file, _, lineno = entry.rpartition(":")
            module = module_of_file(file)
            owner = owners.get((module, int(lineno)))
            if owner is None:
                continue
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
            file, _, lineno = entry.rpartition(":")
            module = module_of_file(file)
            owner = owners.get((module, int(lineno)))
            if owner is None:
                continue
            head = owner.split(".")[0]
            info = table.modules.get(module, {}).get(head)
            if info is not None and info.is_public:
                covered.setdefault(f"{module}.{owner}", set()).add(node.id)
    return {k: set(sorted(v)) for k, v in sorted(covered.items())}
