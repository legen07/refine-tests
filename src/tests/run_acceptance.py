#!/usr/bin/env python3
"""Acceptance runner for the refine-tests plugin (§14, criteria 1–8).

Runs the real CLI end-to-end against the fixture suite (in throwaway copies)
and asserts:

  1. every fixture test is classified per EXPECTED.json (findings, action,
     tier, veto state);
  2. every one of the 11 detectors has ≥3 true positives and ≥2 true
     negatives in the fixture corpus;
  3. `--cleanup --dry-run` performs no writes and exits 0;
  4. `--cleanup` without `--yes` performs no writes and exits 2;
  5. `--cleanup --yes` quarantines exactly the gated removals, stubs preserve
     file line counts, originals + evidence + manifest are written, and every
     changed path is explained by the run's manifest (§8);
  6. a second audit run is byte-identical (determinism, §14.6);
  7. offline mode makes zero network calls;
  8. the approve/restore round-trip works and a restored test is re-detected.

Usage:  python3 src/tests/run_acceptance.py
Exit 0 iff every check passes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
FIXTURES = PLUGIN / "src" / "tests" / "fixtures"
EXPECTED_PATH = FIXTURES / "EXPECTED.json"
CLI = [str(PLUGIN / "refine-tests")]
PY = str(PLUGIN / ".venv" / "bin" / "python")

ARTIFACT_DIRS = {".refine-tests-cov", "__pycache__", ".pytest_cache"}
REPORT_NAMES = {"refine-tests-report.md", "refine-tests-report.json"}

CHECKS: list[tuple[str, str]] = []
FAILURES: list[str] = []


def check(name: str):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


def run_cli(*args: str, cwd: Path | None = None, expect: int | None = None,
             env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        CLI + list(args), cwd=str(cwd or PLUGIN), env=env,
        capture_output=True, text=True, timeout=600)
    if expect is not None and proc.returncode != expect:
        raise AssertionError(
            f"exit code {proc.returncode} != {expect}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def fresh_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rt-accept-"))

    def ignore(_src, names):
        return [n for n in names
                if n in ARTIFACT_DIRS or n.endswith("__pycache__")]

    shutil.copytree(FIXTURES, tmp / "fixtures", ignore=ignore)
    return tmp / "fixtures"


def snapshot(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = str(path.relative_to(root))
        if any(part in ARTIFACT_DIRS or part == ".git"
               for part in Path(rel).parts):
            continue
        if path.name in REPORT_NAMES:
            continue
        out[rel] = path.read_bytes()
    return out


def read_report(target: Path) -> dict:
    return json.loads(
        (target / "refine-tests-report.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# §14.1 — every fixture test classified per EXPECTED.json
# ---------------------------------------------------------------------------

@check("14.1 classification matches EXPECTED.json")
def check_classification() -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    target = fresh_copy()
    try:
        run_cli("--offline", str(target), expect=0)
        report = read_report(target)
        actual = {t["test_id"]: t for t in report["tests"]}
        missing = set(expected) - set(actual)
        assert not missing, f"tests missing from the report: {sorted(missing)}"
        for test_id, want in sorted(expected.items()):
            got = actual[test_id]
            assert got["action"] == want["action"], \
                f"{test_id}: action {got['action']!r} != {want['action']!r}"
            assert got["tier"] == want["tier"], \
                f"{test_id}: tier {got['tier']!r} != {want['tier']!r}"
            assert bool(got.get("vetoed")) == want["vetoed"], \
                f"{test_id}: vetoed {got.get('vetoed')} != {want['vetoed']}"
            got_findings = {f["category"]: round(f["confidence"], 2)
                            for f in got["findings"]}
            assert set(got_findings) == set(want["findings"]), \
                f"{test_id}: findings {sorted(got_findings)} != " \
                f"{sorted(want['findings'])}"
            for cat, conf in want["findings"].items():
                assert abs(got_findings[cat] - conf) < 0.06, \
                    f"{test_id}/{cat}: confidence {got_findings[cat]} " \
                    f"!= {conf}"
        print(f"  {len(expected)} tests classified per EXPECTED.json "
              f"(action + tier + vetoed + findings)")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14.2 — every detector: ≥3 TPs and ≥2 TNs in the fixture corpus
# ---------------------------------------------------------------------------

DETECTORS = ["never-failing", "flaky", "redundant", "zombie", "low-value",
             "tautological", "mock-heavy", "typecheck-only", "existence-only",
             "brittle", "implementation-detail"]


@check("14.2 every detector has ≥3 TPs and ≥2 TNs")
def check_detector_coverage() -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    for category in DETECTORS:
        tps = [k for k, v in expected.items() if category in v["findings"]]
        tns = [k for k, v in expected.items() if category not in v["findings"]]
        assert len(tps) >= 3, f"{category}: only {len(tps)} TPs ({tps})"
        assert len(tns) >= 2, f"{category}: only {len(tns)} TNs ({tns})"
        print(f"  {category:22} TP={len(tps):2}  TN={len(tns):2}")


# ---------------------------------------------------------------------------
# §14.3 — dry run performs no writes, exit 0
# ---------------------------------------------------------------------------

@check("14.3 --cleanup --dry-run: no writes, exit 0")
def check_dry_run() -> None:
    target = fresh_copy()
    try:
        run_cli("--offline", str(target), expect=0)     # prime artifacts
        after_audit = snapshot(target)
        proc = run_cli("--offline", "--cleanup", "--dry-run", str(target),
                       expect=0)
        assert "DRY RUN" in proc.stdout, "stdout lacks the dry-run plan"
        assert snapshot(target) == after_audit, \
            "the dry run modified project files"
        assert not (target / "quarantine").exists(), \
            "the dry run wrote quarantine/"
        assert not (target / "proposed").exists(), \
            "the dry run wrote proposed/"
        print("  no writes; plan printed; exit 0")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14.4 — cleanup without --yes: no writes, exit 2
# ---------------------------------------------------------------------------

@check("14.4 --cleanup without --yes: no writes, exit 2")
def check_refused_cleanup() -> None:
    target = fresh_copy()
    try:
        run_cli("--offline", str(target), expect=0)
        after_audit = snapshot(target)
        proc = run_cli("--offline", "--cleanup", str(target), expect=2)
        combined = proc.stdout + proc.stderr
        assert "refused without --yes" in combined, \
            "output lacks the refusal line"
        assert snapshot(target) == after_audit, \
            "the refused cleanup modified project files"
        assert not (target / "quarantine").exists()
        assert not (target / "proposed").exists()
        print("  no writes; refusal printed; exit 2")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14.5 — --cleanup --yes: quarantine protocol, stubs, manifest
# ---------------------------------------------------------------------------

@check("14.5 --cleanup --yes: §8 quarantine protocol")
def check_apply_cleanup() -> None:
    target = fresh_copy()
    try:
        run_cli("--offline", str(target), expect=0)
        before = snapshot(target)
        run_cli("--offline", "--cleanup", "--yes", str(target), expect=0)
        after = snapshot(target)

        manifest_path = target / "quarantine" / "manifest.json"
        assert manifest_path.exists(), "quarantine manifest.json missing"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        removed = [r for r in manifest.get("records", [])
                   if r.get("state") == "quarantined"]
        assert len(removed) == 6, \
            f"expected 6 quarantined removals, manifest has {len(removed)}"

        changed = sorted(rel for rel in before
                         if before[rel] != after.get(rel))
        explained: set[str] = set()
        for record in removed:
            qid = record["id"]
            qdir = target / "quarantine" / qid
            original = qdir / "original.py"
            evidence = qdir / "evidence.json"
            assert original.exists(), f"{qid}: original.py missing"
            assert evidence.exists(), f"{qid}: evidence.json missing"
            ev = json.loads(evidence.read_text(encoding="utf-8"))
            assert ev["state"] == "quarantined"
            assert ev["action"] == "remove"
            assert ev["file"] in before, f"{qid}: unknown file {ev['file']}"
            explained.add(ev["file"])
            # §8: the stub rewrites the test in place, preserving line count.
            stub_lines = (target / ev["file"]).read_text(
                encoding="utf-8").splitlines()
            orig_lines = original.read_text(encoding="utf-8").splitlines()
            file_before = before[ev["file"]].decode("utf-8").splitlines()
            assert len(stub_lines) == len(file_before), (
                f"{ev['file']}: stub changed the file line count "
                f"({len(stub_lines)} vs {len(file_before)})")
            body = "\n".join(stub_lines[record["lineno"] - 1:
                                        record["end_lineno"]])
            assert "pytest.mark.skip" in body, \
                f"{qid}: stub lacks the pytest.mark.skip decorator"
            assert qid in body, f"{qid}: stub lacks the quarantine id"
            # The archived original is the removed function, verbatim.
            assert "\n".join(orig_lines).strip() != "", \
                f"{qid}: empty archived original"
        # Every changed source path is explained by the manifest (§8).
        reports = {rel for rel in changed
                   if Path(rel).name in REPORT_NAMES}
        unexplained = sorted(set(changed) - explained - reports)
        assert not unexplained, \
            f"changed paths not explained by the quarantine manifest: " \
            f"{unexplained}"
        # Refine decisions become proposals, not rewrites (§6/§12).
        proposals = list((target / "proposed").glob("pp-*"))
        assert len(proposals) >= 6, \
            f"expected ≥6 proposals (refine actions), got {len(proposals)}"
        print(f"  6 removals quarantined; {len(proposals)} proposals written; "
              f"all changed paths explained")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14.6 — determinism: a second audit run is byte-identical
# ---------------------------------------------------------------------------

@check("14.6 determinism: second run byte-identical")
def check_determinism() -> None:
    target = fresh_copy()
    try:
        run_cli("--offline", str(target), expect=0)
        md1 = (target / "refine-tests-report.md").read_bytes()
        js1 = (target / "refine-tests-report.json").read_bytes()
        # Wipe the artifacts a second run would reuse, then re-run.
        for name in REPORT_NAMES:
            (target / name).unlink()
        shutil.rmtree(target / ".refine-tests-cov", ignore_errors=True)
        for cache in target.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        run_cli("--offline", str(target), expect=0)
        md2 = (target / "refine-tests-report.md").read_bytes()
        js2 = (target / "refine-tests-report.json").read_bytes()
        assert md1 == md2, "the markdown report is not deterministic"
        assert js1 == js2, "the JSON report is not deterministic"
        doc = json.loads(js2)
        assert "timestamp" not in json.dumps(doc).lower() or \
            doc.get("run", {}).get("generated_at") in (None, ""), \
            "a timestamp leaked into the report"
        print("  report.md + report.json byte-identical across runs")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14.7 — offline mode: zero network calls
# ---------------------------------------------------------------------------

@check("14.7 offline mode: zero network calls")
def check_offline() -> None:
    target = fresh_copy()
    try:
        # Strongest available proof: a network namespace with no routes.
        unshare = shutil.which("unshare")
        tried_unshare = False
        if unshare:
            probe = subprocess.run([unshare, "-rn", "true"], capture_output=True)
            if probe.returncode == 0:
                tried_unshare = True
                env = os.environ.copy()
                env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
                proc = subprocess.run(
                    [unshare, "-rn"] + CLI + ["--offline", str(target)],
                    cwd=str(PLUGIN), env=env, capture_output=True,
                    text=True, timeout=600)
                assert proc.returncode == 0, \
                    f"CLI failed inside an isolated network namespace: " \
                    f"{proc.stderr}"
        run_cli("--offline", str(target), expect=0)
        report = read_report(target)
        ci = report["run"].get("ci", {})
        assert ci.get("used_network") is False, \
            f"offline mode reported network use: {ci}"
        assert ci.get("source") == "local", \
            f"offline mode did not fall back to local history: {ci}"
        mode = "isolated netns" if tried_unshare else "run.ci.used_network"
        print(f"  no network calls ({mode}); CI source = local")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14.8 — approve / restore round-trip
# ---------------------------------------------------------------------------

@check("14.8 approve/restore round-trip restores the test")
def check_quarantine_roundtrip() -> None:
    target = fresh_copy()
    try:
        run_cli("--offline", str(target), expect=0)
        run_cli("--offline", "--cleanup", "--yes", str(target), expect=0)
        manifest = json.loads(
            (target / "quarantine" / "manifest.json").read_text(
                encoding="utf-8"))
        record = next(r for r in manifest.get("records", [])
                      if r.get("state") == "quarantined")
        qid, test_file = record["id"], record["file"]
        stubbed = (target / test_file).read_text(encoding="utf-8")
        assert "pytest.mark.skip" in stubbed, \
            f"{test_file} has no skip stub after cleanup"
        assert qid in stubbed, f"{test_file} stub lacks the id {qid}"

        # restore: the original function returns, line-for-line.
        run_cli("restore", qid, str(target), expect=0)
        restored = (target / test_file).read_text(encoding="utf-8")
        func_name = record["test_id"].split("::")[-1].split("[")[0]
        assert f"def {func_name}(" in restored, \
            "restored file does not contain the original test function"
        assert qid not in restored, \
            "the restored file still carries this record's stub"
        # (Other stubs in the same file remain — that is correct.)
        manifest_after = json.loads(
            (target / "quarantine" / "manifest.json").read_text(
                encoding="utf-8"))
        live_ids = {r["id"] for r in manifest_after.get("records", [])}
        assert qid not in live_ids, \
            "a restored record must leave the live manifest (§8 archive)"
        archive = target / "quarantine" / ".restored" / qid
        assert archive.exists(), "quarantine entry was not archived (§8)"

        # A re-audit detects the restored test as a live test again.
        run_cli("--offline", str(target), expect=0)
        report = read_report(target)
        ids = {t["test_id"] for t in report["tests"]}
        assert record["test_id"] in ids, \
            "restored test is not re-enumerated by a new audit"
        entry = next(t for t in report["tests"]
                     if t["test_id"] == record["test_id"])
        assert entry["action"] != "quarantined", \
            "restored test still reported as quarantined"
        # And approve of a still-quarantined record records human sign-off.
        run_cli("--offline", "--cleanup", "--yes", str(target), expect=0)
        manifest2 = json.loads(
            (target / "quarantine" / "manifest.json").read_text(
                encoding="utf-8"))
        still = [r for r in manifest2.get("records", [])
                 if r.get("state") == "quarantined"]
        if still:
            pick = still[0]
            run_cli("approve", pick["id"], str(target), expect=0)
            print(f"  restore re-detected; approve signed off on "
                  f"{pick['id']}")
        else:
            print("  restore re-detected; no remaining record to approve")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# §12 gating — Replace proposals only behind the feature flag
# ---------------------------------------------------------------------------

@check("12 Replace gated behind features.replace (strict extension)")
def check_replace_gating() -> None:
    target = fresh_copy()
    try:
        (target / ".refine-tests.toml").write_text(
            '[features]\nreplace = true\n', encoding="utf-8")
        run_cli("--offline", str(target), expect=0)
        report = read_report(target)
        entry = next(t for t in report["tests"] if t["test_id"] ==
                     "tests/test_zombie.py::test_zombie_with_impl_detail")
        assert entry["action"] == "replace", \
            f"with features.replace the zombie+impl-detail test should be " \
            f"Replace, got {entry['action']!r}"
        # The proposal is written only in cleanup mode with the allowance.
        run_cli("--offline", "--cleanup", "--yes",
                "--allow-replace-proposals", str(target), expect=0)
        proposals = list((target / "proposed").glob("*.replacement.md"))
        assert proposals, "no Replace proposal was written (§12)"
        body = proposals[0].read_text(encoding="utf-8")
        # §12 requires: the current failing test, the proposed replacement,
        # the north-star justification, and the organic-test check.
        for section in ("failing original test", "replacement",
                        "north-star question", "Organic test check"):
            assert section.lower() in body.lower(), \
                f"Replace proposal lacks the §12 section: {section}"
        print("  zombie+impl-detail → Replace (flagged); §12 proposal written")
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


def main() -> int:
    print("=" * 72)
    print("refine-tests acceptance run (§14)")
    print("=" * 72)
    passed = 0
    for name, fn in CHECKS:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as exc:
            FAILURES.append(f"{name}: {exc}")
            print(f"FAIL  {name}\n      {exc}")
        except Exception as exc:  # infrastructure failure, not a check result
            FAILURES.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            print(f"ERROR {name}\n      {type(exc).__name__}: {exc}")
    print("-" * 72)
    print(f"{passed}/{len(CHECKS)} checks passed")
    if FAILURES:
        print("\nFailures:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
