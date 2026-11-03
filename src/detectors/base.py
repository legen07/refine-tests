"""Detector contracts (§11).

Two forces shape this file:

* `Detector` — the language-extension interface. Any new language MUST
  implement it; the core engine never hardcodes a language.
* `CategoryDetector` — the §5 detection categories, expressed as a pure
  function over normalized structures so they stay language-agnostic.

Everything here is a `typing.Protocol`/`Callable`, not a base class with
behavior: detectors stay free-standing modules (§3: "one module per detection
category").
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from ..models import (
    CiHistory,
    CoverageSet,
    Finding,
    MutationResult,
    Mutant,
    SymbolTable,
    TestNode,
)


# Input bundle handed to every category detector. Adding a field is
# backwards-compatible; detectors read what they need.
class DetectorContext(dict):
    """Mapping with attribute access; carries the shared audit evidence."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


# A category detector: (tests, ctx) -> findings. Pure; no I/O, no randomness,
# no wall-clock — this is what makes repeated audit runs byte-identical (§14.6).
CategoryDetector = Callable[[list[TestNode], DetectorContext], list[Finding]]


@runtime_checkable
class Detector(Protocol):
    """Language-extension interface (§11).

    `name` and `category` are informational; the engine drives
    `enumerate_tests`, and the optional probes are used by category detectors
    through the shared context.
    """

    name: str
    language: str

    def enumerate_tests(self, root) -> list[TestNode]: ...

    def parse_ast(self, path): ...

    def coverage_fingerprint(self, test_id: str) -> CoverageSet: ...

    def symbol_table(self, root) -> SymbolTable: ...

    def run_mutation_probe(
        self, test_id: str, mutants: list[Mutant]
    ) -> MutationResult: ...


__all__ = [
    "CategoryDetector",
    "DetectorContext",
    "Detector",
    "TestNode",
    "CoverageSet",
    "SymbolTable",
    "CiHistory",
    "Finding",
    "Mutant",
    "MutationResult",
]
