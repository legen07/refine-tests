"""Unit tests for the quarantine protocol primitives (§8)."""

from pathlib import Path

from src.actions.quarantine import (
    QuarantineStore,
    extract_function,
    make_quarantine_id,
    stub_source,
)
from src.models import Decision


def test_quarantine_id_is_deterministic_and_stable():
    a = make_quarantine_id("tests/test_x.py::test_a")
    b = make_quarantine_id("tests/test_x.py::test_a")
    assert a == b
    assert a.startswith("qt-")
    assert make_quarantine_id("tests/test_x.py::test_b") != a


def test_stub_source_preserves_line_counts_exactly():
    for count in (1, 2, 3, 5, 40):
        stub = stub_source("test_x", "qt-abc", count)
        assert len(stub) == count, (count, stub)
        assert any("qt-abc" in ln for ln in stub)


def test_stub_compact_forms_are_valid_python():
    import ast
    for count in (1, 2):
        ast.parse("\n".join(stub_source("test_x", "qt-abc", count)))


def test_extract_function_returns_exact_span(tmp_path: Path):
    f = tmp_path / "test_demo.py"
    f.write_text(
        'import pytest\n\n\n@ pytest.mark.smoke\ndef test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n',
        encoding="utf-8")
    extracted, lineno, end_lineno = extract_function(f, "test_a")
    assert lineno == 4                       # decorator included
    assert end_lineno == 6
    assert extracted == [
        '@ pytest.mark.smoke', 'def test_a():', '    assert 1']
    assert extract_function(f, "test_z") is None


def _decision(test_id: str, file: str) -> Decision:
    return Decision(test_id=test_id, file=file, tier="core",
                    action="remove", confidence=0.9,
                    co_signals=["low-value", "never-failing"], findings=[])


def test_quarantine_roundtrip_roundtrip(tmp_path: Path):
    target = tmp_path
    (target / "tests").mkdir()
    f = target / "tests" / "test_demo.py"
    f.write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    store = QuarantineStore(target / "quarantine")
    record = store.apply_removal(_decision(
        "tests/test_demo.py::test_a", "tests/test_demo.py"), target)

    # §8 layout: <id>/{original.<ext>, evidence.json} + manifest.
    entry = target / "quarantine" / record.id
    assert (entry / "original.py").exists()
    assert (entry / "evidence.json").exists()
    evidence = __import__("json").loads((entry / "evidence.json").read_text())
    assert evidence["action"] == "remove"
    assert evidence["state"] == "quarantined"

    # The stub preserves the file's line count exactly.
    stubbed = f.read_text(encoding="utf-8").splitlines()
    assert len(stubbed) == 2
    assert "pytest.mark.skip" in stubbed[0]

    # Approval records human sign-off (the store cannot invent a committer).
    approved = store.approve(record.id, target)
    assert approved.state == "approved-removed"
    assert approved.human_signoff["name"]

    # Restore swaps the original back in (approved removals append at the
    # end when the slot is gone — §8 allows both).
    store.restore(record.id, target)
    restored_src = f.read_text(encoding="utf-8")
    assert "pytest.mark.skip" not in restored_src
    assert "def test_a():\n    assert 1" in restored_src
    assert (target / "quarantine" / ".restored" / record.id).exists()
    assert record.id
