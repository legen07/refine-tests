"""Language-agnostic data model shared by the engine, detectors, and reports.

The pipeline is deliberately split in two (§11):

* **Language detectors** (`Detector`, e.g. `PythonDetector`) parse a real
  source tree and normalize every test into the `TestNode` structure below.
* **Category detectors** (one module per §5 category) operate purely on
  these normalized structures plus `CoverageSet` / `SymbolTable` / `CiHistory`
  — never on language-specific syntax. Adding a language cannot require a
  core or category-detector edit.

Every number that flows into a report is carried by a dataclass; the JSON
emitter serializes these directly so the machine schema stays stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Enumerations (kept as plain Literal aliases so the schema is grep-able)
# ---------------------------------------------------------------------------

Category = Literal[
    "never-failing",
    "flaky",
    "redundant",
    "zombie",
    "low-value",
    "tautological",
    "mock-heavy",
    "typecheck-only",
    "existence-only",
    "brittle",
    "implementation-detail",
]

Action = Literal[
    "keep",
    "remove",
    "refine",
    "strengthen",
    "replace",
    "quarantined",          # already quarantined in a previous run
]

Tier = Literal["smoke", "core", "extended"]

Confidence = float                          # ∈ [0, 1]

# ---------------------------------------------------------------------------
# Normalized representation of one test
# ---------------------------------------------------------------------------


@dataclass
class AssertionInfo:
    """One assertion, normalized away from language syntax."""

    kind: str = "compare"          # compare | truthy | not-none | isinstance | exists-in | raises | call-order | other
    negated: bool = False
    # The compared value (if any), as a stable string, e.g. "== 5", "is not None"
    comparator: str = ""
    # True iff every operand is a compile-time constant (tautology detector)
    is_constant: bool = False
    # True iff the asserted value was assigned by the test itself one statement
    # earlier (`x = 5; assert x == 5`)
    self_assigned: bool = False
    # True iff the assertion interrogates a mock rather than production code
    on_mock: bool = False
    # True iff the assertion is a isinstance/type check the typechecker enforces
    is_typecheck: bool = False
    # True iff this is `assert x`, `assert x is not None`, `x in coll`
    is_existence: bool = False
    # True iff the assertion compares against a large inline literal
    # (snapshot / serialized-internal-state detector)
    snapshot_like: bool = False
    # True iff the assertion pins the exact call order of collaborators
    pins_call_order: bool = False
    # True iff the assertion reads private internals (`_x`, `__dict__`)
    reads_privates: bool = False
    # Names referenced by the assertion, resolved against the symbol table
    referenced: list[str] = field(default_factory=list)
    # Free-form evidence string, used verbatim in reports
    detail: str = ""


@dataclass
class CallSiteInfo:
    """A call expression inside the test body."""

    repr: str = ""                 # stable textual form, e.g. `calc.add(1, 2)`
    callee: str = ""               # resolved dotted callee, e.g. `prod.calc.add`
    base_name: str = ""            # root object name, e.g. `calc`
    on_mock: bool = False          # resolves to a mock bound in this test
    lineno: int = 0


@dataclass
class ImportInfo:
    module: str = ""               # e.g. `prod.pricing`
    symbol: str = ""               # imported name; "" for `import prod.pricing`
    local: str = ""                # name the test binds it to (asname / symbol)


@dataclass
class TestNode:
    """A single (possibly parametrized) test, language-agnostic."""

    # pytest must not try to collect this dataclass as a test class.
    __test__ = False

    id: str = ""                   # nodeid, e.g. `tests/test_calc.py::test_add`
    file: str = ""                 # path relative to the audit target root
    name: str = ""                 # bare test function / it-name
    language: str = "python"
    lineno: int = 0
    end_lineno: int = 0
    body_line_count: int = 0
    markers: list[str] = field(default_factory=list)      # e.g. ["smoke"]
    params: list[str] = field(default_factory=list)       # parametrize ids
    imports: list[ImportInfo] = field(default_factory=list)
    references: set[str] = field(default_factory=set)     # every name read
    assertions: list[AssertionInfo] = field(default_factory=list)
    call_sites: list[CallSiteInfo] = field(default_factory=list)
    mock_binding_names: dict[str, str] = field(default_factory=dict)  # name -> mock kind
    # Mock configurations: (mock base name, configured literal repr), e.g.
    # `m.return_value = 5` -> ("m", "5"). Feeds the tautology detector.
    mock_configs: list[tuple[str, str]] = field(default_factory=list)
    # `with patch("<dotted.target>")` patch targets in the test body; feeds
    # the mock-heavy detector (patched calls are exercised mocks).
    patched_targets: list[str] = field(default_factory=list)
    # getattr(<module>, "<literal>") pairs; feeds the zombie detector.
    getattr_calls: list[tuple[str, str]] = field(default_factory=list)
    # Assertion-free textual form of the test body, for evidence snippets.
    source_excerpt: str = ""
    # Normalized AST digest used for near-duplicate clustering (§5.3):
    # the statement sequence with identifiers stripped to a placeholder.
    structure_digest: str = ""
    # Assertion skeleton (literals kept, variables abstracted), formed by the
    # language detector; used by the redundancy detector's "same assertions
    # modulo rename" test.
    assertion_skeleton: tuple[str, ...] = ()
    # Per-param coverage key as used by the coverage mechanism (nodeid).
    coverage_key: str = ""
    tier: Tier = "extended"
    # Filled by the engine after the coverage run.
    coverage: "CoverageSet | None" = None
    # Filled by tiering: public production symbols this test exercises.
    covered_public_symbols: list[str] = field(default_factory=list)

    @property
    def is_parametrized(self) -> bool:
        return bool(self.params)

    def fingerprint_params(self) -> list[str]:
        """§5 note: fingerprint per parameter set."""
        return [f"{self.id}[{p}]" for p in self.params] or [self.id]


# ---------------------------------------------------------------------------
# Coverage, symbols, CI history
# ---------------------------------------------------------------------------


@dataclass
class CoverageSet:
    """Per-test coverage fingerprint (§2 mechanism)."""

    test_id: str = ""
    lines: set[str] = field(default_factory=set)          # {"prod/calc.py:12"}
    branches: set[str] = field(default_factory=set)       # {"prod/calc.py:8->9"}
    missing: set[str] = field(default_factory=set)

    def jaccard(self, other: "CoverageSet") -> float:
        union = self.lines | other.lines
        if not union:
            return 0.0
        return len(self.lines & other.lines) / len(union)

    def is_subset_of(self, other: "CoverageSet") -> bool:
        # Strict subset with at least one line: a test that covers nothing is
        # not "covered by" another test.
        return bool(self.lines) and self.lines <= other.lines


@dataclass
class SymbolInfo:
    name: str = ""
    kind: str = "function"           # function | class | constant | method
    module: str = ""
    lineno: int = 0
    end_lineno: int = 0
    is_public: bool = True
    return_annotation: str = ""
    members: dict[str, "SymbolInfo"] = field(default_factory=dict)


@dataclass
class SymbolTable:
    """Production symbol table used for zombie detection (§5.4)."""

    # module path -> {symbol name -> SymbolInfo}
    modules: dict[str, dict[str, SymbolInfo]] = field(default_factory=dict)

    def public_symbols(self) -> list[str]:
        out: list[str] = []
        for mod, syms in sorted(self.modules.items()):
            for name, info in sorted(syms.items()):
                if info.is_public:
                    out.append(f"{mod}.{name}")
        return out

    def has(self, module: str, symbol: str) -> bool:
        return symbol in self.modules.get(module, {})

    def line_range(self, module: str, symbol: str) -> tuple[int, int] | None:
        info = self.modules.get(module, {}).get(symbol)
        if info is None:
            return None
        return info.lineno, info.end_lineno


@dataclass
class TestResult:
    test_id: str = ""
    status: str = "passed"           # passed | failed | skipped | error
    duration_s: float = 0.0


@dataclass
class CiRun:
    run_id: str = ""
    status: str = "passed"
    test_results: list[TestResult] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)


@dataclass
class CiHistory:
    provider: str = "unavailable"
    runs: list[CiRun] = field(default_factory=list)

    @property
    def run_count(self) -> int:
        return len(self.runs)

    def results_for(self, test_id: str) -> list[str]:
        out: list[str] = []
        for run in self.runs:
            for tr in run.test_results:
                if tr.test_id == test_id:
                    out.append(tr.status)
                    break
        return out

    def churn_for(self, path: str) -> int:
        return sum(1 for run in self.runs if path in run.files_changed)


# ---------------------------------------------------------------------------
# Detector outputs
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """§5 finding: {test_id, category, confidence, evidence, tier} + engine extras."""

    test_id: str = ""
    category: Category = "tautological"
    confidence: Confidence = 0.0
    tier: Tier = "extended"
    detector: str = ""               # module name that produced it
    recommended_action: Action = "keep"
    signals: list[str] = field(default_factory=list)   # calibration keys (§7)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "tier": self.tier,
            "detector": self.detector,
            "recommended_action": self.recommended_action,
            "signals": sorted(self.signals),
            "evidence": self.evidence,
        }


@dataclass
class Mutant:
    id: str = ""
    module: str = ""
    lineno: int = 0
    description: str = ""
    kind: str = ""


@dataclass
class MutationResult:
    test_id: str = ""
    mutants: list[Mutant] = field(default_factory=list)
    killed: int = 0
    survived: int = 0
    score: float = 0.0               # killed / total (0.0 if no mutants ran)
    over_budget: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "mutant_count": len(self.mutants),
            "killed": self.killed,
            "survived": self.survived,
            "score": round(self.score, 3),
            "over_budget": self.over_budget,
            "mutants": [
                {"id": m.id, "module": m.module, "lineno": m.lineno,
                 "description": m.description}
                for m in self.mutants
            ],
        }


# ---------------------------------------------------------------------------
# Engine-level decisions
# ---------------------------------------------------------------------------


@dataclass
class Veto:
    signal: str = ""
    reason: str = ""


@dataclass
class Decision:
    """Engine verdict for one test after calibration, gating, and vetoes."""

    test_id: str = ""
    file: str = ""
    tier: Tier = "extended"
    action: Action = "keep"
    findings: list[Finding] = field(default_factory=list)
    confidence: Confidence = 0.0
    co_signals: int = 0
    vetoes: list[Veto] = field(default_factory=list)
    # Human-readable one-liners required by §10 sections 3–7.
    evidence_line: str = ""
    what_would_this_catch: str = ""
    mutation: MutationResult | None = None
    # Filled by actions in cleanup mode.
    quarantine_id: str = ""
    proposal_path: str = ""

    @property
    def vetoed(self) -> bool:
        return bool(self.vetoes) and self.action == "keep"

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "file": self.file,
            "tier": self.tier,
            "action": self.action,
            "confidence": round(self.confidence, 3),
            "co_signals": self.co_signals,
            "findings": [f.to_dict() for f in sorted(
                self.findings, key=lambda f: (f.category, f.detector))],
            "vetoes": [{"signal": v.signal, "reason": v.reason} for v in self.vetoes],
            "vetoed": self.vetoed,
            "evidence_line": self.evidence_line,
            "what_would_this_catch": self.what_would_this_catch,
            "mutation": self.mutation.to_dict() if self.mutation else None,
            "quarantine_id": self.quarantine_id,
            "proposal_path": self.proposal_path,
        }


@dataclass
class QuarantineRecord:
    id: str = ""
    test_id: str = ""
    file: str = ""
    state: str = "quarantined"       # quarantined | approved-removed | rejected
    quarantined_at: str = ""
    lineno: int = 0                  # stub slot in the test file (§8)
    end_lineno: int = 0
    human_signoff: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    original_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "test_id": self.test_id,
            "file": self.file,
            "state": self.state,
            "quarantined_at": self.quarantined_at,
            "human_signoff": self.human_signoff,
            "findings": self.findings,
            "original_path": self.original_path,
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
        }


@dataclass
class AuditResult:
    """Everything the report emitters need; serialized to the JSON schema."""

    run: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    quarantine: list[QuarantineRecord] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    coverage_available: bool = True
    ci: CiHistory | None = None
    mutation_run: bool = False
    # Live TestNode map (not serialized; proposal generators need the real
    # normalized structures, not rebuilt stubs).
    nodes: dict[str, "TestNode"] = field(default_factory=dict)

    def by_action(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.decisions:
            counts[d.action] = counts.get(d.action, 0) + 1
        return counts

    def decisions_for(self, action: Action) -> list[Decision]:
        return [d for d in self.decisions if d.action == action]
