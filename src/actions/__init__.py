"""Actions (§6): remove / refine / strengthen / replace.

Rank order when multiple apply (§6): Replace > Strengthen > Refine > Remove >
Keep. Refine/Strengthen/Replace emit proposals under `proposed/`; Remove never
deletes (§8) — it archives the test in `quarantine/` and stubs it in place.
"""

from .quarantine import (
    QuarantineStore,
    make_quarantine_id,
)

__all__ = ["QuarantineStore", "make_quarantine_id"]
