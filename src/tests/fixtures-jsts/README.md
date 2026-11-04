# JS/TS fixture suite (structural)

A deliberately-decayed JavaScript test suite for the regression check of the
JS/TS detector path (`src/detectors/jsts.py`). It exists because that path
was silently broken for months — `it("t", () => {...})` arrow callbacks were
never enumerated — and nothing in the Python-only fixtures would have caught
it.

Decay planted per file:

- `tests/calc.test.js` (all under `describe("calc")`)
  - `adds numbers alpha` / `adds numbers beta` — mutually redundant
    (identical claims; only the binding name differs).
  - `adds numbers organic` — the discriminator: same shape as the pair but a
    different claim (`toBe(5)`), so it must NOT be flagged.
  - `add is defined` — existence-only assertion.
  - `legacy total still used` — imports and references `legacyTotal`, which
    `src/calc.js` no longer exports (zombie).
  - `sums to snapshot` — snapshot pinning (brittle).
- `tests/mocked.test.js`
  - `mock only` — every exercised call resolves to a `vi.fn` (mock-heavy).
  - `divide by zero throws` — organic behavior check (no findings).

Per A3, JS/TS analysis is structural-only: no per-test coverage is run, so
coverage-dependent detectors (redundant, low-value, never-failing) cap at
LOW confidence and removal gates stay closed. `EXPECTED.json` pins exactly
that outcome.
