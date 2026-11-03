"""CI history mining (§2, §5.1, §5.2).

Providers:

* `local`    — read `<target>/.ci-history/*.json` (schema below). Zero network.
* `github`   — GitHub Actions via `gh` CLI or REST (api.github.com, GITHUB_TOKEN).
* `gitlab`   — GitLab CI via REST (gitlab.com, GITLAB_TOKEN).
* none       — explicitly disabled.

Per-test results come from JUnit XML staged under `.ci-history/` named
`junit-<run_id>.xml`; provider APIs list *runs* (and changed files), and the
JUnit documents the per-test outcomes for each run. This split keeps a clear
audit trail and keeps every network call behind an explicit opt-in (§9: audit
mode makes no network calls when --offline is set).

Local cache schema (`.ci-history/*.json`):

    {
      "provider": "local",
      "runs": [
        {"run_id": "r001", "status": "passed",
         "test_results": [{"test_id": "tests/test_a.py::test_a",
                           "status": "passed"}],
         "files_changed": ["prod/calc.py"]}
      ]
    }
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .models import CiHistory, CiRun, TestResult

JUnitRow = tuple[str, str, list[TestResult]]  # (run_id, status, results)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_local_cache(target: Path) -> list[CiRun]:
    """Runs + per-test results from the local JSON cache (both providers)."""
    runs: list[CiRun] = []
    for path in sorted(glob.glob(str(target / ".ci-history" / "*.json"))):
        data = _load_json(Path(path))
        if not data or not isinstance(data, dict):
            continue
        for run in data.get("runs", []):
            if not isinstance(run, dict):
                continue
            results = [
                TestResult(
                    test_id=str(tr.get("test_id", "")),
                    status=str(tr.get("status", "passed")),
                    duration_s=float(tr.get("duration_s", 0.0) or 0.0))
                for tr in run.get("test_results", [])
                if isinstance(tr, dict) and tr.get("test_id")
            ]
            runs.append(CiRun(
                run_id=str(run.get("run_id", "")),
                status=str(run.get("status", "passed")),
                test_results=results,
                files_changed=[str(f) for f in run.get("files_changed", [])],
            ))
    runs.sort(key=lambda r: r.run_id)
    return runs


def _read_junit_cache(target: Path) -> dict[str, list[TestResult]]:
    """JUnit XML staged as `.ci-history/junit-<run_id>.xml`."""
    out: dict[str, list[TestResult]] = {}
    for path in sorted(glob.glob(str(target / ".ci-history" / "junit-*.xml"))):
        run_id = Path(path).stem[len("junit-"):]
        results: list[TestResult] = []
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            continue
        for case in root.iter("testcase"):
            test_id = case.get("name") or ""
            classname = case.get("classname") or ""
            if classname and test_id:
                file = classname.replace(".", "/")
                test_id = f"{file}.py::{test_id.split('.')[-1]}" \
                    if not test_id.startswith(classname) else test_id
            failure = case.find("failure") is not None or \
                case.find("error") is not None
            skipped = case.find("skipped") is not None
            status = "skipped" if skipped else ("failed" if failure else "passed")
            results.append(TestResult(test_id=test_id, status=status))
        out[run_id] = results
    return out


class GitHubProvider:
    """GitHub Actions: list runs via gh CLI or REST; outcomes from JUnit cache."""

    base = "https://api.github.com"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def _remote(self, target: Path) -> tuple[str, str] | None:
        try:
            proc = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(target), capture_output=True, text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        url = (proc.stdout or "").strip()
        m = re.match(r"(?:https://)?(?:git@)?github\.com[:/]([^/]+)/(.+?)(?:\.git)?$",
                     url)
        if not m:
            return None
        return m.group(1), m.group(2)

    def list_runs(self, target: Path) -> list[dict[str, Any]]:
        remote = self._remote(target)
        if remote is None:
            return []
        owner, repo = remote
        # Prefer the gh CLI (handles auth from the environment).
        if shutil_which("gh"):
            proc = subprocess.run(
                ["gh", "run", "list", "--repo", f"{owner}/{repo}",
                 "--json", "databaseId,status,conclusion,createdAt",
                 "--limit", "50"],
                capture_output=True, text=True, check=False)
            if proc.returncode == 0:
                try:
                    return json.loads(proc.stdout or "[]")
                except json.JSONDecodeError:
                    pass
        # Fall back to the REST API with GITHUB_TOKEN (§2).
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            return []
        url = f"{self.base}/repos/{owner}/{repo}/actions/runs?per_page=50"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return []
        return [
            {"databaseId": r.get("id"), "conclusion": r.get("conclusion"),
             "createdAt": r.get("run_started_at")}
            for r in data.get("workflow_runs", [])
        ]

    def history(self, target: Path) -> tuple[CiHistory, str]:
        runs_meta = self.list_runs(target)
        junit = _read_junit_cache(target)
        runs: list[CiRun] = []
        for meta in runs_meta:
            run_id = str(meta.get("databaseId") or meta.get("id") or "")
            status = str(meta.get("conclusion") or meta.get("status") or "passed")
            runs.append(CiRun(run_id=run_id, status=status,
                              test_results=junit.get(run_id, [])))
        cache = _read_local_cache(target)
        seen = {r.run_id for r in runs}
        for extra in cache:
            if extra.run_id not in seen:
                runs.append(extra)
        runs.sort(key=lambda r: r.run_id)
        if runs and not any(r.test_results for r in runs):
            return (CiHistory(provider="github", runs=runs),
                    "github: run metadata fetched, but no per-test results — "
                    "stage JUnit XML as .ci-history/junit-<run_id>.xml "
                    "(see README §CI)")
        return (CiHistory(provider="github", runs=runs),
                f"github: {len(runs)} runs" if runs else
                "github: no runs resolved (check remote origin / GITHUB_TOKEN)")


class GitLabProvider:
    """GitLab CI: list pipelines via REST (gitlab.com, GITLAB_TOKEN)."""

    base = "https://gitlab.com/api/v4"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def _project_id(self, target: Path) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(target), capture_output=True, text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        url = (proc.stdout or "").strip()
        m = re.match(r"(?:https://)?(?:git@)?gitlab\.com[:/](.+?)(?:\.git)?$", url)
        if not m:
            return None
        return m.group(1).replace("/", "%2F")

    def history(self, target: Path) -> tuple[CiHistory, str]:
        project = self._project_id(target)
        token = os.environ.get("GITLAB_TOKEN")
        if not project or not token:
            return (CiHistory(provider="gitlab"),
                    "gitlab: no GITLAB_TOKEN or no gitlab.com remote")
        url = f"{self.base}/projects/{project}/pipelines?per_page=50"
        req = urllib.request.Request(url, headers={
            "PRIVATE-TOKEN": token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                pipelines = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return (CiHistory(provider="gitlab"),
                    "gitlab: pipeline listing failed")
        junit = _read_junit_cache(target)
        runs = [
            CiRun(run_id=str(p.get("id", "")),
                  status=str(p.get("status") or "passed"),
                  test_results=junit.get(str(p.get("id", "")), []))
            for p in pipelines
        ]
        runs.sort(key=lambda r: r.run_id)
        return (CiHistory(provider="gitlab", runs=runs),
                f"gitlab: {len(runs)} pipelines")


def shutil_which(binary: str) -> str | None:
    from shutil import which
    return which(binary)


def resolve_provider(cfg: dict[str, Any], offline: bool) -> tuple[str, bool]:
    """(provider, network_needed) — auto resolves without network; network is
    only needed for an explicitly selected github/gitlab provider."""
    provider = (cfg.get("ci", {}) or {}).get("provider", "auto")
    if offline and provider == "auto":
        return "local", False          # offline: only the local cache may be read
    return provider, provider in {"github", "gitlab"}


def load_history(target: Path, cfg: dict[str, Any], offline: bool = False
                 ) -> tuple[CiHistory, str, bool]:
    """Load CI history. Returns (history, note, used_network)."""
    provider, needs_network = resolve_provider(cfg, offline)
    target = Path(target)
    # --offline is a hard override (§9, §14.7): never make a network call.
    if offline and provider in {"github", "gitlab"}:
        provider = "local"
    if provider == "none":
        return CiHistory(provider="none"), "disabled (config ci.provider=none)", False

    if provider == "local":
        runs = _read_local_cache(target)
        if not runs:
            return (CiHistory(provider="local"),
                    "no .ci-history/*.json found under the target root", False)
        note = f"local: {len(runs)} runs from .ci-history/"
        if not any(r.test_results for r in runs):
            note += "; WARNING: no per-test results in cache"
        return CiHistory(provider="local", runs=runs), note, False

    if provider == "github":
        hist, note = GitHubProvider(cfg).history(target)
        return hist, note, needs_network

    if provider == "gitlab":
        hist, note = GitLabProvider(cfg).history(target)
        return hist, note, needs_network

    # auto: prefer a local cache, then infer from the git remote — the remote
    # is read with a *local* git call; a network fetch happens only below.
    runs = _read_local_cache(target)
    if runs:
        note = f"local: {len(runs)} runs from .ci-history/"
        if not any(r.test_results for r in runs):
            note += "; WARNING: no per-test results in cache"
        return CiHistory(provider="local", runs=runs), note, False
    if offline:
        return (CiHistory(provider="unavailable"),
                "CI history: unavailable (reason: --offline and no local "
                ".ci-history cache)", False)
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(target), capture_output=True, text=True, check=False)
        remote = (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        remote = ""
    if "github.com" in remote:
        hist, note = GitHubProvider(cfg).history(target)
        return hist, note, True
    if "gitlab.com" in remote:
        hist, note = GitLabProvider(cfg).history(target)
        return hist, note, True
    return (CiHistory(provider="unavailable"),
            "CI history: unavailable (reason: no .ci-history cache and no "
            "github.com/gitlab.com remote)", False)
