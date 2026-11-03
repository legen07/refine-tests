# SKILL.md — refine-tests

> **North star:** every retained or proposed test must answer *what real
> production bug would this catch?* A test that cannot name one is forensic
> evidence of decay, not protection.

## What this skill does

`refine-tests` audits a project's test suite and produces a ranked, gated,
fully-evidenced cleanup plan — without deleting anything you did not approve:

1. **Forensic audit (read-only, default)**: enumerates every test through a
   language Detector, fingerprints each one (AST structure, assertion
   skeleton, per-test coverage, CI history), and runs **11 detectors**
   (never-failing, flaky, redundant, zombie, low-value, tautological,
   mock-heavy, typecheck-only, existence-only, brittle, implementation-detail).
2. **Gating (§7)**: each finding gets a calibrated confidence; actions rank
   **Replace > Strengthen > Refine > Remove > Keep**, with hard vetoes
   (sole coverage of a public symbol, smoke tier) that downgrade removals to
   Keep (flagged).
3. **Cleanup (opt-in)**: gated *removals* are quarantined in place
   (`quarantine/<id>/original.py` + `evidence.json`, line-number-preserving
   skip stubs); refine/strengthen/replace become **proposals** under
   `proposed/` (unified patches / replacement documents). Nothing is ever
   deleted without human sign-off (`refine-tests approve <id>`), and
   everything is reversible (`refine-tests restore <id>`).

## Deliverable tree (spec §3)

```
refine-tests/
├── package.json             # the DSH/Cordis bundle package (dsh.bundle.patch)
├── plugin.toml              # manifest (spec §3 schema; see ASSUMPTIONS A1)
├── SKILL.md                 # this file
├── README.md                # install + quickstart
├── ASSUMPTIONS.md           # §0: every assumption, labeled A1–A13
├── config.toml              # toolchain, coverage, CI, mutation, gating, tiers
├── refine-tests             # zero-install POSIX launcher
├── dsh-host-plugin/         # Cordis Host half: index.js + cordis.patch.yml
│                            #   (tools.refine_tests; see package.json at the root)
├── quarantine/              # created on first cleanup (§8 protocol)
├── proposed/                # created on first cleanup (§6 proposals)
└── src/
    ├── cli.py               # entrypoint (§9 modes, §17 usage)
    ├── engine.py            # the audit pipeline + cleanup plan/apply
    ├── models.py            # normalized structures (§11 protocol)
    ├── coverage_runner.py   # per-test coverage mechanism (§2)
    ├── ci.py                # CI history: GitHub / GitLab / local
    ├── tiering.py           # smoke | core | extended
    ├── gating.py            # §7 calibration, rank, vetoes
    ├── mutators.py          # P4 mutation probe (budgeted, deterministic)
    ├── narrative.py         # "what would this catch" sentences
    ├── detectors/           # 11 category detectors + Python/JS-TS Detectors
    ├── actions/             # quarantine protocol + proposals (§8, §12)
    ├── report/              # markdown + JSON emitters (§10)
    └── tests/               # unit tests, fixture suite, acceptance runner
```

## Modes (§9)

| Mode                              | Command                                  | Writes                | Exit  |
|-----------------------------------|------------------------------------------|-----------------------|-------|
| Audit (default, read-only)        | `refine-tests [target]`                  | reports only          | 0     |
| Scoped audit                      | `refine-tests --path "src/billing/**"`   | reports only          | 0     |
| Scoped audit (diff)               | `refine-tests --diff origin/main..HEAD`  | reports only          | 0     |
| Cleanup plan (no writes)          | `refine-tests --cleanup`                 | —                     | 2     |
| Cleanup plan (dry run)            | `refine-tests --cleanup --dry-run`       | —                     | 0     |
| Cleanup (apply)                   | `refine-tests --cleanup --yes`           | quarantine/ + proposed/ | 0   |
| Approve permanent removal (§8)    | `refine-tests approve <qt-id>`           | quarantine evidence   | 0     |
| Reverse a quarantine (§8)         | `refine-tests restore <qt-id>`           | test file + archive   | 0     |
| Bootstrap plugin venv (one-time)  | `refine-tests bootstrap`                 | `.venv/` (network)    | 0     |

Scope defaults to **smoke + core** tiers (pass `--all-tiers` to include
`extended`). `--offline` makes zero network calls (local `.ci-history/`
becomes the CI provider).

## Detectors (§5) and their primary remediation

| # | Detector            | Primary claim                                            | Action        |
|---|---------------------|----------------------------------------------------------|---------------|
| 1 | never-failing       | 0 failures over ≥20 CI runs (A2)                         | Remove (co-gated) |
| 2 | flaky               | interleaved pass/fail (isolated failures)                | Refine        |
| 3 | redundant           | AST ≥0.9 + skeleton subseq + coverage Jaccard ≥0.9       | Remove        |
| 4 | zombie              | references symbols absent from production                | Replace       |
| 5 | low-value           | coverage ⊂ another test, no unique assertion             | Remove        |
| 6 | tautological        | asserts constants / self-assigned / configured mocks     | Strengthen    |
| 7 | mock-heavy          | ≥80% of exercised calls are mocks (A11)                  | Strengthen    |
| 8 | typecheck-only      | isinstance the typechecker already enforces (A4)         | Strengthen    |
| 9 | existence-only      | only `is not None` / truthiness                          | Strengthen    |
| 10| brittle             | snapshots, call-order pins, exact log strings + churn    | Refine        |
| 11| implementation-detail| private methods/internals/`__dict__`                     | Replace       |

## Gating (§7)

- Confidence = base + Σ calibration signal deltas (9-row table in
  `config.toml` `[calibration]`), clamped to [0, 1]; any required mechanism
  unavailable ⇒ capped at LOW (§2).
- **Remove**: ≥2 remove-category findings at ≥ `cosignal_min_confidence`
  (0.5), or one at ≥0.9 with mutation score 0.
- **Refine**: ≥0.7 · **Strengthen**: ≥0.6 · **Replace**: A6 gate (≥2 findings
  at ≥0.5, ≥1 replace-primary, tier core/extended).
- **Hard vetoes** (downgrade to Keep, flagged): sole coverage of a public
  symbol; tier `smoke`.
- **P4 mutation probe**: opt-in (`features.mutation`); content-derived
  deterministic sampling (A8), wall-clock + mutant budgets from `config.toml`.

## Outputs (§10)

`refine-tests-report.md` (7 sections) + `refine-tests-report.json`
(schema-pinned `1.0.0`): run configuration, before/after summary tables, the
quarantine log (with restore commands), proposals, vetoed removals, and the
"what would this catch" narrative (§16 north star). Reports are deterministic
— no timestamps, no volatile hashes (A9) — so two identical audits are
byte-identical (§14.6).

## Confidence protocol (§0)

Missing information is never invented: every interpretation we had to make is
recorded in **ASSUMPTIONS.md** (A1–A13) and mirrored into every report.
When a mechanism is missing (no pytest, no CI history, no coverage contexts),
the dependent detectors degrade conservatively — LOW-capped or silent — and
the report says exactly why.
