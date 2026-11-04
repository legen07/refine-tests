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
import posixpath
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
# A matcher argument that is a plain JS scalar literal. Aggregates (arrays,
# objects) collapse to "agg": the point of the skeleton is to tell
# `.toBe(3)` from `.toBe(2)` (the claims differ), not to diff data shapes.
SCALAR_ARG_RE = re.compile(
    r"^(?:-?\d+(?:\.\d+)?|(?:'[^']*'|\"[^\"]*\"|`[^`]*`)"
    r"|true|false|null|undefined)$")


def _matcher_skeleton(chain: str) -> str:
    """`.toBe(3).strictEqual(2)` -> `toBe:3/strictEqual:2` (literals kept)."""
    parts: list[str] = []
    for mm in re.finditer(r"\.(\w+)(?:\((.*?)\))?", chain or ""):
        name = mm.group(1)
        args = (mm.group(2) or "").strip()
        lit = args if SCALAR_ARG_RE.match(args) else ("agg" if args else "-")
        parts.append(f"{name}:{lit}")
    return "/".join(parts)

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "coverage", ".venv"}


def _find_blocks(src: str, name_re) -> list[tuple[str, int, int]]:
    """Locate `name("title", fn)` blocks with brace matching."""
    out: list[tuple[str, int, int]] = []
    for m in name_re.finditer(src):
        # Walk forward to the opening brace of the callback, skipping the
        # callback's own parameter parens — modern suites write
        # `it("title", (a, b) => { ... })` or `describe("t", function () {`,
        # so a `)` legitimately precedes the body brace. A close paren at
        # depth 0 (the caller closed first) means shorthand or malformed.
        i = m.end()
        depth = 0
        while i < len(src):
            ch = src[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break          # shorthand or malformed; skip
                depth -= 1
            elif ch == "{" and depth == 0:
                break
            elif ch in "\"'`":
                i = _skip_string(src, i)
                continue
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
    """Index just PAST the string literal starting at ``i``.

    Returning the closing quote's index would make the callers' ``continue``
    re-enter the skip and swallow to the next quote (or off the end of the
    file) — the first test body containing a string literal would never
    close its block.
    """
    quote = src[i]
    i += 1
    while i < len(src) and src[i] != quote:
        if src[i] == "\\":
            i += 1
        i += 1
    return i + 1


def _expect_regions(src: str) -> list[tuple[str, str, int, int]]:
    """Every `expect(<target>)<chain>` in a test body.

    `[^)]` regexes cannot handle nested calls (`expect(add(1, 2)).toBe(3)`),
    so this walks balanced parens with string awareness. Returns the target
    expression, the matcher chain, and the chain's `(start, end)` span —
    the span lets the call-site extractor treat matchers (`.toBe(3)`) as
    assertions rather than exercised calls.
    """
    out: list[tuple[str, str, int, int]] = []
    for m in re.finditer(r"\bexpect\s*\(", src):
        i, depth, in_str = m.end(), 1, None
        start = i
        while i < len(src) and depth:
            ch = src[i]
            if in_str:
                if ch == in_str:
                    in_str = None
                elif ch == "\\":
                    i += 1
            elif ch in "\"'`":
                in_str = ch
                i += 1
                continue
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            continue                     # unbalanced; skip this candidate
        target = src[start:i]
        i += 1                           # past the closing paren
        j = i
        in_str = None
        while j < len(src):
            ch = src[j]
            if in_str:
                if ch == in_str:
                    in_str = None
                elif ch == "\\":
                    j += 1
            elif ch in "\"'`":
                in_str = ch
            elif ch in ";\n":
                break
            j += 1
        out.append((target.strip(), src[i:j].strip(), i, j))
    return out


# Harness macros and mock-factory setups are never exercised calls (A11).
HARNESS_MACROS = {"describe", "it", "test", "expect", "xdescribe", "xit",
                  "beforeEach", "beforeAll", "afterEach", "afterAll",
                  "before", "after", "expect"}
MOCK_SETUP_CALL = re.compile(r"^(?:vi|jest)\.(?:fn|mock|spyOn)$")


def _exercised_calls(body: str, mocks: dict[str, str]
                     ) -> list[CallSiteInfo]:
    """Behavioral call sites: what the test drives, with mock resolution.

    Excluded: `expect(...)` targets' matcher chains (they are assertions),
    the harness macros themselves, and `vi.fn(...)` / `jest.fn(...)`
    constructor calls — a mock's construction verifies nothing (A11, the
    same rule the Python detector applies).
    """
    regions = _expect_regions(body)
    out: list[CallSiteInfo] = []
    for m in re.finditer(r"\b(\w[\w.]*)\s*\(", body):
        callee = m.group(1).strip()
        base = callee.split(".")[0]
        if any(cstart <= m.start() < cend
               for _, _, cstart, cend in regions):
            continue                       # inside a matcher chain
        if base in HARNESS_MACROS:
            continue
        if MOCK_SETUP_CALL.match(callee):
            continue
        out.append(CallSiteInfo(
            repr=m.group(0)[:120], callee=callee, base_name=base,
            lineno=body[:m.start()].count("\n") + 1,
            on_mock=base in mocks))
    return out


def _module_key(spec: str, rel: str) -> str:
    """Normalize an import specifier to the symbol-table key form.

    The symbol table keys production modules root-relative without a suffix
    (`src/calc`); a relative specifier is resolved against the importing
    test file's directory. Package specifiers ("vitest", "lodash") are kept
    as written: they are never production modules, and the zombie detector
    skips modules absent from the table.
    """
    if spec.startswith(".") and rel:
        base = posixpath.dirname(rel)
        spec = posixpath.normpath(posixpath.join(base, spec))
    if spec.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        spec = spec.rsplit(".", 1)[0]
    return spec


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
            mocks = {m.group(1): "vi.fn" for m in MOCK_RE.finditer(body)}
            calls = _exercised_calls(body, mocks)
            mock_only = bool(CALLOUT_RE.search(body)) and mocks
            assertions: list[AssertionInfo] = []
            for target, chain, _, _ in _expect_regions(body):
                matchers = re.findall(r"\.(\w+)(?=\s*\()", chain)
                a = AssertionInfo()
                a.kind = "compare"
                a.comparator = " ".join(matchers)
                a.matcher_chain = chain
                a.detail = f"expect({target}){chain}"
                if any(m in {"toBeDefined", "toBeTruthy", "toBeNotNull"}
                       for m in matchers):
                    a.is_existence = True
                if "toMatchSnapshot" in matchers or \
                        "toMatchInlineSnapshot" in matchers:
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
                    # Matcher text with literals kept, mirroring the Python
                    # detector's `== 4` vs `== 5` discrimination.
                    "EXPECT:" + _matcher_skeleton(a.matcher_chain)
                    for a in assertions),
                coverage_key=nodeid,
                imports=self._imports(src, rel),
                markers=["mock-only"] if mock_only else [],
            ))
        return out

    def _imports(self, src: str, rel: str = "") -> list[ImportInfo]:
        out: list[ImportInfo] = []
        for m in re.finditer(
                r"^\s*import\s+(?:\{([^}]*)\}|(\w+))\s+from\s+"
                r"['\"]([^'\"]+)['\"]",
                src, re.M):
            named, bare, module = m.group(1), m.group(2), m.group(3)
            key = _module_key(module, rel)
            if named:
                for sym in re.findall(r"\b(\w+)\b", named):
                    out.append(ImportInfo(module=key, symbol=sym, local=sym))
            else:
                out.append(ImportInfo(module=key, symbol="", local=bare or ""))
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
