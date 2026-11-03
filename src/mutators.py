"""Mutation-style verification for "cannot fail" claims (§5, §15 P4).

Design:

* Mutants are sampled **uniformly but deterministically** (seed = hash of the
  test id) from the production lines the test covers — so repeated runs are
  byte-identical (§14.6).
* Each mutant runs in a **sandbox copy** of the target tree (audit mode never
  mutates the audited source, §9).
* Budgets (§2): max_mutants per test and a wall-clock cap; over budget →
  degrade to static heuristics and mark over_budget (§2).
* A mutant is *killed* when the test fails; it *survives* when the test still
  passes. Collection errors are inconclusive (the test itself is broken) and
  are excluded from the score.

Mutation operators (classic mutmut-style):
  1. comparison flip        `a == b` → `a != b`, `<` → `>=`
  2. numeric constant tweak `n` → `n + 1`
  3. boolean constant flip  `True` ↔ `False`
"""

from __future__ import annotations

import ast
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .models import CoverageSet, Mutant, MutationResult

MUTATOR_KINDS = ("compare-flip", "constant-tweak", "bool-flip")
SKIP_ON_COPY = {"__pycache__", ".pytest_cache", ".refine-tests-cov",
                ".git", ".ci-history", "node_modules", "quarantine"}


class _Mutator(ast.NodeTransformer):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.points: list[tuple[int, str]] = []

    def _record(self, node: ast.AST, desc: str) -> None:
        self.points.append((node.lineno, desc))

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if self.kind == "compare-flip" and node.ops:
            flipped = _flip_compare(node.ops[0])
            if flipped is not None:
                node.ops = [flipped] + list(node.ops[1:])
                self._record(node, f"comparison {ast.unparse(node)[:48]}")
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        self.generic_visit(node)
        if self.kind == "constant-tweak" and isinstance(node.value, int) and \
                not isinstance(node.value, bool):
            node.value = node.value + 1
            self._record(node, f"int constant -> {node.value}")
        elif self.kind == "bool-flip" and isinstance(node.value, bool):
            node.value = not node.value
            self._record(node, f"bool -> {node.value}")
        return node


def _flip_compare(op: ast.cmpop) -> ast.cmpop | None:
    return {
        ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
        ast.Lt: ast.GtE, ast.LtE: ast.Gt,
        ast.Gt: ast.LtE, ast.GtE: ast.Lt,
    }.get(type(op))() if type(op) in {
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE} else None


def collect_mutation_points(root: Path, cov: CoverageSet | None,
                            kinds: tuple[str, ...] = MUTATOR_KINDS
                            ) -> list[tuple[str, str, int, str]]:
    """(module_path, kind, lineno, description) candidates on covered lines."""
    if cov is None or not cov.lines:
        return []
    points: list[tuple[str, str, int, str]] = []
    covered: dict[str, set[int]] = {}
    for entry in cov.lines:
        file, _, lineno = entry.rpartition(":")
        covered.setdefault(file, set()).add(int(lineno))
    for rel, lines in sorted(covered.items()):
        path = Path(root) / rel
        if not path.exists() or not path.name.endswith(".py"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for kind in kinds:
            mutator = _Mutator(kind)
            mutator.visit(ast.parse(source))
            for lineno, desc in mutator.points:
                if lineno in lines:
                    points.append((rel, kind, lineno, desc))
    return points


def _run_one_test(python: str, sandbox: Path, root: Path,
                  test_id: str, timeout: int) -> tuple[str, str]:
    """Run exactly one test in the sandbox; return (outcome, summary)."""
    args = test_id.split("::")
    pytest_target = args[0]
    for extra in args[1:]:
        pytest_target += "::" + extra
    try:
        proc = subprocess.run(
            [python, "-m", "pytest", pytest_target,
             "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=str(sandbox), capture_output=True, text=True, timeout=timeout,
            check=False)
    except subprocess.TimeoutExpired:
        return "timeout", "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", str(exc)
    summary = (proc.stdout or proc.stderr or "").strip().splitlines()
    tail = next((ln for ln in reversed(summary) if
                 ln.startswith("=")), "")
    if "error" in tail.lower() and "failed" not in tail.lower():
        return "error", tail
    if "failed" in tail.lower():
        return "failed", tail
    return "passed", tail


def run_probe(root: Path, cov: CoverageSet | None, test_id: str,
              mutants: list | None, cfg: dict[str, Any],
              python: str = "python3") -> MutationResult:
    """Run the mutation probe for one test (§2 budgets apply)."""
    mcfg = (cfg.get("mutation", {}) or {})
    max_mutants = int(mcfg.get("max_mutants", 5))
    budget_s = int(mcfg.get("max_wall_clock_min", 30)) * 60
    timeout = int(mcfg.get("per_mutant_timeout", 30))

    points = collect_mutation_points(Path(root), cov)
    if not points:
        return MutationResult(test_id=test_id, mutants=[], score=0.0,
                              over_budget=False)
    # Deterministic uniform sampling (§2): seed from the test id.
    rng = random.Random(_stable_seed(test_id))
    if len(points) > max_mutants:
        points = sorted(rng.sample(points, max_mutants),
                        key=lambda p: (p[0], p[2]))
    else:
        points = sorted(points, key=lambda p: (p[0], p[2]))

    started = time.monotonic()
    sandbox = Path(tempfile.mkdtemp(prefix="refine-tests-mut-"))
    try:
        shutil.copytree(
            Path(root), sandbox, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*SKIP_ON_COPY))
        chosen: list[Mutant] = [
            Mutant(id=f"m{i}", module=rel, lineno=lineno, kind=kind,
                   description=f"[{kind}] {desc}")
            for i, (rel, kind, lineno, desc) in enumerate(points)]
        killed = survived = errors = 0
        for mutant in chosen:
            if time.monotonic() - started > budget_s:
                return MutationResult(
                    test_id=test_id, mutants=chosen, killed=killed,
                    survived=survived, score=_score(killed, survived),
                    over_budget=True)
            _apply_mutant(sandbox, mutant)
            outcome, _ = _run_one_test(python, sandbox, Path(root), test_id,
                                       timeout)
            if outcome == "failed":
                killed += 1
            elif outcome == "passed":
                survived += 1
            else:
                errors += 1
        return MutationResult(
            test_id=test_id, mutants=chosen, killed=killed, survived=survived,
            score=_score(killed, survived), over_budget=False)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _score(killed: int, survived: int) -> float:
    total = killed + survived
    if total == 0:
        return 0.0            # inconclusive: no runnable verdicts
    return round(killed / total, 3)


def _apply_mutant(sandbox: Path, mutant: Mutant) -> None:
    path = sandbox / mutant.module
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return

    class _Single(ast.NodeTransformer):
        """Apply exactly one mutation, at mutant.lineno."""

        def __init__(self) -> None:
            self.done = False

        def visit_Compare(self, node: ast.Compare) -> ast.AST:
            self.generic_visit(node)
            if mutant.kind == "compare-flip" and not self.done and \
                    node.lineno == mutant.lineno and node.ops:
                flipped = _flip_compare(node.ops[0])
                if flipped is not None:
                    node.ops = [flipped] + list(node.ops[1:])
                    self.done = True
            return node

        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            self.generic_visit(node)
            if not self.done and node.lineno == mutant.lineno:
                if mutant.kind == "constant-tweak" and \
                        isinstance(node.value, int) and \
                        not isinstance(node.value, bool):
                    node.value = node.value + 1
                    self.done = True
                elif mutant.kind == "bool-flip" and \
                        isinstance(node.value, bool):
                    node.value = not node.value
                    self.done = True
            return node

    tree = _Single().visit(tree)
    ast.fix_missing_locations(tree)
    path.write_text(ast.unparse(tree), encoding="utf-8")


def _stable_seed(text: str) -> int:
    """Stable across processes (Python's hash() is randomized per run)."""
    import hashlib
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)
