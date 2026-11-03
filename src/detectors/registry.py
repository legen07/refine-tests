"""Language + category registration (§11).

The core engine imports only this module. Adding a language means implementing
a `Detector` and adding one entry to `LANGUAGE_DETECTORS` — no core edit.
The category list is the §5 table itself; every module exports
`__detector__` so the registry can validate completeness at import time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import (
    brittle,
    existence_only,
    flaky,
    implementation_detail,
    low_value,
    mock_heavy,
    never_failing,
    redundant,
    tautological,
    typecheck_only,
    zombie,
)
from .base import CategoryDetector, DetectorContext
from .jsts import JsTsDetector
from .python import PythonDetector

# Language -> detector factory. Factories take (root, cfg).
LANGUAGE_DETECTORS: dict[str, Any] = {
    "python": PythonDetector,
    "javascript": JsTsDetector,
    "typescript": JsTsDetector,   # vitest/jest structural support (A3)
}

# §5 categories, in spec order. One module per category (§3).
CATEGORY_MODULES: dict[str, Any] = {
    "never-failing": never_failing,
    "flaky": flaky,
    "redundant": redundant,
    "zombie": zombie,
    "low-value": low_value,
    "tautological": tautological,
    "mock-heavy": mock_heavy,
    "typecheck-only": typecheck_only,
    "existence-only": existence_only,
    "brittle": brittle,
    "implementation-detail": implementation_detail,
}


def category_detectors() -> list[tuple[str, Callable]]:
    """(category, detector) pairs; validates every module's contract."""
    out: list[tuple[str, Callable]] = []
    for category, module in CATEGORY_MODULES.items():
        fn = getattr(module, "__detector__", None)
        if fn is None:
            raise RuntimeError(
                f"category detector module {module.__name__} must export "
                f"`__detector__`")
        out.append((category, fn))
    return out


def language_detector(language: str, root: Path,
                      cfg: dict[str, Any] | None = None):
    factory = LANGUAGE_DETECTORS.get(language)
    if factory is None:
        raise KeyError(
            f"no Detector implementation for language {language!r} (§13: "
            f"languages without a Detector are unsupported; registered: "
            f"{sorted(LANGUAGE_DETECTORS)})")
    return factory(root, cfg)


def registered_languages() -> list[str]:
    return sorted(LANGUAGE_DETECTORS)


__all__ = [
    "CategoryDetector",
    "DetectorContext",
    "LANGUAGE_DETECTORS",
    "CATEGORY_MODULES",
    "category_detectors",
    "language_detector",
    "registered_languages",
]
