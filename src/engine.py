"""Engine: audit + cleanup orchestration (§9 modes, §8 protocol, §10 output).

Pipeline order matters for correctness and for the §14 acceptance criteria:

    discover languages → enumerate tests → symbol tables → per-test coverage
    → CI history → tiering → category detectors → mutation probe (P4, opt-in)
    → gating/vetoes → quarantine redetect → reports

Everything downstream of the language detectors is language-agnostic (§11),
and every step is deterministic (no wall-clock in reports, §14.6).
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ci as ci_mod
from . import coverage_runner, tiering
from .actions import QuarantineStore
from .actions import proposals as proposal_mod
from .detectors import registry
from .detectors.base import DetectorContext
from .gating import decide as gate_decide
from .models import (
    AuditResult,
    CoverageSet,
    Decision,
    Finding,
    SymbolTable,
    TestNode,
    Tier,
)
from .narrative import what_would_this_catch
from .report import emit_json, emit_markdown
from .actions.quarantine import extract_function, stub_source

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPORT_BASE = "refine-tests-report"


@dataclass
class AuditArgs:
    mode: str = "audit"                       # audit | cleanup
    path_glob: str | None = None              # §9 scoped
    diff: str | None = None                   # §9 scoped: base..head
    offline: bool = False                     # §9 / §14.7
    all_tiers: bool = False                   # §9 scoped default smoke+core
    yes: bool = False                         # §8 / §14.3-4
    dry_run: bool = False
    approve: list[str] = field(default_factory=list)
    approve_all: bool = False
    allow_replace_proposals: bool = False     # §12
    mutation: bool = False                    # P4
    report_dir: str | None = None
    languages: list[str] | None = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(target: Path | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    cfg_path = PLUGIN_ROOT / "config.toml"
    if cfg_path.exists():
        with open(cfg_path, "rb") as fh:
            cfg = tomllib.load(fh)
    if target is not None:
        override = Path(target) / ".refine-tests.toml"
        if override.exists():
            with open(override, "rb") as fh:
                extra = tomllib.load(fh)
            cfg = _deep_merge(cfg, extra)
    return cfg


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def feature_flags(cfg: dict, args: AuditArgs) -> dict[str, bool]:
    feats = dict((cfg.get("features", {}) or {}))
    if args.mutation:
        feats["mutation"] = True
    if args.allow_replace_proposals:
        feats["replace"] = True
    return feats


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
             ".pytest_cache", ".refine-tests-cov", "quarantine", ".ci-history"}


def discover_languages(target: Path, configured: list[str]) -> list[str]:
    found: set[str] = set()
    for dirpath, dirnames, filenames in os_walk(target):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py") and "python" in configured:
                found.add("python")
            if (fn.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts",
                             ".test.jsx", ".test.tsx", ".spec.jsx", ".spec.tsx"))
                    and ("javascript" in configured or "typescript" in configured)):
                found.update(["javascript", "typescript"])
    return sorted(lang for lang in found if lang in registry.LANGUAGE_DETECTORS)


class RefineTestsError(RuntimeError):
    """A usage-level failure (bad --diff range, unreadable target, …).

    The CLI prints it as a one-liner and exits 2; never a traceback (§16).
    """


def os_walk(root: Path):
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        yield dirpath, dirnames, filenames


def changed_files_from_diff(target: Path, diff_spec: str) -> set[str] | None:
    base, sep, head = diff_spec.partition("..")
    if not sep or not base or not head:
        raise RefineTestsError(
            f"--diff expects the form base..head, got {diff_spec!r}")
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{base}..{head}"],
            cwd=str(target), capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RefineTestsError(f"git diff failed: {exc}") from exc
    if proc.returncode != 0:
        raise RefineTestsError(
            f"git diff {base}..{head} failed: {(proc.stderr or '').strip()}")
    return {ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()}


# ---------------------------------------------------------------------------
# Coverage merge
# ---------------------------------------------------------------------------

def merge_param_coverage(tests: list[TestNode],
                         raw: dict[str, CoverageSet]) -> dict[str, CoverageSet]:
    """Per-test coverage keyed by both `id` and `coverage_key`, unioning the
    parametrized variants (§5 note: fingerprint per parameter set; the union
    is the function-level fingerprint used for tiering/veto)."""
    merged: dict[str, CoverageSet] = {}
    for node in tests:
        keys = [node.coverage_key or node.id] + \
               [f"{node.coverage_key}[{p}]" for p in node.params]
        cov = CoverageSet(test_id=node.id)
        for key in keys:
            piece = raw.get(key)
            if piece:
                cov.lines |= piece.lines
                cov.branches |= piece.branches
        cov.lines = set(sorted(cov.lines))
        cov.branches = set(sorted(cov.branches))
        merged[node.id] = cov
        if node.coverage_key:
            merged.setdefault(node.coverage_key, cov)
        node.coverage = cov if cov.lines else None
    return merged


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze(target: Path, cfg: dict, args: AuditArgs) -> AuditResult:
    target = Path(target).resolve()
    feats = feature_flags(cfg, args)
    result = AuditResult()
    run: dict[str, Any] = {
        "mode": args.mode,
        "target": str(target),
        "scope": {"type": "full", "spec": None, "tiers": None},
        "toolchain": {"languages": [], "coverage_available": False,
                      "coverage_note": "", "schema_note":
                      "languages detected by file presence (§11 registry)"},
        "ci": {"source": "unavailable", "details": ""},
        "features": feats,
        "assumptions_ref": "ASSUMPTIONS.md",
    }

    # --- language detectors --------------------------------------------------
    configured = args.languages or (cfg.get("toolchain", {}) or {}).get(
        "languages", ["python"])
    languages = discover_languages(target, configured)
    detectors = {}
    for lang in languages:
        try:
            detectors[lang] = registry.language_detector(lang, target, cfg)
        except KeyError:
            continue
    run["toolchain"]["languages"] = sorted(detectors)
    if not detectors:
        run["toolchain"]["coverage_note"] = (
            "no Detector-implemented language found among the configured "
            "languages (§13)")

    tests: list[TestNode] = []
    seen: set[str] = set()
    for det in detectors.values():
        for node in det.enumerate_tests(target):
            if node.id not in seen:
                seen.add(node.id)
                tests.append(node)

    # --- symbol tables (merged across languages) -----------------------------
    table = SymbolTable()
    for det in detectors.values():
        for module, syms in det.symbol_table(target).modules.items():
            table.modules.setdefault(module, {}).update(syms)

    # --- per-test coverage mechanism ------------------------------------------
    raw_coverage: dict[str, CoverageSet] = {}
    langs = sorted(detectors)
    if "python" in detectors:
        cov_note = ""
    elif "javascript" in langs or "typescript" in langs:
        cov_note = ("not run (JS/TS: per-test coverage requires a pre-built "
                    "c8/coverage JSON — set [coverage] jsts_coverage_json; "
                    "coverage-dependent detectors stay LOW-capped)")
    else:
        cov_note = "not run (no supported test files found)"
    coverage_available = False
    if "python" in detectors:
        raw_coverage, coverage_available, cov_note = coverage_runner.run(
            target, cfg)
        if coverage_available:
            run["toolchain"]["pytest_python"] = coverage_runner.resolve_python(cfg)
    # A user-supplied JS/TS coverage document beats the (absent) python run.
    jsts_cov = (cfg.get("coverage", {}) or {}).get("jsts_coverage_json")
    if jsts_cov and ("javascript" in langs or "typescript" in langs):
        cov_path = Path(jsts_cov)
        if not cov_path.is_absolute():     # target-relative, like quarantine/
            cov_path = Path(target) / cov_path
        jsts_raw, ok, jsts_note = detectors["javascript"].load_coverage(
            cov_path)
        if ok:
            raw_coverage.update(jsts_raw)
            coverage_available = True
            cov_note = jsts_note
        elif "python" not in detectors:
            cov_note = jsts_note
    run["toolchain"]["coverage_available"] = coverage_available
    run["toolchain"]["coverage_note"] = cov_note
    result.coverage_available = coverage_available
    coverage = merge_param_coverage(tests, raw_coverage)

    # --- CI history (network only if a network provider resolves) --------------
    history, ci_note, used_network = ci_mod.load_history(
        target, cfg, offline=args.offline)
    run["ci"] = {
        "source": history.provider,
        "runs": history.run_count,
        "details": ci_note,
        "used_network": used_network,
    }
    result.ci = history

    # --- tiering + symbol coverage (feeds the §7 vetoes) ---------------------
    tier_of, _pub_by_id = tiering.assign_tiers(tests, table, coverage, cfg)
    sym_cov = tiering.symbol_coverage(tests, table, coverage)
    sole_covered: dict[str, list[str]] = {}
    for symbol, coverers in sym_cov.items():
        if len(coverers) == 1:
            sole_covered.setdefault(next(iter(coverers)), []).append(symbol)
    sole_covered = {k: sorted(v) for k, v in sorted(sole_covered.items())}

    # --- scoped run filtering (§9) --------------------------------------------
    scope = {"type": "full", "spec": None, "tiers": None}
    if args.path_glob:
        scope = {"type": "path", "spec": args.path_glob, "tiers": None}
        tests = [n for n in tests
                 if fnmatch.fnmatch(n.file, args.path_glob)]
    if args.diff:
        changed = changed_files_from_diff(target, args.diff)
        scope = {"type": "diff", "spec": args.diff, "tiers": None}
        tests = [n for n in tests
                 if n.file in changed or (n.coverage and any(
                     ln.rpartition(":")[0] in changed for ln in n.coverage.lines))]
    if scope["type"] != "full" and not args.all_tiers:
        scope["tiers"] = ["smoke", "core"]
        tests = [n for n in tests if tier_of.get(n.id, n.tier) in {"smoke", "core"}]
    elif args.all_tiers:
        scope["tiers"] = ["smoke", "core", "extended"]
    run["scope"] = scope

    # --- detector context -----------------------------------------------------
    ctx = DetectorContext({
        "coverage": coverage,
        "coverage_available": coverage_available,
        "symbol_table": table,
        "ci": history if history.runs else None,
        "ci_min_runs": 20,
        "calibration": cfg.get("calibration", {}),
        "detector_confidence": cfg.get("detector_confidence", {}),
        "tier_of": tier_of,
        "test_by_id": {n.id: n for n in tests},
        "gating": cfg.get("gating", {}),
        "features": feats,
        "vetoes": cfg.get("vetoes", {}),
        "sole_covered_symbols": sole_covered,
        "symbol_coverage": sym_cov,
    })
    for det in detectors.values():
        if hasattr(det, "set_coverage"):
            det.set_coverage(coverage)

    # --- run category detectors ------------------------------------------------
    findings: dict[tuple[str, str], Finding] = {}
    for category, fn in registry.category_detectors():
        try:
            produced = fn(tests, ctx)
        except Exception as exc:  # a broken detector must not sink the audit
            print(f"[refine-tests] detector {category} failed: {exc}",
                  file=sys.stderr)
            continue
        for f in produced or []:
            key = (f.test_id, f.category)
            if key not in findings or f.confidence > findings[key].confidence:
                findings[key] = f

    # --- mutation probe (P4, opt-in, budgeted) ---------------------------------
    mutation_results: dict[str, Any] = {}
    run["mutation"] = {"note": "disabled (feature flag off, §15 P4)"}
    result.mutation_run = bool(feats.get("mutation", False))
    if feats.get("mutation", False):
        cfg_mut = dict(cfg.get("mutation", {}) or {})
        cfg_mut.setdefault("max_mutants", 5)
        max_tests_cap = int(cfg_mut.get("max_tests_cap", 50000))
        if len(tests) > max_tests_cap:
            run["mutation"] = {"note": (
                f"refused: {len(tests)} tests exceed the §13 hard cap "
                f"({max_tests_cap})")}
        else:
            python = coverage_runner.resolve_python(cfg)
            run["mutation"] = {"note": f"enabled (python={python})"}
            # §2: probe the tests with a live claim to verify.
            from .mutators import run_probe
            for node in sorted(tests, key=lambda n: n.id):
                node_findings = findings_for(findings, node.id)
                if not any(f.confidence >= 0.7 for f in node_findings):
                    continue
                if node.language != "python":
                    continue
                probe = run_probe(target, node.coverage, node.id, None,
                                  {"mutation": cfg_mut},
                                  python=python or "python3")
                mutation_results[node.id] = probe

    # --- decisions ---------------------------------------------------------------
    decisions: list[Decision] = []
    for node in sorted(tests, key=lambda n: n.id):
        node_findings = findings_for(findings, node.id)
        ctx_with_mut = dict(ctx)
        ctx_with_mut["mutation"] = mutation_results
        decisions.append(gate_decide(node, node_findings, ctx_with_mut))

    # --- quarantine redetect (§8) ------------------------------------------------
    store = QuarantineStore(target / (cfg.get("quarantine", {}) or {}).get(
        "root", "quarantine"))
    known = {r.test_id: r for r in store.records()}
    for decision in decisions:
        record = known.get(decision.test_id)
        if record is not None and record.state == "quarantined":
            decision.action = "quarantined"
            decision.quarantine_id = record.id
    result.quarantine = store.records()

    # --- narrative (§10.7) ---------------------------------------------------------
    what = []
    for decision in sorted(decisions, key=lambda d: d.test_id):
        node = ctx["test_by_id"].get(decision.test_id)
        if node is None:
            continue
        if decision.action in {"keep", "strengthen"} and \
                (decision.findings or decision.action == "strengthen"):
            what.append({"test_id": decision.test_id,
                         "bug": what_would_this_catch(node, history)})
    what.sort(key=lambda w: w["test_id"])

    result.run = run
    result.decisions = decisions
    result.summary = {
        "total_tests": len(tests),
        "what_would_this_catch": what,
    }
    result.assumptions = load_assumptions()
    result.nodes = ctx["test_by_id"]
    return result


def findings_for(findings: dict[tuple[str, str], Finding], test_id: str
                 ) -> list[Finding]:
    return [f for (tid, _cat), f in findings.items() if tid == test_id]


def load_assumptions() -> list[str]:
    """Extract the A# assumption headers from ASSUMPTIONS.md (§0).

    The full document is mirrored into the plugin root; the report lists the
    one-line headers so reviewers see the assumption surface at a glance.
    """
    path = PLUGIN_ROOT / "ASSUMPTIONS.md"
    if not path.exists():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("## A") and "—" in s:
            out.append(s.removeprefix("## ").strip())
    return out


# ---------------------------------------------------------------------------
# Cleanup (§8)
# ---------------------------------------------------------------------------

def removals(result: AuditResult) -> list[Decision]:
    return [d for d in result.decisions if d.action == "remove"]


def proposal_targets(result: AuditResult) -> dict[str, list[Decision]]:
    out: dict[str, list[Decision]] = {"refine": [], "strengthen": [],
                                      "replace": []}
    for d in result.decisions:
        if d.action in {"refine", "strengthen", "replace"}:
            out[d.action].append(d)
    return out


def render_plan(result: AuditResult, target: Path, cfg: dict) -> str:
    """The `--dry-run` / missing-`--yes` diff preview (§8, §14.3)."""
    target = Path(target).resolve()
    lines: list[str] = []
    lines.append("refine-tests cleanup plan (DRY RUN — no writes)")
    lines.append("=" * 62)
    lines.append(f"target: {target}")
    lines.append(f"mode:   {result.run.get('mode', 'cleanup')}")
    lines.append("")
    removes = removals(result)
    if removes:
        lines.append(f"## REMOVE → quarantine ({len(removes)})")
        for d in sorted(removes, key=lambda x: x.test_id):
            qid = quarantine_id_for(d.test_id)
            lines.append(f"  WOULD REMOVE `{d.test_id}` → quarantine/{qid}/")
            lines.append(f"    gate evidence: {d.evidence_line}")
            lines.append(f"    restore with: refine-tests restore {qid}")
            diff = stub_diff_for(target, d)
            if diff:
                for ln in diff.splitlines():
                    lines.append(f"    {ln}")
            lines.append("")
    targets = proposal_targets(result)
    for kind in ("refine", "strengthen", "replace"):
        decisions = targets[kind]
        if not decisions:
            continue
        lines.append(f"## {kind.upper()} → proposed/ ({len(decisions)})")
        for d in decisions:
            pid = proposal_mod.proposal_id(d)
            lines.append(f"  WOULD {kind.upper()} `{d.test_id}` → "
                         f"proposed/{pid}.{kind}.patch")
        lines.append("")
    if not removes and not any(targets.values()):
        lines.append("No gated actions; nothing would be written.")
    lines.append("Refused without --yes: every write above (quarantine/, "
                 "proposed/). Re-run with --yes to apply.")
    return "\n".join(lines)


def quarantine_id_for(test_id: str) -> str:
    from .actions.quarantine import make_quarantine_id
    return make_quarantine_id(test_id)


def stub_diff_for(target: Path, decision: Decision) -> str:
    import difflib
    from .actions.quarantine import extract_function, stub_source
    path = target / decision.file
    func = decision.test_id.split("::")[-1].split("[")[0]
    extracted = extract_function(path, func)
    if extracted is None:
        return ""
    original_lines, lineno, end_lineno = extracted
    qid = quarantine_id_for(decision.test_id)
    stub = stub_source(func, qid, len(original_lines))
    file_lines = path.read_text(encoding="utf-8").splitlines(keepends=False)
    new_lines = list(file_lines)
    new_lines[lineno - 1:end_lineno] = stub
    diff = difflib.unified_diff(
        [ln + "\n" for ln in file_lines], [ln + "\n" for ln in new_lines],
        fromfile=f"a/{decision.file}", tofile=f"b/{decision.file}",
        lineterm="")
    return "".join(diff)


def apply_cleanup(result: AuditResult, target: Path, cfg: dict,
                  args: AuditArgs) -> AuditResult:
    """Execute gated actions. Writes only to quarantine/ and proposed/ (§9)."""
    target = Path(target).resolve()
    feats = feature_flags(cfg, args)
    store = QuarantineStore(target / (cfg.get("quarantine", {}) or {}).get(
        "root", "quarantine"))
    proposals_root = target / "proposed"

    removes = removals(result)
    approved: set[str] = set()
    if args.approve_all:
        approved = {d.test_id for d in removes}
    elif args.approve:
        wanted = {a for a in args.approve}
        for d in removes:
            if d.test_id in wanted or quarantine_id_for(d.test_id) in wanted:
                approved.add(d.test_id)
    else:
        # --yes alone is explicit global consent for all gated removals
        # (reconciliation of §8 with §14.4 documented in ASSUMPTIONS A7).
        approved = {d.test_id for d in removes}

    skipped: list[dict[str, str]] = []
    for decision in sorted(removes, key=lambda d: d.test_id):
        if decision.test_id not in approved:
            continue
        # Quarantine mechanics (AST extraction, skip stub, restore) are
        # Python-only today: a JS/TS removal is reported by the audit but
        # must not be rewritten by a Python AST tool — refuse and surface
        # (§0/A3) instead of corrupting the file.
        if not decision.file.endswith(".py"):
            skipped.append({
                "test_id": decision.test_id,
                "file": decision.file,
                "reason": "quarantine not supported for JS/TS yet — remove "
                          "manually (see the audit findings)",
            })
            continue
        record = store.apply_removal(decision, target)
        decision.quarantine_id = record.id

    targets = proposal_targets(result)
    proposals: list[dict] = []
    for kind, decisions in targets.items():
        if kind == "replace" and not args.allow_replace_proposals:
            continue
        if not feats.get(kind, False):
            continue
        for decision in decisions:
            node = result.nodes.get(decision.test_id)
            if node is None:
                continue
            if kind == "refine":
                proposals.append(proposal_mod.refine_proposal(
                    target, node, decision, proposals_root))
            elif kind == "strengthen":
                proposals.append(proposal_mod.strengthen_proposal(
                    target, node, decision, proposals_root))
            else:
                proposals.append(proposal_mod.replace_proposal(
                    target, node, decision, proposals_root))
            decision.proposal_path = proposals[-1]["path"]
    result.proposals = proposals
    result.quarantine = store.records()
    result.run["applied"] = {
        "removed": sorted(approved),
        "proposals": [p["id"] for p in proposals],
        "skipped": skipped,
    }
    return result


def write_reports(result: AuditResult, target: Path, args: AuditArgs
                  ) -> tuple[Path, Path]:
    """Write both §10 reports. Target root by default, `--report-dir` override."""
    report_dir = Path(args.report_dir) if args.report_dir else target
    md_path = report_dir / f"{REPORT_BASE}.md"
    json_path = report_dir / f"{REPORT_BASE}.json"
    emit_markdown(result, md_path)
    emit_json(result, json_path)
    return md_path, json_path
