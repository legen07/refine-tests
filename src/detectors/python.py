"""Python language detector (§11) for pytest + coverage.py + mutmut-style probes.

Responsibilities:

* `enumerate_tests`  — parse the target's pytest tests into `TestNode`s,
  including parametrize handling (fingerprint per parameter set, §5 note).
* `parse_ast`        — raw AST access (protocol).
* `symbol_table`     — production symbol table (functions, classes, constants,
  members, return annotations) for zombie detection and the sole-coverage veto.
* `coverage_fingerprint` — per-test `CoverageSet` from `pytest --cov-context`.
* `run_mutation_probe` — mutate covered production code in a *sandbox copy* and
  run exactly one test per mutant (§5, P4).

No §5 category logic lives here; category detectors consume only the normalized
structures this module emits.
"""

from __future__ import annotations

import ast
import os
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

MOCK_FACTORIES = {"Mock", "MagicMock", "AsyncMock", "PropertyMock",
                  "create_autospec", "patch", "mock_open"}
MOCK_ASSERT_METHODS = {"assert_called_once_with", "assert_called_with",
                       "assert_any_call", "assert_called", "assert_not_called",
                       "assert_has_calls", "assert_called_once"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
             ".pytest_cache", ".refine-tests-cov", "quarantine", ".ci-history"}
SNAPSHOT_LITERAL_MIN = 5          # ≥5 literal scalar values → snapshot-like
SNAPSHOT_STR_LEN = 60             # … or a single ≥60-char string literal


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def is_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") or name.endswith("_test.py")


def module_path(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    return ".".join(rel.with_suffix("").parts)


def dotted(node: ast.AST) -> str:
    """Resolve an expression to its dotted name (prod.pricing.DISCOUNT)."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def flatten_names(node: ast.AST) -> set[str]:
    """All leaf names and dotted attribute paths read inside `node`."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(dotted(n))
    return names


def private_refs(names: set[str]) -> list[str]:
    out: set[str] = set()
    for n in names:
        for part in n.split("."):
            if part == "__dict__":
                out.add(part)
            elif part.startswith("_") and not (
                part.startswith("__") and part.endswith("__")):
                out.add(part)
    return sorted(out)


def literal_eval_safe(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def is_constant_expr(node: ast.AST) -> bool:
    """True iff the expression evaluates without any runtime value."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(is_constant_expr(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(is_constant_expr(e) for e in node.values if e is not None)
    if isinstance(node, ast.BinOp):
        return is_constant_expr(node.left) and is_constant_expr(node.right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return is_constant_expr(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return is_constant_expr(node.operand)
    if isinstance(node, ast.BoolOp):
        return all(is_constant_expr(v) for v in node.values)
    return False


def _literal_mass(node: ast.AST) -> int:
    """Literal mass of an expression.

    Every scalar constant counts 1; a single string literal of ≥60 chars
    counts 2 (one long string alone is already snapshot-like, §5.10).
    """
    total = 0
    for n in ast.walk(node):
        if isinstance(n, ast.Constant):
            if isinstance(n.value, str) and len(n.value) >= SNAPSHOT_STR_LEN:
                total += 2
            else:
                total += 1
    return total


def comparisons(test: ast.AST) -> list[dict[str, Any]]:
    """Expand `a == b == c` / BoolOp-anded checks into unit comparisons."""
    comps: list[dict[str, Any]] = []
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            comps.extend(comparisons(value))
    if isinstance(test, ast.Compare):
        left = test.left
        for op, right in zip(test.ops, test.comparators):
            comps.append({"left": left, "op": type(op).__name__, "right": right})
            left = right
    return comps


class _DigestNormalizer(ast.NodeTransformer):
    """Strip identifier identity so near-duplicates differ only by renames."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="ID", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = "ID"
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name):
            node.value = ast.copy_location(
                ast.Name(id="ID", ctx=ast.Load()), node.value)
        return node


def structure_tokens(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Token stream of the test body with identifiers anonymized (§5.3)."""
    module = ast.Module(body=list(fn.body), type_ignores=[])
    module = _DigestNormalizer().visit(module)
    ast.fix_missing_locations(module)
    tokens: list[str] = []
    for n in ast.walk(module):
        tokens.append(type(n).__name__)
        if isinstance(n, ast.Compare):
            tokens.append(type(n.ops[0]).__name__)
        elif isinstance(n, ast.Constant):
            tokens.append(f"C:{type(n.value).__name__}")
    return " ".join(tokens)


def assertion_skeleton(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Assertion structure with literals kept, variables abstracted (§5.3).

    Two tests whose skeletons match make the same claims modulo renames.
    """
    out: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            comps = comparisons(node.test)
            if not comps:
                out.append("truthy")
                continue
            for cmp in comps:
                parts = [cmp["op"]]
                for operand in (cmp["left"], cmp["right"]):
                    val = literal_eval_safe(operand)
                    if isinstance(val, bool):
                        parts.append("bool")
                    elif isinstance(val, (int, float, str)):
                        parts.append(repr(val))   # literals are kept
                    elif val is not None and not isinstance(val, type(None)):
                        parts.append(type(val).__name__)
                    else:
                        parts.append("expr")
                out.append(":".join(parts))
    return tuple(out)


_OP_SYMBOL = {"Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">",
              "GtE": ">=", "Is": "is", "IsNot": "is not", "In": "in",
              "NotIn": "not in"}


# ---------------------------------------------------------------------------
# Python detector
# ---------------------------------------------------------------------------

def _is_patched(callee: str, patched_targets: list[str]) -> bool:
    """True when a call target is monkeypatched by a `with patch("<t>")`."""
    if not callee or not patched_targets:
        return False
    return any(p == callee or p.endswith("." + callee) for p in patched_targets)


class PythonDetector:
    """Implements the §11 `Detector` protocol for Python/pytest."""

    name = "python-pytest"
    language = "python"

    def __init__(self, root: Path, cfg: dict[str, Any] | None = None) -> None:
        self.root = Path(root)
        self.cfg = cfg or {}
        self._coverage: dict[str, CoverageSet] = {}

    # -- protocol ------------------------------------------------------------

    def parse_ast(self, path) -> ast.AST:
        return ast.parse(Path(path).read_text(encoding="utf-8"))

    def coverage_fingerprint(self, test_id: str) -> CoverageSet:
        return self._coverage.get(test_id, CoverageSet(test_id=test_id))

    def set_coverage(self, coverage: dict[str, CoverageSet]) -> None:
        self._coverage = coverage

    # -- test enumeration ------------------------------------------------------

    def enumerate_tests(self, root=None) -> list[TestNode]:
        root = Path(root) if root else self.root
        nodes: list[TestNode] = []
        for path in sorted(self._iter_python_files(root, test_only=True),
                           key=lambda p: str(p)):
            try:
                tree = self.parse_ast(path)
            except SyntaxError:
                continue
            nodes.extend(self._tests_from_module(tree, path, root))
        return sorted(nodes, key=lambda n: (n.file, n.lineno, n.id))

    def _iter_python_files(self, root: Path, test_only: bool):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                p = Path(dirpath) / fn
                if not test_only or is_test_file(p):
                    yield p

    def _tests_from_module(self, tree: ast.Module, path: Path,
                           root: Path) -> list[TestNode]:
        rel = path.relative_to(root).as_posix()
        module_imports = self._module_imports(path, tree)
        module_marks = self._module_marks(tree)
        out: list[TestNode] = []
        for node in tree.body:
            out.extend(self._class_or_module_tests(node, rel, root, path,
                                                   module_imports, module_marks))
        return out

    def _class_or_module_tests(self, node: ast.AST, rel: str, root: Path,
                               path: Path, module_imports: list[ImportInfo],
                               module_marks: list[str]) -> list[TestNode]:
        out: list[TestNode] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name.startswith("test"):
            tn = self._test_node(node, rel, path, module_imports)
            for m in module_marks:
                if m not in tn.markers:
                    tn.markers.append(m)
            out.append(tn)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        sub.name.startswith("test"):
                    tn = self._test_node(sub, rel, path, module_imports,
                                         class_node=node)
                    for m in module_marks:
                        if m not in tn.markers:
                            tn.markers.append(m)
                    out.append(tn)
        return out

    def _module_imports(self, path: Path, tree: ast.Module) -> list[ImportInfo]:
        """Module-level imports only.

        Function-level imports (e.g. a zombie test importing a deleted symbol
        inside its body) are local to that test and are attached to its node
        alone by `_test_node` — attributing them to every test in the file
        would produce false positives in the zombie detector.
        """
        imports: list[ImportInfo] = []
        for stmt in tree.body:
            if isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    imports.append(ImportInfo(
                        module=stmt.module or "",
                        symbol=alias.name,
                        local=alias.asname or alias.name))
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    imports.append(ImportInfo(
                        module=alias.name, symbol="",
                        local=alias.asname or alias.name.split(".")[0]))
        # Deduplicate, keep order.
        seen: set[tuple[str, str]] = set()
        unique: list[ImportInfo] = []
        for imp in imports:
            key = (imp.module, imp.symbol)
            if key not in seen:
                seen.add(key)
                unique.append(imp)
        return unique

    def _module_marks(self, tree: ast.Module) -> list[str]:
        marks: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "pytestmark"
                    for t in node.targets):
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Attribute):
                            marks.append(elt.attr)
                elif isinstance(node.value, ast.Attribute):
                    marks.append(node.value.attr)
        return sorted(set(marks))

    def _read_decorators(self, node) -> tuple[list[str], list[str]]:
        markers: list[str] = []
        params: list[str] = []
        for dec in node.decorator_list:
            d = dec.func if isinstance(dec, ast.Call) else dec
            dotted_name = dotted(d)
            name = (dotted_name[len("pytest.mark."):]
                    if dotted_name.startswith("pytest.mark.") else "")
            if name and name != "parametrize" and "." not in name:
                markers.append(name)
            elif name == "parametrize" and isinstance(dec, ast.Call) and \
                    len(dec.args) >= 2:
                argnames = literal_eval_safe(dec.args[0])
                values = literal_eval_safe(dec.args[1])
                if isinstance(argnames, str) and isinstance(values, list):
                    params.extend(self._param_ids(argnames, values))
        return sorted(set(markers)), params

    def _param_ids(self, argnames: str, values: list) -> list[str]:
        ids: list[str] = []
        for v in values:
            if isinstance(v, (list, tuple)):
                ids.append("-".join(self._vpart(x) for x in v))
            else:
                ids.append(self._vpart(v))
        return ids

    def _vpart(self, v: Any) -> str:
        if v is None:
            return "None"
        if isinstance(v, str):
            return v or "empty"
        if isinstance(v, bool):
            return "True" if v else "False"
        return str(v)

    def _test_node(self, node, rel: str, path: Path,
                   module_imports: list[ImportInfo],
                   class_node: ast.ClassDef | None = None) -> TestNode:
        markers, params = self._read_decorators(node)
        # Pass 1 over the function body: bindings (values + mock factories).
        binds: dict[str, Any] = {}
        mock_names: dict[str, str] = {}
        mock_configs: list[tuple[str, str]] = []
        getattr_calls: list[tuple[str, str]] = []
        patched_targets: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                factory = self._mock_factory(sub.value)
                for tgt in sub.targets:
                    if isinstance(tgt, ast.Name):
                        binds[tgt.id] = literal_eval_safe(sub.value)
                        if factory:
                            mock_names[tgt.id] = factory
                    # `m.return_value = <constant>` / side_effect: the test
                    # configures the mock, then (often) asserts exactly that.
                    if isinstance(tgt, ast.Attribute) and \
                            tgt.attr in {"return_value", "side_effect"} and \
                            isinstance(tgt.value, ast.Name) and \
                            tgt.value.id in mock_names and \
                            is_constant_expr(sub.value):
                        mock_configs.append((tgt.value.id, ast.unparse(sub.value)))
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                binds[sub.target.id] = (
                    literal_eval_safe(sub.value) if sub.value else None)
            elif isinstance(sub, ast.Call) and sub.args and \
                    isinstance(sub.func, ast.Name) and sub.func.id == "getattr" and \
                    isinstance(sub.args[0], ast.Name) and \
                    len(sub.args) >= 2 and isinstance(sub.args[1], ast.Constant) and \
                    isinstance(sub.args[1].value, str):
                getattr_calls.append((sub.args[0].id, sub.args[1].value))
            elif isinstance(sub, ast.With):
                for item in sub.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Call) and ctx.args and \
                            isinstance(ctx.args[0], ast.Constant) and \
                            isinstance(ctx.args[0].value, str) and \
                            (dotted(ctx.func) or "").split(".")[-1] == "patch":
                        patched_targets.append(ctx.args[0].value)
                        # `with patch(...) as m` binds a mock: record it so
                        # `m.return_value = …`, `m.assert_called_once()…` and
                        # calls through `m` count as mock interactions.
                        if isinstance(item.optional_vars, ast.Name):
                            mock_names[item.optional_vars.id] = "patch"
        # Mock bindings from a unittest setUp-style method on the same class.
        if class_node is not None:
            for sub in class_node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in {"setUp", "setup_method"}:
                    for inner in ast.walk(sub):
                        if isinstance(inner, ast.Assign):
                            factory = self._mock_factory(inner.value)
                            for tgt in inner.targets:
                                name = None
                                if isinstance(tgt, ast.Attribute):
                                    name = tgt.attr
                                elif isinstance(tgt, ast.Name):
                                    name = tgt.id
                                if name and factory:
                                    mock_names[name] = factory
        # Function-local imports (e.g. a zombie test importing a deleted
        # symbol inside its body) belong to this test alone.
        local_imports: list[ImportInfo] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom):
                for alias in sub.names:
                    local_imports.append(ImportInfo(
                        module=sub.module or "",
                        symbol=alias.name,
                        local=alias.asname or alias.name))
            elif isinstance(sub, ast.Import):
                for alias in sub.names:
                    local_imports.append(ImportInfo(
                        module=alias.name, symbol="",
                        local=alias.asname or alias.name.split(".")[0]))
        seen: set[tuple[str, str]] = set()
        merged: list[ImportInfo] = []
        for imp in module_imports + local_imports:
            key = (imp.module, imp.symbol)
            if key not in seen:
                seen.add(key)
                merged.append(imp)

        # Pass 2: call sites and assertions.
        calls: list[CallSiteInfo] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                callee = dotted(sub.func) or "<expr>"
                # Mock-factory construction (`Mock()`, `MagicMock()`) and
                # `patch(...)` are mock *setup*, not exercised behavior:
                # counting them would dilute the mock-call fraction (§5.7).
                leaf = callee.split(".")[-1]
                if leaf in MOCK_FACTORIES:
                    continue
                if leaf == "patch" and sub.args and \
                        isinstance(sub.args[0], ast.Constant) and \
                        isinstance(sub.args[0].value, str):
                    continue
                calls.append(CallSiteInfo(
                    repr=ast.unparse(sub)[:120],
                    callee=callee,
                    base_name=callee.split(".")[0],
                    on_mock=callee.split(".")[0] in mock_names or
                    _is_patched(callee, patched_targets),
                    lineno=sub.lineno,
                ))
        assertions = self._assertions(node, binds, mock_names)
        return TestNode(
            id=f"{rel}::{node.name}",
            file=rel,
            name=node.name,
            language="python",
            lineno=node.lineno,
            end_lineno=node.end_lineno or node.lineno,
            body_line_count=(node.end_lineno or node.lineno) - node.lineno + 1,
            markers=markers,
            params=params,
            imports=merged,
            references=flatten_names(node),
            assertions=assertions,
            call_sites=calls,
            mock_binding_names=mock_names,
            mock_configs=mock_configs,
            patched_targets=patched_targets,
            getattr_calls=getattr_calls,
            structure_digest=structure_tokens(node),
            assertion_skeleton=assertion_skeleton(node),
            coverage_key=f"{rel}::{node.name}",
        )

    def _mock_factory(self, value: ast.AST) -> str | None:
        if isinstance(value, ast.Call):
            callee = dotted(value.func) or ""
            base = callee.split(".")[-1]
            if base in MOCK_FACTORIES:
                return base
            if "mock" in callee.lower():
                return callee
        return None

    # -- assertion normalization ------------------------------------------------

    def _assertions(self, node, binds: dict[str, Any],
                    mock_names: dict[str, str]) -> list[AssertionInfo]:
        out: list[AssertionInfo] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assert):
                out.append(self._one_assertion(sub, binds, mock_names))
        return out

    def _one_assertion(self, sub: ast.Assert, binds: dict[str, Any],
                       mock_names: dict[str, str]) -> AssertionInfo:
        test = sub.test
        info = AssertionInfo()
        names = flatten_names(test)
        info.referenced = sorted(names)
        info.reads_privates = bool(private_refs(names))

        if isinstance(test, (ast.BoolOp, ast.Compare)):
            self._fill_compare(test, info, binds, mock_names)
        else:
            self._fill_truthy(test, info, binds, mock_names)

        if info.snapshot_like and not info.pins_call_order:
            info.kind = "compare"
        return info

    def _fill_truthy(self, test: ast.AST, info: AssertionInfo,
                     binds: dict[str, Any],
                     mock_names: dict[str, str]) -> None:
        operand = test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            info.negated = True
            operand = test.operand
        info.kind = "truthy"
        info.is_constant = is_constant_expr(operand)
        info.comparator = ast.unparse(operand)
        info.detail = f"assert {ast.unparse(test)}"
        # `assert m.assert_called_once()` is a mock interaction, not an
        # existence check: the mock-assert methods are owned by the mock and
        # brittle/mock-heavy detectors, not by existence-only.
        mock_assert = False
        if isinstance(operand, ast.Call):
            callee = dotted(operand.func) or ""
            base = callee.split(".")[0]
            info.on_mock = base in mock_names
            leaf = callee.split(".")[-1]
            if leaf == "isinstance" or leaf.endswith(".isinstance"):
                info.is_typecheck = True
                info.kind = "isinstance"
                # Store the asserted type itself (the isinstance 2nd arg) as
                # the comparator so downstream detectors can match it against
                # return annotations.
                if len(operand.args) >= 2:
                    info.comparator = ast.unparse(operand.args[1])
            if leaf in MOCK_ASSERT_METHODS and base in mock_names:
                mock_assert = True
                info.pins_call_order = True
        elif isinstance(operand, ast.Name):
            # `assert x` where the test itself just set x to a truthy constant
            if operand.id in binds and binds[operand.id]:
                info.self_assigned = is_constant_expr(operand)
                if info.self_assigned:
                    info.is_constant = True
        if not info.is_constant and not info.is_typecheck and not mock_assert:
            info.is_existence = True

    def _fill_compare(self, test: ast.AST, info: AssertionInfo,
                      binds: dict[str, Any],
                      mock_names: dict[str, str]) -> None:
        comps = comparisons(test)
        info.kind = "compare"
        detail_parts: list[str] = []
        all_const = True
        any_left_call = None
        for cmp in comps:
            left, op, right = cmp["left"], cmp["op"], cmp["right"]
            detail_parts.append(f"{_OP_SYMBOL.get(op, op)} {ast.unparse(right)}")
            for operand in (left, right):
                if not is_constant_expr(operand):
                    all_const = False
                if _literal_mass(operand) >= SNAPSHOT_LITERAL_MIN:
                    info.snapshot_like = True
                if isinstance(operand, ast.Attribute) and operand.attr in {
                        "mock_calls", "call_args_list", "call_args"}:
                    info.pins_call_order = True
                if isinstance(operand, ast.Attribute) and operand.attr in {
                        "assert_called_once_with", "assert_called_with",
                        "assert_any_call", "assert_has_calls"}:
                    info.pins_call_order = True
            if isinstance(left, ast.Call):
                any_left_call = dotted(left.func) or ""
                leaf = any_left_call.split(".")[-1]
                if leaf == "isinstance":
                    info.is_typecheck = True
                    info.kind = "isinstance"
            if op in {"Is", "IsNot"} and isinstance(right, ast.Constant) and \
                    right.value is None:
                info.is_existence = True
            if op == "In":
                # `assert k in coll` is an existence check, but an exact log
                # string assertion (`assert "e=v" in caplog.text`) is content
                # matching — the brittle detector owns that claim.
                reads_caplog = any(
                    ref == "caplog" or ref.startswith("caplog.")
                    for ref in info.referenced)
                info.is_existence = not reads_caplog
        info.comparator = ", ".join(detail_parts)
        info.detail = f"assert {ast.unparse(test)}"
        if all_const:
            info.is_constant = True
        info.on_mock = bool(any_left_call and
                            any_left_call.split(".")[0] in mock_names)
        # `x = 5; assert x == 5` — asserts a value the test itself just set.
        if isinstance(test, ast.Compare) and len(test.comparators) == 1 and \
                isinstance(test.left, ast.Name):
            want = literal_eval_safe(test.comparators[0])
            if want is not None and binds.get(test.left.id) == want:
                info.self_assigned = True

    # -- symbol table -----------------------------------------------------------

    def symbol_table(self, root=None) -> SymbolTable:
        root = Path(root) if root else self.root
        table = SymbolTable()
        for p in sorted(self._iter_python_files(root, test_only=False),
                        key=lambda x: str(x)):
            if p.name == "conftest.py":
                continue
            try:
                tree = self.parse_ast(p)
            except SyntaxError:
                continue
            module = module_path(root, p)
            table.modules[module] = self._module_symbols(tree, module)
        return table

    def _module_symbols(self, tree: ast.Module, module: str) -> dict[str, SymbolInfo]:
        syms: dict[str, SymbolInfo] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ret = ""
                if node.returns is not None:
                    try:
                        ret = ast.unparse(node.returns)
                    except Exception:
                        ret = ""
                syms.setdefault(node.name, SymbolInfo(
                    name=node.name, kind="function", module=module,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno or node.lineno,
                    is_public=not node.name.startswith("_"),
                    return_annotation=ret))
            elif isinstance(node, ast.ClassDef):
                members: dict[str, SymbolInfo] = {}
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members[sub.name] = SymbolInfo(
                            name=sub.name, kind="method", module=module,
                            lineno=sub.lineno,
                            end_lineno=sub.end_lineno or sub.lineno,
                            is_public=not sub.name.startswith("_"))
                syms.setdefault(node.name, SymbolInfo(
                    name=node.name, kind="class", module=module,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno or node.lineno,
                    is_public=not node.name.startswith("_"),
                    members=members))
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id not in syms:
                        syms[tgt.id] = SymbolInfo(
                            name=tgt.id, kind="constant", module=module,
                            lineno=node.lineno, end_lineno=node.lineno,
                            is_public=not tgt.id.startswith("_"))
        return syms

    # -- mutation probe (§5, P4) -------------------------------------------------

    def run_mutation_probe(self, test_id: str, mutants) -> Any:
        from .mutators import run_probe  # local import: P4 stays optional
        return run_probe(self.root, self._coverage.get(test_id), test_id,
                         mutants, self.cfg)
