"""conftest for the fixture suite: make `prod` importable.

This is the audited project's own conftest — part of the fixture dataset.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
