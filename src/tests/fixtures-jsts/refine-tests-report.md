# refine-tests report

## 1. Run header

- Mode: **audit**
- Scope: full; tiers: smoke, core, extended
- Toolchain: languages detected javascript, typescript; per-test coverage: UNAVAILABLE (not run (JS/TS: per-test coverage requires a pre-built c8/coverage JSON — set [coverage] jsts_coverage_json; coverage-dependent detectors stay LOW-capped))
- CI source: local (no .ci-history/*.json found under the target root)
- Assumptions: see [ASSUMPTIONS.md](ASSUMPTIONS.md)

## 2. Executive summary

- Total tests analyzed: **8**
- By action: keep=6, strengthen=2

Counts by tier × category (findings):

| tier | brittle | existence-only | mock-heavy | redundant | zombie |
| --- | --- | --- | --- | --- | --- |
| extended | 1 | 1 | 1 | 2 | 1 |

- Quarantined (all states): 0
- Proposals staged: 0

## 3. Before / after

| test | tier | action | confidence | co-signals | evidence |
| --- | --- | --- | --- | --- | --- |
| `tests/calc.test.js::calc::adds numbers alpha` | extended | **keep** | 0.30 | 0 | redundant (0.30): Near-duplicate of tests/calc.test.js::calc::adds numbers beta: AST similarity 1.00… |
| `tests/calc.test.js::calc::adds numbers beta` | extended | **keep** | 0.30 | 0 | redundant (0.30): Near-duplicate of tests/calc.test.js::calc::adds numbers alpha: AST similarity 1.0… |
| `tests/calc.test.js::calc::divides two numbers` | extended | **keep** | — | — | no detector fired |
| `tests/calc.test.js::calc::legacy total still used` | extended | **keep** | 1.00 | 1 | zombie (1.00): References production symbol(s) absent from the symbol table: `src/calc.legacyTotal`. |
| `tests/calc.test.js::calc::sums to snapshot` | extended | **keep** | 0.65 | 1 | brittle (0.65): Asserts serialized internal state / large inline literal (1 snapshot-like assertion(… |
| `tests/mocked.test.js::mocked::divide by zero throws` | extended | **keep** | — | — | no detector fired |
| `tests/calc.test.js::calc::add is defined` | extended | **strengthen** | 0.60 | 1 | existence-only (0.60): Only existence/truthiness assertions present: `expect(add).toBeDefined()`. |
| `tests/mocked.test.js::mocked::mock only` | extended | **strengthen** | 0.70 | 1 | mock-heavy (0.70): 1 of 1 exercised calls resolve to mocks (bindings: add). |

## 4. Quarantine log

_No quarantined tests._

## 5. Proposed patches

_No proposals staged. In audit mode `proposed/` is never written (§9); run `refine-tests --cleanup --yes` to stage refine/strengthen patches and replace proposals._

## 6. Vetoed removals

_No vetoed removals._

## 7. "What would this catch?"

One sentence per retained/strengthened test naming the production bug it guards:

- `**tests/calc.test.js::calc::add is defined**`: Guards **the code under test**: if a future change regressed the code under test, the test would no longer hold the claim "toBeDefined".
- `**tests/calc.test.js::calc::adds numbers alpha**`: Guards **add**: if a future change regressed add, the test would no longer hold the claim "toBe".
- `**tests/calc.test.js::calc::adds numbers beta**`: Guards **add**: if a future change regressed add, the test would no longer hold the claim "toBe".
- `**tests/calc.test.js::calc::legacy total still used**`: Guards **legacyTotal**: if a future change regressed legacyTotal, the test would no longer hold the claim "toBe".
- `**tests/calc.test.js::calc::sums to snapshot**`: Guards **add**: if a future change regressed add, the test would no longer hold the claim "toMatchSnapshot".
- `**tests/mocked.test.js::mocked::mock only**`: Guards **add**: if a future change regressed add, the test would no longer hold the claim "toBe".
