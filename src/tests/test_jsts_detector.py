"""Unit tests for the JS/TS scanner primitives (src/detectors/jsts.py).

These exist because the whole path was once silently broken — arrow
callbacks (`it("t", () => {})`) and string literals in test bodies both
defeated the scanners, and the Python fixtures could not see it. They pin
the primitives directly; the end-to-end behavior is pinned by
fixtures-jsts/EXPECTED.json in run_acceptance.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.detectors.jsts import (  # noqa: E402
    JsTsDetector, _expect_regions, _find_blocks, _matcher_skeleton, IT_RE)


def test_find_blocks_handles_arrow_callbacks():
    src = 'it("t1", () => { expect(1).toBe(1); });\n' \
          'it("t2", (a, b) => { f(a); });\n' \
          'it("t3", function () { g(); });\n' \
          'test("t4", () => expect(1).toBe(1));\n' \
          'test.each([1, 2])("t5", (n) => { f(n); });\n'
    blocks = _find_blocks(src, IT_RE)
    assert [b[0] for b in blocks] == ["t1", "t2", "t3", "t4", "t5"]


def test_find_blocks_survives_string_literals_in_body():
    src = ('it("with strings", () => {\n'
           '  expect(() => f("a } b")).toThrow("boom }");\n'
           '});\n')
    title, header, start, end = _find_blocks(src, IT_RE)[0]
    assert title == "with strings"
    # The span covers exactly the callback body, not the title clause.
    assert src[start - 1] == "{"
    assert src[end] == "}"
    assert "toThrow" in src[start:end]
    # The line number anchors on the `it(...)` header, not the body start.
    assert src.count("\n", 0, header) == 0


def test_expect_regions_nested_target_and_chain_span():
    regions = _expect_regions("expect(add(1, 2)).toBe(3);\nx")
    assert regions == [("add(1, 2)", ".toBe(3)", 17, 25)]
    assert "expect(add(1, 2))".find(".toBe") == -1  # sanity: chain excluded


def test_expect_regions_ignores_unbalanced():
    assert _expect_regions("expect(x") == []


def test_matcher_skeleton_keeps_scalar_literals():
    assert _matcher_skeleton(".toBe(3)") == "toBe:3"
    assert _matcher_skeleton(".toBe(2)") == "toBe:2"
    assert _matcher_skeleton('.strictEqual("x")') == 'strictEqual:"x"'
    assert _matcher_skeleton(".toEqual({a: 1})") == "toEqual:agg"
    assert _matcher_skeleton(".toBeDefined()") == "toBeDefined:-"
    assert _matcher_skeleton("") == ""


def test_import_specifiers_resolve_to_symbol_table_keys(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.js").write_text("export function add() {}\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "calc.test.js").write_text(
        'import { describe, it, expect } from "vitest";\n'
        'import { add } from "../src/calc.js";\n'
        'import legacy from "../src/legacy";\n'
        'it("t", () => { expect(add()).toBe(0); });\n')
    d = JsTsDetector(tmp_path)
    nodes = d.enumerate_tests(tmp_path)
    modules = {i.module for i in nodes[0].imports}
    # Package specifier untouched; relative ones resolved and suffix-stripped
    # to exactly the keys the symbol table uses.
    assert "vitest" in modules
    assert "src/calc" in modules
    table = d.symbol_table(tmp_path)
    assert "add" in table.modules["src/calc"]


def test_exercised_calls_exclude_macros_and_matchers():
    from src.detectors.jsts import _exercised_calls
    body = ("const add = vi.fn(() => 4);\n"
            "expect(add(1, 2)).toBe(4);\n"
            "expect(add).toHaveBeenCalled();\n")
    calls = _exercised_calls(body, {"add": "vi.fn"})
    assert len(calls) == 1
    assert calls[0].callee == "add" and calls[0].on_mock is True


def test_strip_comments_preserves_length_and_strings():
    from src.detectors.jsts import _strip_comments
    src = ('const a = 1; // the { map below\n'
           'const re = /\\{.*\\}/;\n'
           'toMatch(/^https?:\\/\\//);\n'
           '/* block } */ b = 2;\n'
           'const x = y / z;\n'
           's = "a } b";\n')
    out = _strip_comments(src)
    assert len(out) == len(src)
    assert "// the" not in out and "block }" not in out  # comments blanked
    assert "a } b" in out           # string contents untouched
    assert "y / z" in out           # division is not a comment
    assert out.count("{") == 0      # the regex quantifier braces blanked


def test_enumeration_survives_unbalanced_comment_braces(tmp_path):
    """A `{` in a comment must not swallow following tests' spans."""
    (tmp_path / "t.spec.ts").write_text(
        'test("first", () => {\n'
        '  // the { unbalanced comment\n'
        '  expect(1).toBe(1);\n'
        '});\n'
        'test("second", () => {\n'
        '  expect(/\\{2\\}/.test("x")).toBe(false);\n'
        '});\n'
        'test("third", () => expect(3).toBe(3));\n', encoding="utf-8")
    d = JsTsDetector(tmp_path)
    nodes = d.enumerate_tests(tmp_path)
    assert [n.id for n in nodes] == ["t.spec.ts::first",
                                     "t.spec.ts::second",
                                     "t.spec.ts::third"]
    first, second, third = nodes
    # "first"'s body stops at its own close, not at a later test's.
    assert second.lineno > first.end_lineno
    assert third.body_line_count >= 1


def test_load_coverage_accepts_garbage(tmp_path):
    d = JsTsDetector(tmp_path)
    bad = tmp_path / "nope.json"
    cov, ok, note = d.load_coverage(bad)
    assert ok is False and cov == {}
    assert "jsts_coverage_json" in note or "contexts" in note


def test_load_coverage_nodeid_format(tmp_path):
    """The document's contract: test node ids -> {'s': {'file:line': hits}}."""
    import json
    doc = {"results": {
        "tests/a.test.js::c::alpha": {"s": {"src/calc.js:1": 1}},
    }}
    p = tmp_path / "cov.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    d = JsTsDetector(tmp_path)
    cov, ok, note = d.load_coverage(p)
    assert ok and "src/calc.js:1" in cov["tests/a.test.js::c::alpha"].lines


def test_malformed_coverage_entries_never_crash_tiering(tmp_path):
    """User-supplied coverage data must degrade, not raise (§0)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src import tiering
    from src.models import CoverageSet, SymbolTable, TestNode

    node = TestNode(id="t::x", file="t", name="x", coverage_key="t::x")
    bad = CoverageSet(test_id="t::x", lines={"", "1", "no:line",
                                             "a.js:notanumber"})
    tiers, pub = tiering.assign_tiers([node], SymbolTable(), {"t::x": bad}, {})
    assert tiers["t::x"] == "extended" and pub["t::x"] == []
    cov = tiering.symbol_coverage([node], SymbolTable(), {"t::x": bad})
    assert cov == {}
