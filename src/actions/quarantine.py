"""Quarantine & destructive-safety protocol (§8).

Layout (§8, verbatim):

    quarantine/
      manifest.json
      <test-id>/
        original.<ext>       # exact source of the test
        evidence.json       # every finding that contributed
        state               # quarantined | approved-removed | rejected

Rules implemented here:

* "Remove" never deletes: the test is archived in `quarantine/<id>/` and its
  source is rewritten in place to a framework-specific skip stub, preserving
  line numbers.
* Permanent removal requires `approve <id>` plus a `human_signoff` record
  (git identity + timestamp).
* Quarantine is reversible with `restore <id>`.
"""

from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import Decision, QuarantineRecord

SKIPPED_STATES = {"quarantined", "approved-removed", "rejected"}


def make_quarantine_id(test_id: str) -> str:
    digest = hashlib.sha1(test_id.encode("utf-8")).hexdigest()[:10]
    return f"qt-{digest}"


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_identity(target: Path) -> dict[str, str]:
    """Git identity for the human sign-off record (§8)."""
    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(target), capture_output=True,
                text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return (proc.stdout or "").strip()
    name = _git("config", "user.name") or "unknown"
    email = _git("config", "user.email") or "unknown"
    commit = _git("rev-parse", "HEAD") or "unknown"
    return {"name": name, "email": email, "commit": commit}


def stub_source(name: str, quarantine_id: str, line_count: int,
                 indent: str = "") -> list[str]:
    """pytest skip stub that occupies exactly `line_count` lines (§8).

    The file's total line count must not change: every other test in the
    file keeps its line numbers, and `restore` can swap the original back
    into the same span. Short functions therefore get a compact one-body
    stub; longer ones are padded with comment lines.
    """
    prefix = indent
    marker = f"refine-tests:quarantined {quarantine_id}"
    if line_count <= 1:
        return [f"{prefix}def {name}(): pass  # {marker}"]
    if line_count == 2:
        return [
            f'{prefix}@pytest.mark.skip(reason="{marker}")',
            f"{prefix}def {name}(): pass  # {marker}",
        ]
    lines = [
        f'{prefix}@pytest.mark.skip(reason="{marker}")',
        f"{prefix}def {name}():",
        f"{prefix}    pass  # {marker}",
    ]
    # Pad with comment lines to preserve line numbers exactly.
    while len(lines) < line_count:
        lines.append(f"{prefix}    # quarantined placeholder ({marker})")
    return lines[:line_count]


def extract_function(path: Path, func_name: str) -> tuple[list[str], int, int] | None:
    """Exact source lines of a top-level test function (+ decorators)."""
    try:
        source = path.read_text(encoding="utf-8").splitlines(keepends=False)
        tree = ast.parse("\n".join(source))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == func_name:
            # `lineno` is the first line of the extracted span (decorators
            # included): apply_removal replaces [lineno-1, end_lineno) with
            # the stub, and that span must cover the original decorators
            # exactly.
            start = (node.decorator_list[0].lineno - 1
                     if node.decorator_list else node.lineno - 1)
            return (source[start:node.end_lineno],
                    start + 1, node.end_lineno)
    return None


class QuarantineStore:
    """Filesystem-backed quarantine state."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        # Created lazily on the first write: read-only audit runs must not
        # touch the filesystem (§9).
        self.manifest_path = self.root / "manifest.json"

    # -- manifest -----------------------------------------------------------

    def load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"records": []}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"records": []}
        data.setdefault("records", [])
        return data

    def save(self, data: dict[str, Any]) -> None:
        data["records"] = sorted(
            data.get("records", []),
            key=lambda r: (r.get("state", ""), r.get("id", "")))
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def records(self) -> list[QuarantineRecord]:
        out: list[QuarantineRecord] = []
        for raw in self.load().get("records", []):
            out.append(QuarantineRecord(
                id=raw.get("id", ""),
                test_id=raw.get("test_id", ""),
                file=raw.get("file", ""),
                state=raw.get("state", "quarantined"),
                quarantined_at=raw.get("quarantined_at", ""),
                human_signoff=raw.get("human_signoff", {}),
                findings=raw.get("findings", []),
                original_path=raw.get("original_path", "")))
        return sorted(out, key=lambda r: r.id)

    # -- removal (§8) ----------------------------------------------------------

    def apply_removal(self, decision: Decision, target: Path) -> QuarantineRecord:
        test_file = target / decision.file
        node_id = decision.test_id
        func_name = node_id.split("::")[-1].split("[")[0]
        extracted = extract_function(test_file, func_name)
        if extracted is None:
            raise FileNotFoundError(
                f"test {node_id} not found in {decision.file}")
        original_lines, lineno, end_lineno = extracted
        qid = make_quarantine_id(node_id)
        entry = self.root / qid
        self.root.mkdir(parents=True, exist_ok=True)
        entry.mkdir(exist_ok=True)
        (entry / "original.py").write_text(
            "\n".join(original_lines) + "\n", encoding="utf-8")
        evidence = {
            "test_id": node_id,
            "file": decision.file,
            "action": "remove",
            "confidence": decision.confidence,
            "co_signals": decision.co_signals,
            "findings": [f.to_dict() for f in sorted(
                decision.findings, key=lambda f: (f.category, f.detector))],
            "evidence_line": decision.evidence_line,
            "mutation": decision.mutation.to_dict() if decision.mutation else None,
            "quarantined_at": _utc_stamp(),
            "human_signoff": {},
            "state": "quarantined",
        }
        (entry / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        # Rewrite the test in place to a skip stub, preserving line numbers.
        stub = stub_source(func_name, qid, len(original_lines))
        file_lines = test_file.read_text(encoding="utf-8").splitlines(
            keepends=False)
        file_lines[lineno - 1:end_lineno] = stub
        test_file.write_text("\n".join(file_lines) + "\n", encoding="utf-8")
        record = QuarantineRecord(
            id=qid, test_id=node_id, file=decision.file,
            state="quarantined", quarantined_at=evidence["quarantined_at"],
            findings=evidence["findings"],
            original_path=f"quarantine/{qid}/original.py",
            lineno=lineno, end_lineno=end_lineno)
        self._upsert(record)
        return record

    def _upsert(self, record: QuarantineRecord) -> None:
        data = self.load()
        records = [r for r in data.get("records", []) if r.get("id") != record.id]
        records.append(record.to_dict())
        data["records"] = records
        self.save(data)

    # -- approve / restore (§8) -------------------------------------------------

    def approve(self, qid: str, target: Path) -> QuarantineRecord:
        record = self._require(qid, {"quarantined"})
        entry = self.root / qid
        evidence_path = entry / "evidence.json"
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            evidence = {}
        evidence["human_signoff"] = {
            **git_identity(target),
            "signed_at": _utc_stamp(),
        }
        evidence["state"] = "approved-removed"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        # Permanent removal: the stub leaves the working tree; the exact
        # original stays archived in quarantine/<id>/original.py (§8).
        test_file = target / record.file
        func_name = record.test_id.split("::")[-1].split("[")[0]
        extracted = extract_function(test_file, func_name)
        if extracted is not None:
            _, lineno, end_lineno = extracted
            file_lines = test_file.read_text(
                encoding="utf-8").splitlines(keepends=False)
            del file_lines[lineno - 1:end_lineno]
            test_file.write_text("\n".join(file_lines) + "\n", encoding="utf-8")
        record = QuarantineRecord(
            **{**record.to_dict(),
               "state": "approved-removed",
               "human_signoff": evidence["human_signoff"]})
        self._upsert(record)
        return record

    def restore(self, qid: str, target: Path) -> QuarantineRecord:
        record = self._require(qid, {"quarantined", "approved-removed"})
        entry = self.root / qid
        original = (entry / "original.py").read_text(encoding="utf-8")
        test_file = target / record.file
        func_name = record.test_id.split("::")[-1].split("[")[0]
        if test_file.exists():
            file_lines = test_file.read_text(
                encoding="utf-8").splitlines(keepends=False)
            slot = self._find_stub_slot(file_lines, func_name)
        else:
            file_lines, slot = [], None
        if slot is None:
            # Nothing to replace (e.g. the file moved): append at the end.
            file_lines.extend(original.rstrip("\n").split("\n"))
        else:
            start, end = slot
            file_lines[start:end] = original.rstrip("\n").split("\n")
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("\n".join(file_lines) + "\n", encoding="utf-8")
        # Archive the quarantine entry out of the live manifest.
        restored_dir = self.root / ".restored"
        restored_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry), str(restored_dir / qid))
        data = self.load()
        data["records"] = [r for r in data.get("records", [])
                           if r.get("id") != qid]
        self.save(data)
        return record

    def _find_stub_slot(self, lines: list[str], func_name: str) -> tuple[int, int] | None:
        """Locate a quarantined stub block (def .. pass # refine-tests:...) by name."""
        pattern = re.compile(rf"def {re.escape(func_name)}\(\):")
        for i, line in enumerate(lines):
            if pattern.search(line):
                start = i
                while start > 0 and (lines[start - 1].startswith("@pytest") or
                                     lines[start - 1].strip().startswith("@")):
                    start -= 1
                end = i + 1
                while end < len(lines) and (
                        lines[end].startswith("    ") and (
                        "refine-tests" in lines[end] or lines[end].strip() == "")):
                    end += 1
                return start, end
        return None

    def _require(self, qid: str, allowed: set[str]) -> QuarantineRecord:
        for record in self.records():
            if record.id == qid:
                if record.state not in allowed:
                    raise ValueError(
                        f"quarantine {qid} is in state {record.state!r}; "
                        f"expected one of {sorted(allowed)}")
                return record
        raise KeyError(f"no quarantined test with id {qid}")
