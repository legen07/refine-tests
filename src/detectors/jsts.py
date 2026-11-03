"""JavaScript / TypeScript detector (§11) — vitest / jest.

Scope (documented in ASSUMPTIONS.md, A3): this detector provides *structural*
support — test enumeration over `describe`/`it`/`test` blocks, a module symbol
table from export declarations, normalized structure digests, expect-assertion
shapes, and mock bindings (`vi.fn`, `jest.fn`, `mockModule`). It deliberately
ships **no JavaScript parser dependency**: without one, per-test coverage
fingerprinting is impossible from source. Per §2, when the per-test coverage
mechanism is unavailable (no vitest/jest per-test coverage JSON supplied via
config `coverage.jsts_coverage_json`), coverage-dependent detectors refuse
findings above LOW confidence. A production deployment that wants full
JS/TS fidelity plugs a tree-sitter-backed detector in here — the core never
asks which language it is talking to.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..models import (
    AssertionInfo,
    CallSiteInfo,
    CoverageSet,
    ImportInfo,
    SymbolInfo,
    SymbolTable,
    TestNode,
)

TEST_FILE_RE = re.compile(r"\.(?:test|spec)\.(?:js|ts|jsx|tsx)$")
IT_RE = re.compile(r"\b(?:it|test)\s*(?:\.\w+)?\s*\(\s*(['\"`])(.*?)\1\s*,")
DESCRIBE_RE = re.compile(r"\bdescribe\s*\(\s*(['\"`])(.*?)\1\s*,")
EXPECT_RE = re.compile(
    r"expect\s*\(\s*([^)]*?)\s*\)\s*(\.\w+(?:\([^)]*\))?)*")
EXPORT_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|const|let|class)\s+(\w+)",
    re.M)
MOCK_RE = re.compile(
    r"\b(?:const|let)\s+(\w+)\s*=\s*(?:vi|jest)\.(?:fn|spyOn|mock)\b")
CALLOUT_RE = re.compile(r"(?:^|\s)(?:vi|jest)\.(?:fn|mock|spyOn)\(")

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "coverage", ".venv"}


def _find_blocks(src: str, name_re) -> list[tuple[str, int, int]]:
    """Locate `name("title", fn)` blocks with brace matching."""
    out: list[tuple[str, int, int]] = []
    for m in name_re.finditer(src):
        # Walk forward to the opening brace of the callback.
        i = m.end()
        while i < len(src) and src[i] != "{":
            if src[i] == ")":
                break  # shorthand or malformed; skip
            i += 1
        if i >= len(src) or src[i] != "{":
            continue
        depth = 0
        start = i
        while i < len(src):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(2), start, i))
                    break
            elif ch in "\"'`":
                i = _skip_string(src, i)
                continue
            i += 1
    return out


def _skip_string(src: str, i: int) -> int:
    quote = src[i]
    i += 1
    while i < len(src) and src[i] != quote:
        if src[i] == "\\":
            i += 1
        i += 1
    return i


def _norm_tokens(text: str) -> str:
    """Word tokens with identifiers anonymized (§5.3)."""
    words = re.findall(r"[A-Za-z_]\w*|\W", text)
    out = []
    for w in words:
        if re.fullmatch(r"[A-Za-z_]\w*", w):
            out.append("ID" if not w[0].isupper() else "Cid")
        else:
            out.append(w)
    return " ".join(out)


class JsTsDetector:
    """Implements the §11 `Detector` protocol for JS/TS (vitest | jest)."""

    name = "jsts-vitest-jest"
    language = "javascript"

    def __init__(self, root: Path, cfg: dict[str, Any] | None = None) -> None:
        self.root = Path(root)
        self.cfg = cfg or {}
        self._coverage: dict[str, CoverageSet] = {}
        cov_path = (self.cfg.get("coverage", {}) or {}).get("jsts_coverage_json")
        if cov_path:
            self._coverage = self._load_per_test_json(Path(cov_path))

    # -- protocol ------------------------------------------------------------

    def parse_ast(self, path):
        return Path(path).read_text(encoding="utf-8")

    def coverage_fingerprint(self, test_id: str) -> CoverageSet:
        return self._coverage.get(test_id, CoverageSet(test_id=test_id))

    def set_coverage(self, coverage: dict[str, CoverageSet]) -> None:
        merged = dict(self._coverage)
        merged.update(coverage)
        self._coverage = merged

    def _load_per_test_json(self, path: Path) -> dict[str, CoverageSet]:
        """vitest `--coverage.reporter=json --per-test` / jest per-test shards."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, CoverageSet] = {}
        results = data.get("result", data.get("results", {}))
        for name, entry in (results.items() if isinstance(results, dict)
                            else []):
            if not isinstance(entry, dict):
                continue
            lines = {f"{k}" for k, v in (entry.get("s", {}) or {}).items()
                     if v > 0}
            out[name] = CoverageSet(test_id=name, lines=lines)
        return out

    # -- enumeration -----------------------------------------------------------

    def enumerate_tests(self, root=None) -> list[TestNode]:
        root = Path(root) if root else self.root
        nodes: list[TestNode] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not TEST_FILE_RE.search(fn):
                    continue
                p = Path(dirpath) / fn
                try:
                    src = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                rel = p.relative_to(root).as_posix()
                nodes.extend(self._tests_from_source(src, rel, root))
        return sorted(nodes, key=lambda n: (n.file, n.lineno, n.id))

    def _tests_from_source(self, src: str, rel: str, root: Path) -> list[TestNode]:
        out: list[TestNode] = []
        describes = _find_blocks(src, DESCRIBE_RE)
        its = _find_blocks(src, IT_RE)
        # Expand describe titles into nodeids.
        for title, start, end in its:
            enclosing = [dt for dt, ds, de in describes
                         if ds < start and de > end]
            prefix = "/".join(enclosing) if enclosing else ""
            nodeid = f"{rel}::{prefix}::{title}" if prefix else f"{rel}::{title}"
            lineno = src.count("\n", 0, start) + 1
            body = src[start + 1:end]
            calls = [
                CallSiteInfo(repr=m.group(0)[:120], callee=m.group(1).strip(),
                             base_name=m.group(1).strip().split(".")[0],
                             lineno=src.count("\n", 0, m.start()) + 1)
                for m in re.finditer(r"\b(\w[\w.]*)\s*\(", body)
            ]
            mocks = {m.group(1): "vi.fn" for m in MOCK_RE.finditer(body)}
            mock_only = bool(CALLOUT_RE.search(body)) and mocks
            assertions: list[AssertionInfo] = []
            for em in EXPECT_RE.finditer(body):
                target = em.group(1).strip()
                matchers = [t for t in re.findall(r"\.(\w+)", em.group(2) or "")]
                a = AssertionInfo()
                a.kind = "compare"
                a.comparator = " ".join(matchers)
                a.detail = f"expect({target}).{em.group(2) or ''}"
                if any(m in {"toBeDefined", "toBeTruthy", "toBeNotNull",
                             "toBeTruthy"} for m in matchers):
                    a.is_existence = True
                if "toMatchSnapshot" in matchers or "toMatchInlineSnapshot" in matchers:
                    a.snapshot_like = True
                if "toHaveBeenCalledWith" in matchers:
                    a.pins_call_order = True
                if target.startswith("_"):
                    a.reads_privates = True
                assertions.append(a)
            out.append(TestNode(
                id=nodeid, file=rel, name=title, language="javascript",
                lineno=lineno, end_lineno=src.count("\n", 0, end) + 1,
                body_line_count=end - start,
                references=set(re.findall(r"\b(\w[\w.]*)\b", body)),
                assertions=assertions, call_sites=calls,
                mock_binding_names=mocks,
                mock_configs=[],
                structure_digest=_norm_tokens(body),
                assertion_skeleton=tuple(
                    "EXPECT:" + a.comparator for a in assertions),
                coverage_key=nodeid,
                imports=self._imports(src),
                markers=["mock-only"] if mock_only else [],
            ))
        return out

    def _imports(self, src: str) -> list[ImportInfo]:
        out: list[ImportInfo] = []
        for m in re.finditer(
                r"^\s*import\s+(?:\{([^}]*)\}|\w+)\s+from\s+['\"]([^'\"]+)['\"]",
                src, re.M):
            named, module = m.group(1), m.group(2)
            if named:
                for sym in re.findall(r"\b(\w+)\b", named):
                    out.append(ImportInfo(module=module, symbol=sym, local=sym))
            else:
                out.append(ImportInfo(module=module, symbol="", local=m.group(0)))
        return out

    # -- symbol table -----------------------------------------------------------

    def symbol_table(self, root=None) -> SymbolTable:
        root = Path(root) if root else self.root
        table = SymbolTable()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not fn.endswith((".js", ".ts", ".jsx", ".tsx")):
                    continue
                if TEST_FILE_RE.search(fn):
                    continue
                p = Path(dirpath) / fn
                try:
                    src = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                rel = p.relative_to(root).with_suffix("").as_posix()
                syms: dict[str, SymbolInfo] = {}
                for name in EXPORT_RE.findall(src):
                    syms[name] = SymbolInfo(
                        name=name, kind="function", module=rel, lineno=0,
                        end_lineno=0, is_public=not name.startswith("_"))
                default_export = re.search(r"export default\s+(\w+)", src)
                if default_export:
                    syms.setdefault("default", SymbolInfo(
                        name="default", kind="function", module=rel,
                        is_public=True))
                table.modules[rel] = syms
        return table

    # -- mutation probe -----------------------------------------------------------

    def run_mutation_probe(self, test_id: str, mutants) -> Any:
        from ..models import MutationResult
        # §13 non-goal: no Stryker integration in v1. Report honestly.
        del test_id, mutants
        return MutationResult(test_id=test_id, over_budget=True)
