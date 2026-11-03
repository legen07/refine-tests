"""CLI-level tests: scoped audits (--path/--diff) and usage-error exit codes.

These drive the real `refine-tests` launcher through subprocess on a
throwaway copy of the fixture suite, exactly like the acceptance runner.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "src/tests/fixtures"
LAUNCHER = str(ROOT / "refine-tests")


def _copy_fixtures(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, target,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", ".pytest_cache", ".refine-tests-cov",
                        "quarantine", "proposed", "refine-tests-report.*"))
    return target


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *argv],
        cwd=str(ROOT), capture_output=True, text=True, timeout=600)


def _tests(target: Path) -> dict:
    return {t["test_id"]: t for t in json.loads(
        (target / "refine-tests-report.json").read_text(encoding="utf-8"))
        ["tests"]}


def test_path_scope_limits_the_audit_to_matching_files(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    proc = _run("--offline", "--path", "tests/test_calc_duplicates.py",
                str(target))
    assert proc.returncode == 0, proc.stderr
    tests = _tests(target)
    assert set(tests) == {
        "tests/test_calc_duplicates.py::test_add_pair_alpha",
        "tests/test_calc_duplicates.py::test_add_pair_beta"}
    # The redundant pair is still detected inside the scope.
    assert all(t["action"] == "remove" for t in tests.values())


def test_path_glob_matches_multiple_files(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    proc = _run("--offline", "--path", "tests/test_*duplicate*",
                str(target))
    assert proc.returncode == 0, proc.stderr
    files = {Path(t).parent for t in _tests(target)}
    assert files == {Path("tests")}


def test_path_and_diff_are_mutually_exclusive(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    proc = _run("--offline", "--path", "tests/*", "--diff", "a..b", str(target))
    assert proc.returncode == 1
    assert "mutually exclusive" in proc.stderr


def test_bad_diff_range_is_a_clean_one_liner_exit_2(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    proc = _run("--offline", "--diff", "main..feature", str(target))
    assert proc.returncode == 2
    assert "refine-tests: git diff main..feature failed" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_malformed_diff_is_reported_cleanly(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    proc = _run("--offline", "--diff", "not-a-range", str(target))
    assert proc.returncode == 2
    assert "expects the form base..head" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_diff_scope_uses_git_history(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    env_git = ["git", "-c", "user.email=rt@x", "-c", "user.name=rt"]
    subprocess.run(env_git + ["init", "-q", "."], cwd=str(target), check=True)
    subprocess.run(env_git + ["add", "-A"], cwd=str(target), check=True)
    subprocess.run(env_git + ["commit", "-qm", "base"], cwd=str(target),
                   check=True)
    (target / "tests/test_lowvalue.py").write_text(
        "# churn\n" + (target / "tests/test_lowvalue.py").read_text(
            encoding="utf-8"), encoding="utf-8")
    subprocess.run(env_git + ["add", "-A"], cwd=str(target), check=True)
    subprocess.run(env_git + ["commit", "-qm", "churn"], cwd=str(target),
                   check=True)
    proc = _run("--offline", "--all-tiers", "--diff", "HEAD~1..HEAD",
                str(target))
    assert proc.returncode == 0, proc.stderr
    # The audit is limited to the two tests in the touched file.
    tests = _tests(target)
    assert set(tests) == {
        "tests/test_lowvalue.py::test_price_smoke_only",
        "tests/test_lowvalue.py::test_price_full"}
    # The low-value finding survives the scoping (EXPECT.json classification).
    assert tests["tests/test_lowvalue.py::test_price_smoke_only"]["action"] \
        == "remove"
    assert tests["tests/test_lowvalue.py::test_price_full"]["action"] == "keep"


def test_audit_writes_no_artifacts_into_the_target(tmp_path: Path):
    target = _copy_fixtures(tmp_path)
    _run("--offline", str(target))
    assert not (target / "quarantine").exists()
    assert not (target / "proposed").exists()
    assert (target / "refine-tests-report.md").exists()
    assert (target / "refine-tests-report.json").exists()


def test_nonexistent_target_fails_cleanly(tmp_path: Path):
    proc = _run("--offline", str(tmp_path / "does-not-exist"))
    assert proc.returncode == 1
    assert "does not exist" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_version_reads_the_manifest(tmp_path: Path):
    proc = _run("--version")
    assert proc.returncode == 0
    assert "refine-tests" in proc.stdout


@pytest.mark.parametrize("mode", ["audit"])  # audit stays read-only
def test_smoke_tier_veto_survives_scoping(tmp_path: Path, mode: str):
    target = _copy_fixtures(tmp_path)
    proc = _run("--offline", "--path", "tests/test_smoke_vetoes.py",
                str(target))
    assert proc.returncode == 0, proc.stderr
    tests = _tests(target)
    # The smoke tier is a hard veto: removals become keep (flagged).
    assert all(t["action"] == "keep" for t in tests.values())
    assert any(t.get("vetoed") for t in tests.values())
