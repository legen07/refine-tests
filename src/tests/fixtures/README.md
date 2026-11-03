# Fixture suite — a deliberately decayed demo project

This is the forensic test bed for the plugin: **45 tests** across 15 files,
plus a synthetic CI history (`.ci-history/runs.json`, **24 runs**), with every
one of the 11 detector categories represented by ≥3 true positives and ≥2 true
negatives. `EXPECTED.json` pins the exact classification matrix the
acceptance runner verifies (`src/tests/run_acceptance.py`).

## Layout

- `prod/` — the demo production code (`calc`, `pricing`, `reporting`,
  `notifier`, `warehouse`). Shapes hammered out so the detectors have real
  signals to find: e.g. `reporting.summarize()` shares ~21 executed lines
  between its redundant pair while one variant adds a **unique public symbol**
  (`note_trace`) — that pair is the **sole-coverage veto** fixture.
- `tests/` — the decayed suite. See the module docstrings: each file names the
  category it demonstrates and the signal shape used.
- `conftest.py` — puts `prod` on `sys.path`.
- `.ci-history/runs.json` — deterministic 24-run history. `tools/make_ci_history.py`
  regenerates it byte-identically (§14.6): flaky tests follow a cadence
  (`i % 3 == 1`), zombie tests fail every run, and regressions fail one
  **contiguous** block of runs so the flaky detector's
  isolated-failure rule can tell them apart.
- `EXPECTED.json` — `test_id → {action, tier, vetoed, findings}` for all 45
  tests; the acceptance runner compares this against a fresh audit.

## The two §7 hard-veto fixtures

- `tests/test_smoke_vetoes.py::test_smoke_lowvalue_dup` — a genuine low-value
  duplicate, but **smoke tier** → removal downgraded to *Keep (flagged)*.
- `tests/test_sole_coverage.py::test_report_dup_with_footprint` — a redundant
  pair (Jaccard 0.95) where the flagged test is the **sole coverage of the
  public symbol** `prod.reporting.note_trace` → downgraded to *Keep*.

## Regenerating

```bash
python3 src/tests/fixtures/tools/make_ci_history.py   # byte-identical
python3 src/tests/run_acceptance.py                   # §14.1–8 + §12, 9/9
```

Acceptance copies these fixtures into a temporary directory; nothing the
runner writes (quarantine stubs, proposals, coverage caches) is committed.
