"""Unit tests for the Python detector's structural normalization (§11)."""

import ast

from src.detectors import _util
from src.detectors.python import (
    PythonDetector,
    assertion_skeleton,
    is_constant_expr,
    structure_tokens,
)
from src.models import ImportInfo, SymbolTable, TestNode


def _node(source: str, tmp_path=None) -> TestNode:
    tree = ast.parse(source)
    return PythonDetector(tmp_path or PathLike())._test_node(
        tree.body[-1], "test_x.py", PathLike(), [], None)


class PathLike:
    """pathlib stand-in: only read_text is used by _test_node."""

    def __init__(self, text: str = ""):
        self._text = text

    def read_text(self, encoding="utf-8"):
        return self._text


def test_is_constant_expr_handles_composite_constants():
    assert is_constant_expr(ast.parse("2 + 2", mode="eval").body)
    assert is_constant_expr(ast.parse("(1, 'a', None)", mode="eval").body)
    assert is_constant_expr(ast.parse("{'a': [1, 2]}", mode="eval").body)
    assert not is_constant_expr(ast.parse("x + 1", mode="eval").body)
    assert not is_constant_expr(ast.parse("f(1)", mode="eval").body)


def test_structure_tokens_anonymize_identifiers():
    # The digest is the *shape* of the test: identifiers are stripped and
    # constants carry only their kind, so renames are invisible while
    # structural differences are not.
    a = structure_tokens(ast.parse("result = calc.add(1, 2)"))
    b = structure_tokens(ast.parse("value = helper.add(3, 4)"))
    assert a == b
    c = structure_tokens(ast.parse("value = helper.add('a', 'b')"))
    assert a != c                              # constant kinds differ


def test_assertion_skeleton_keeps_literals_abstracts_variables():
    skel = assertion_skeleton(ast.parse("assert out == 'total: 30'"))
    assert skel == ("Eq:expr:'total: 30'",)
    skel2 = assertion_skeleton(ast.parse("assert out == 'total: 31'"))
    assert skel != skel2                       # literal values are kept


def test_alias_map_binds_from_imports():
    node = TestNode(id="t", file="t.py", imports=[
        ImportInfo(module="prod", symbol="calc", local="calc"),
        ImportInfo(module="prod.pricing", symbol="price_order",
                   local="price"),
    ])
    alias = _util.alias_map(node)
    assert alias == {"calc": "prod.calc", "price": "prod.pricing.price_order"}


def test_resolve_finds_production_symbols():
    table = SymbolTable()
    table.modules["prod.calc"] = {"add": object(), "divide": object()}
    alias = {"calc": "prod.calc"}
    assert _util.resolve(alias, table, "calc.add") == ("prod.calc", "add")
    # Non-production references resolve to None.
    assert _util.resolve(alias, table, "pytest.raises") is None


def test_patch_target_is_a_mock_call(tmp_path):
    source = (
        "from unittest.mock import patch\n"
        "from prod import pricing\n\n\n"
        "def test_patched():\n"
        "    with patch('prod.pricing.price_order') as m:\n"
        "        m.return_value = 90.0\n"
        "        assert pricing.price_order([100]) == 90.0\n"
    )
    path = tmp_path / "test_patched.py"
    path.write_text(source, encoding="utf-8")
    node = PythonDetector(tmp_path)._test_node(
        ast.parse(source).body[-1], "test_patched.py", path, [], None)
    assert node.patched_targets == ["prod.pricing.price_order"]
    assert node.mock_binding_names == {"m": "patch"}
    calls = {c.callee: c.on_mock for c in node.call_sites}
    assert calls["pricing.price_order"] is True   # patched → exercised mock
    assert "m.return_value" not in calls
    # The patch() setup call itself is excluded from the denominator.
    assert "patch" not in calls
