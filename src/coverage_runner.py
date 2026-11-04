"""Per-test coverage mechanism (§2: pytest + coverage.py).

Runs the suite under pytest-cov with dynamic per-test contexts, keeps the
raw ``.coverage`` data file (``COVERAGE_FILE``), and materializes it as a
``coverage json --show-contexts`` document, whose per-file ``contexts`` map
is::

    {"prod/calc.py": {"2": ["tests/test_a.py::test_a|run"], ...}}

Context strings are ``<test file>::<test name>|run`` — the trailing ``|run``
is stripped, and module-level lines (empty context) are not attributed to any
test. Paths in the data file are relative to the target root because both
pytest and ``coverage json`` run with ``cwd=target``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .models import CoverageSet

COV_DIR_NAME = ".refine-tests-cov"
CONTEXT_SUFFIX = "|run"


def resolve_python(cfg: dict) -> str | None:
    """Resolve a Python interpreter that has pytest + coverage.py.

    Preference: the plugin's own ``.venv`` (zero-install contract), then the
    running interpreter (it may be inside an adequately provisioned venv),
    then a bare ``python3``/``python``. Returns ``None`` when nothing usable
    exists.
    """
    here = Path(__file__).resolve().parent.parent
    # An explicit `[coverage] python_runner` (or legacy `python`) in
    # config.toml / .refine-tests.toml wins — e.g. the audited project's own
    # venv, so its test dependencies are importable during collection.
    section = (cfg.get("coverage", {}) or {})
    configured = section.get("python_runner") or section.get("python")
    candidates = []
    if configured and configured != "auto":
        candidates.append(str(configured))
    candidates.append(here / ".venv" / "bin" / "python")
    if sys.executable:
        candidates.append(sys.executable)
    candidates += ["python3", "python"]
    for candidate in candidates:
        try:
            proc = subprocess.run(
                [str(candidate), "--version"], capture_output=True,
                text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        probe = subprocess.run(
            [str(candidate), "-c",
             "import pytest, coverage, pytest_cov"],
            capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return str(candidate)
    return None


def run(target: Path, cfg: dict) -> tuple[dict[str, CoverageSet], bool, str]:
    """Run per-test coverage for ``target``.

    Returns ``(per-test coverage map, available, note)``. Never raises: a
    coverage failure degrades to "unavailable" and the note explains why
    (§0: surface, never invent).
    """
    target = Path(target).resolve()
    section = cfg.get("coverage", {}) or {}
    timeout = int(section.get("timeout", 600))
    python = resolve_python(cfg)
    if not python:
        return {}, False, (
            "no python with pytest+coverage found (looked for .venv, then "
            "the running interpreter; install with `python -m venv .venv "
            "&& .venv/bin/pip install pytest pytest-cov`)")

    cov_dir = target / COV_DIR_NAME
    cov_dir.mkdir(exist_ok=True)
    data_file = cov_dir / ".coverage"
    json_path = cov_dir / "coverage.json"

    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(data_file)
    # Deterministic pytest behavior regardless of the audited project's own
    # configuration: no cache provider (state must not leak between runs).
    env.setdefault("PYTEST_ADDOPTS", "-p no:cacheprovider")

    pytest_cmd = [
        python, "-m", "pytest",
        str(target),
        # Force the rootdir to the target so node ids (and therefore coverage
        # contexts) are target-relative, and so an ancestor pyproject.toml of
        # the audited project cannot hijack collection/config discovery.
        f"--rootdir={target}",
        f"--cov={target}",
        "--cov-context=test",
    ]
    if section.get("branch", True):
        pytest_cmd.append("--cov-branch")
    pytest_cmd += ["--cov-report=", "-q", "--no-header",
                   "-p", "no:cacheprovider"]

    try:
        subprocess.run(pytest_cmd, cwd=str(target), env=env,
                       capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {}, False, f"pytest exceeded the {timeout}s coverage timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, False, f"pytest could not run: {exc}"

    if not data_file.exists():
        return {}, False, (
            "pytest-cov produced no coverage data file; is pytest-cov "
            "installed in the interpreter used for the run?")

    json_cmd = [python, "-m", "coverage", "json", "--show-contexts",
                "-o", str(json_path)]
    try:
        proc = subprocess.run(json_cmd, cwd=str(target), env=env,
                              capture_output=True, text=True, check=False,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired
            ) as exc:
        return {}, False, f"coverage json failed: {exc}"
    if proc.returncode != 0 or not json_path.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {}, False, "coverage json failed: " + (
            tail[-1] if tail else "unknown reason")

    try:
        with open(json_path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, False, f"could not parse the coverage JSON: {exc}"
    return _parse(doc, target), True, (
        f"{len(doc.get('files', {}))} measured files "
        f"(python={python}, pytest-cov dynamic contexts)")


def _parse(doc: dict, target: Path) -> dict[str, CoverageSet]:
    """Turn the coverage JSON ``contexts`` map into per-test CoverageSets.

    Both the file keys and the file part of the context node ids are
    normalized to be relative to the target root: coverage.py emits absolute
    paths or paths relative to wherever it found its config, and pytest's
    node ids are relative to pytest's rootdir (which we force to the target).
    """
    by_test: dict[str, CoverageSet] = {}
    files = doc.get("files", {}) or {}
    for file_key, entry in sorted(files.items()):
        file_rel = _relativize(file_key, target)
        path = Path(file_rel)
        # Test modules and conftests are audit inputs, not production code:
        # their lines must not count toward coverage fingerprints (§2).
        if path.name == "conftest.py" or _is_test_module(path):
            continue
        contexts = entry.get("contexts", {}) or {}
        for line_str, ctx_list in contexts.items():
            for ctx in ctx_list or []:
                if not ctx or not ctx.endswith(CONTEXT_SUFFIX):
                    continue
                node_id = ctx[:-len(CONTEXT_SUFFIX)]
                key = _node_key(node_id, target)
                cov = by_test.setdefault(key, CoverageSet(test_id=key))
                try:
                    cov.lines.add(f"{file_rel}:{int(line_str)}")
                except ValueError:
                    continue
                cov.lines = set(sorted(cov.lines))
    return by_test


def _relativize(path_str: str, target: Path) -> str:
    p = Path(path_str)
    if p.is_absolute():
        try:
            return str(p.relative_to(target))
        except ValueError:
            return path_str
    parts = p.parts
    tparts = target.parts
    for i in range(max(0, len(parts) - len(tparts)) + 1):
        if parts[i:i + len(tparts)] == tparts:
            return str(Path(*parts[i + len(tparts):])) or path_str
    return path_str


def _node_key(node_id: str, target: Path) -> str:
    """Normalize a pytest node id to ``<rel path>::<test>[param]`` (§5 ids)."""
    node_id = node_id.replace("\\", "/")
    file_part, sep, rest = node_id.partition("::")
    if sep:
        file_part = _relativize(file_part, target)
        return f"{file_part}::{rest}"
    return node_id


def _is_test_module(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") or name.endswith("_test.py") or \
        "tests" in path.parts
