"""Sole-coverage veto fixture (§7).

`test_report_dup_with_footprint` duplicates `test_report_summarize_full`:
same single assertion modulo renames, AST similarity ≥ 0.9, and a coverage
Jaccard ≥ 0.9 because ~21 shared lines of `summarize` dwarf the one extra
covered line (`note_trace`, called from the trace branch). Redundancy +
never-failing make it removal-eligible — but it is the *only* test covering
the public `note_trace` symbol, so the sole-coverage veto downgrades it to
Keep (flagged).
"""

from prod import reporting


def test_report_summarize_full():
    lines = reporting.summarize([10, 20])
    assert lines == "total: 30"


def test_report_dup_with_footprint():
    out = reporting.summarize([10, 20], trace=True)
    assert out == "total: 30"
