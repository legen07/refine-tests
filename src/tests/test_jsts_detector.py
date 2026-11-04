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
          'it("t3", function () { g(); });\n'
    blocks = _find_blocks(src, IT_RE)
    assert [b[0] for b in blocks] == ["t1", "t2", "t3"]


def test_find_blocks_survives_string_literals_in_body():
    src = ('it("with strings", () => {\n'
           '  expect(() => f("a } b")).toThrow("boom }");\n'
           '});\n')
    title, start, end = _find_blocks(src, IT_RE)[0]
    assert title == "with strings"
    # The span covers exactly the callback body, not the title clause.
    assert src[start] == "{"
    assert src[end] == "}"
    assert "toThrow" in src[start:end]


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
