"""Production module: report building (fixture prod code).

`summarize` is intentionally longer than `footnote` so the sole-coverage veto
fixture (§7) can build a coverage fingerprint that overlaps another test at
Jaccard ≥ 0.9 while still being the *only* test footing `footnote`.
"""

import logging

TRACE_LOG: list = []


def note_trace(total: int, count: int) -> None:
    """Record a trace entry for a summarized batch (public symbol)."""
    TRACE_LOG.append((total, count))


def build_report(rows: list) -> dict:
    """Build a summary report dict for the given rows."""
    totals = {"total": 0, "row_count": 0, "currency": "USD",
              "rounding": 2, "note": ""}
    for row in rows:
        totals["total"] += row.get("amount", 0)
        totals["row_count"] += 1
    return totals


def summarize(amounts: list, trace: bool = False) -> str:
    """Summarize a list of amounts as a total string.

    This function is intentionally ~20 executed statements long so that the
    sole-coverage fixture (§7) works: one fixture test calls it with
    ``trace=True``, which adds exactly one unique covered line (the body of
    `note_trace`) on top of ~21 shared ones — putting the pair's coverage
    Jaccard at ≥ 0.9 (redundant) while `note_trace` is covered by that one
    test alone (sole-coverage veto).
    """
    total = 0
    count = 0
    smallest = None
    largest = None
    negatives = 0
    zeros = 0
    for amount in amounts:
        if amount < 0:
            negatives += 1
            continue
        if amount == 0:
            zeros += 1
            continue
        total += amount
        count += 1
        if smallest is None or amount < smallest:
            smallest = amount
        if largest is None or amount > largest:
            largest = amount
    label = "total"
    mean = round(total / count, 2) if count else 0
    rounded = int(mean)
    span = (largest - smallest) if (largest is not None and
                                    smallest is not None) else 0
    midpoint = span / 2 if span else 0
    density = round(count / (count + zeros), 2) if (count + zeros) else 0
    if trace:
        note_trace(total, count)
    return f"{label}: {total}"


def footnote(amounts: list) -> str:
    return f"{len(amounts)} rows"


def emit_log(msg: str) -> str:
    logging.getLogger("prod.reporting").info("event=%s", msg)
    return msg
