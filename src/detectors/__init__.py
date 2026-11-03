"""Detector package: language detectors (§11) and category detectors (§5).

The core engine (src/engine.py) consumes only the `Detector` protocol and the
category detector callables registered here. Adding a language means adding a
module under `languages/` and one line in `registry.py` — never a core edit.
"""

from .base import CategoryDetector, Detector
from . import registry

__all__ = ["CategoryDetector", "Detector", "registry"]
