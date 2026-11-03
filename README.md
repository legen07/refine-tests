# refine-tests

Forensic audit + curation of a project's test suite — a production-ready,
strict extension of the test-suite-curation skill from
[`petrkindlmann/qa-skills`](https://github.com/petrkindlmann/qa-skills).

> **North star:** every retained or proposed test must answer *what real
> production bug would this catch?*

## What it does

- **Audits** your suite with 11 calibrated detectors (never-failing, flaky,
  redundant, zombie, low-value, tautological, mock-heavy, typecheck-only,
  existence-only, brittle, implementation-detail), combining AST structure,
  assertion skeletons, **per-test coverage**, CI history, and (opt-in)
  mutation probing.
- **Ranks and gates** every finding (§7 calibration, action rank
  Replace > Strengthen > Refine > Remove > Keep, hard vetoes for sole-covered
  public symbols and smoke-tier tests).
- **Cleans up without deleting anything**: gated removals are quarantined in
  place (line-number-preserving skip stubs + archived originals + evidence),
  improvements ship as proposals (patches / replacement documents).
  Approvals and restores are first-class commands.

## Install (zero-dependency Python)

The plugin is pure Python (3.10+); only the *audited project* needs pytest.

```bash
git clone <this repo> refine-tests && cd refine-tests
./refine-tests --help            # uses .venv if present, else python3
```

For the per-test coverage mechanism, create the plugin venv once
(equivalent to `refine-tests bootstrap`):

```bash
python3 -m venv .venv && .venv/bin/pip install pytest pytest-cov
```

## Quickstart (§17 usage examples)

```bash
# 1. Audit the whole repo — read-only, always exits 0.
refine-tests .
refine-tests --path "src/billing/**"          # scoped audit
refine-tests --diff origin/main..HEAD         # audit what the PR touches
refine-tests --offline                        # zero network calls

# 2. Review the findings.
$EDITOR refine-tests-report.md                # 7 sections, vetoes, proposals

# 3. Plan the cleanup (no writes), then apply.
refine-tests --cleanup --dry-run              # plan on stdout, exit 0
refine-tests --cleanup                        # refused: exit 2 (no writes)
refine-tests --cleanup --yes                  # quarantine/ + proposed/ only
refine-tests --cleanup --yes --approve <id>   # quarantine one action only

# 4. Human sign-off for permanent removal, or reverse a quarantine.
refine-tests approve qt-4f397b252e
refine-tests restore qt-4f397b252e
```

| Mode | Writes | Exit |
|---|---|---|
| audit (default) | reports only | 0 |
| `--cleanup` (no `--yes`) | nothing | 2 + diff preview |
| `--cleanup --dry-run` | nothing | 0 + diff preview |
| `--cleanup --yes` | `quarantine/`, `proposed/`, reports | 0 |

Feature flags: `--allow-replace-proposals` (P3, §12) and `--mutation` (P4)
are off by default; both are also configurable in `config.toml` /
`.refine-tests.toml`.

## Reports

- `refine-tests-report.md` — run configuration, before/after tables,
  quarantine log (with restore commands), proposals, vetoed removals, and the
  per-test "what would this catch" narrative.
- `refine-tests-report.json` — schema-pinned (`schema_version: 1.0.0`),
  stable, sorted; suitable for CI integration.

Reports are deterministic: no timestamps, no volatile hashes — two identical
audits are byte-identical.

## The fixture suite + acceptance (§14)

```bash
python3 src/tests/fixtures/tools/make_ci_history.py   # regenerate CI history
python3 src/tests/run_acceptance.py                   # §14.1–8, 9/9 checks
.venv/bin/python -m pytest src/tests                  # unit tests
```

The fixtures are a deliberately decayed demo project (45 tests) with a
synthetic 24-run CI history. Every one of the 11 detectors has ≥3 true
positives and ≥2 true negatives there, and `EXPECTED.json` pins the exact
classification matrix the acceptance runner verifies — including the two
§7 veto fixtures (sole coverage of a public symbol, smoke tier).

## Documentation

- **SKILL.md** — the skill definition (modes, detectors, gating, outputs).
- **ASSUMPTIONS.md** — §0 confidence protocol: every assumption we had to
  make (A1–A13), with the conservative fallback chosen instead.
- **config.toml** — every knob (toolchain, coverage, CI, mutation budgets,
  the §7 calibration table, gating thresholds, tiers, feature flags).
- **dsh-host-plugin/** — the real DSH (Cordis) Host half that registers
  this CLI as the `refine_tests` tool; the repo-root `package.json` is the
  installable bundle package (see ASSUMPTIONS A1).

## Install as a DSH plugin (Cordis bundle)

```bash
dsh plugin --profile <profile> add file:/home/joe/Dev/refine-tests
# or, manually in the profile directory:
#   pnpm add refine-tests file:/home/joe/Dev/refine-tests
```

The row carries no config — the CLI's own `config.toml` /
`.refine-tests.toml` stay the single source of truth. To uninstall:
`dsh plugin --profile <profile> remove refine-tests`.

**One-time bootstrap (only for installed copies):** the plugin's `.venv/`
(per-test coverage: pytest + pytest-cov + coverage) is gitignored and never
ships inside an installed copy, so run this once per install — it is the only
step that needs the network:

```bash
refine-tests bootstrap
# or through the tool:  refine_tests with mode=bootstrap
```

Without it the plugin still works — audits degrade honestly to
no-coverage mode (coverage-dependent detectors cap at LOW, §2).

## License

MIT — `petrkindlmann`
