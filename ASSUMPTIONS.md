# ASSUMPTIONS.md

Refine-tests is an implementation of (and strict extension to) the
test-suite-curation skill from `petrkindlmann/qa-skills`. §0 of that skill
demands: **never invent missing information silently — every assumption is
written down here, with the conservative fallback that was chosen instead.**

Each assumption is labeled `A#` and referenced from the code, the reports, and
`config.toml` (see `[assumptions]`). The plugin mirrors this list into every
report (`assumptions` field, §10.1) so a reviewer never has to open the repo
to audit our reasoning.

---

## A1 — `plugin.toml` is not a native DSH manifest (schema by spec §3)

**Missing information:** the harness (DeepSeek Harness / Cordis) has no
native `plugin.toml` plugin manifest format. Its real plugin system is
*Cordis bundles* (`package.json` with `dsh.bundle.patch` + `cordis.patch.yml`
inserting plugin rows; Host plugins exporting `apply(ctx, config)` with
`inject = ["tools"]`).

**Assumption:** ship the manifest in the schema given by the skill spec §3
(name, version, description, entrypoint, author, license, triggers,
default_mode, permissions, network, destructive) — it is a stable,
human-readable contract — and *additionally* ship a real, installable Cordis
Host bundle: the repo-root `package.json` is the bundle package (`dsh.bundle.patch`
→ `dsh-host-plugin/cordis.patch.yml`, exports delegating to
`dsh-host-plugin/index.js`), so `dsh plugin --profile <p> add
file:/…/refine-tests` installs it; the row's `name` equals the dependency key
because that is the specifier the Loader imports.

**Conservative fallback:** audit mode by default; destructive actions
(quarantine) require `--cleanup --yes`; the wrapper tool shells out to the
CLI and never performs writes itself unless the caller passes the same flags.

## A2 — "never-failing" reads as *zero failures* over ≥20 runs (§7)

**Missing information:** the §7 calibration row for the never-failing
detector is worded as "0 passes over ≥20 runs", while the detector category
is *never-failing* and every other row is phrased in terms of the claim
failing.

**Assumption:** the row means **0 failures** over ≥20 runs (a test that has
never failed), base confidence 0.3, +0.4 with ≥20 runs observed
→ 0.7 — exactly as implemented in `src/detectors/never_failing.py`.

**Conservative fallback:** a test with no CI history gets *no* finding (not a
low-confidence one); the detector requires the history to exist at all.

## A3 — JS/TS support is structural-only (per-test coverage needs an adapter)

**Missing information:** the environment contracts (§2) assume vitest/jest +
c8 + Stryker; no JS/TS toolchain is installed here, and c8's per-test context
format is project-specific.

**Assumption:** the JS/TS detector (`src/detectors/jsts.py`) does full
structural analysis (describe/it/test scanning, export symbol table, expect
matcher shapes, vi.fn/jest.fn mocks) but consumes per-test coverage only when
the user points `coverage.jsts_coverage_json` at a pre-built c8/coverage
document, and the mutation probe reports `over_budget` (Stryker is out of
scope for v1, §15 P4).

**Conservative fallback:** with no JS/TS coverage document, every detector
that needs coverage fingerprints refuses findings above LOW (§2 LOW cap).

## A4 — "the typechecker already enforces it" is an AST-level approximation

**Missing information:** whether the audited project actually runs mypy or
pyright in CI is unknown to the audit (and typecheckers do not prove
annotations at runtime).

**Assumption:** the typecheck-only detector treats `assert isinstance(f(x), T)`
as annotation-enforced when `f`'s *return annotation* equals the asserted
type literal (resolved through the symbol table). When the annotation cannot
be resolved, the finding is still reported but at reduced confidence (0.4, vs
0.6 when matched) — see `src/detectors/typecheck_only.py`.

**Conservative fallback:** never remove on typecheck-only evidence; the
primary action is *strengthen* (make the assertion observe behavior).

## A5 — branch contexts are unavailable per test (coverage.py limitation)

**Missing information:** coverage.py records dynamic contexts per *line*,
not per branch, so "no unique branch" cannot be verified per test from the
coverage JSON.

**Assumption:** the low-value detector compares line fingerprints (strict
subset) and treats the unique-branch check as a no-op for Python; the
"unique branch" guard therefore lives in the assertion-skeleton check (no
unique assertion ⇒ no unique claim).

**Conservative fallback:** low-value still requires coverage availability
plus skeleton subsequence, and removal still needs the §7 co-signal gate.

## A6 — Replace gate: "two detectors agree" (§7) read as two *findings*

**Missing information:** §7 gates Replace behind co-signals without saying
whether the second signal must come from a different detector or may repeat.

**Assumption:** Replace requires **≥2 findings at ≥ `cosignal_min_confidence`
(0.5)** from the test, **at least one of which is replace-primary**
(`zombie` or `implementation-detail`), **and** tier `core` or `extended`.

**Conservative fallback:** Replace is a *proposal only* in v1 (§12) and is
disabled by default behind `features.replace`; even then it writes
`proposed/<id>.replacement.md`, never an edit.

## A7 — `--yes` reconciles §8 (explicit per-action approval) with §14.4

**Missing information:** §8 describes per-action `--approve <id>` sign-off,
while §14.4 requires `--cleanup --yes` to quarantine the gated removals.

**Assumption:** `--yes` is explicit informed consent for **every gated
removal in this run** (the audit report the user just read names them all);
`--approve <id>` / `--approve-all` remain for narrower consent. Cleanup
without `--yes` never writes (exit 2 with the diff preview); `--cleanup
--dry-run` exits 0 with the identical plan.

**Conservative fallback:** quarantined tests are never deleted — originals
are archived in `quarantine/<id>/original.py` and `refine-tests restore <id>`
puts them back (§8).

## A8 — mutation sampling determinism (§14.6) uses a content-derived seed

**Missing information:** mutation budgets (§2: 30 min / 5 mutants) force a
*sample* of mutation points; the sampling must be deterministic across runs.

**Assumption:** the sample is drawn with `random.Random(sha1(<test id> +
<sorted covered lines>))` — content-derived, never `hash()` (which is
PYTHONHASHSEED-randomized), never wall-clock.

**Conservative fallback:** the probe is disabled by default
(`features.mutation = false`, §15 P4) and refuses to start when the test
count exceeds `mutation.max_tests_cap` (§13 hard cap).

## A9 — reports omit timestamps entirely (determinism over freshness)

**Missing information:** §10 asks for stable reports; a generation timestamp
would make two identical runs differ byte-for-byte.

**Assumption:** the Markdown and JSON reports contain **no timestamps and no
hashes of volatile state**; run identity comes from the target path, mode,
and feature flags. Freshness is the caller's responsibility (git).

**Conservative fallback:** the quarantined-at / approved-at fields that the
*protocol* needs (§8 evidence) live only in `quarantine/<id>/evidence.json`,
never in the reports.

## A10 — refine/strengthen proposals are ON by default; replace and mutation OFF

**Missing information:** §15's phase list (P0–P4) does not name default
feature states for shipped plugins.

**Assumption:** P0+P1 (audit, refine/strengthen proposals) ship enabled;
P3 (replace proposals) and P4 (mutation probe) ship behind flags
(`features.replace`, `features.mutation`), each documented in `config.toml`.

**Conservative fallback:** every phase can be disabled from
`.refine-tests.toml` in the audited project; flags cannot re-enable
destructive behavior without `--cleanup --yes`.

## A11 — mock setup calls are excluded from the mock-fraction denominator

**Missing information:** §5.7 defines mock-heavy as "≥80% of exercised calls
resolve to mocks" without naming whether mock construction counts as
exercise.

**Assumption:** `Mock()`/`MagicMock()` construction and `patch(...)` setup
are **not** exercised calls (they verify nothing); only behavioral calls
count, including calls through patch targets. A test whose every behavioral
call is a mock is *mock-only* even when its assertions read through local
variables.

**Conservative fallback:** a test with zero behavioral calls gets no
mock-heavy finding at all (no division by zero, no invented fraction).

## A12 — approval permanently removes the stub (line numbers in that file shift)

**Missing information:** §8 requires the quarantine *stub* to preserve line
numbers, but says nothing about the post-approval file shape.

**Assumption:** approving a quarantine record permanently deletes the stub
(the original stays archived verbatim); the file's line count then changes,
which is the documented cost of permanent removal. Un-approved stubs never
shift line numbers.

**Conservative fallback:** approval is the *only* path that removes lines,
and it requires a human sign-off (`refine-tests approve <id>`) recorded in
`evidence.json` with git identity.

## A13 — local CI history is a first-class provider (offline by default)

**Missing information:** §2 lists GitHub/GitLab providers; networks in this
environment are restricted.

**Assumption:** `.ci-history/runs.json` is the third provider (schema in
`src/ci.py`), and it is the *default* when no provider is configured;
`--offline` hard-overrides any network provider to local.

**Conservative fallback:** missing or partial history disables the
history-dependent detectors (never-failing, flaky, churn signals) rather
than degrading their confidences.
