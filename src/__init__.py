"""refine-tests — forensic audit + curation of a project's test suite.

Package layout (see plugin.toml / README.md):

    src.cli         — entrypoint (`refine-tests`, `rt`)
    src.engine      — audit/cleanup orchestration (§9 modes)
    src.gating      — confidence calibration, gates, vetoes (§7)
    src.tiering     — risk-based smoke | core | extended tiering
    src.ci          — CI history mining (§5.1/§5.2)
    src.coverage_runner — per-test coverage mechanism (§2)
    src.detectors/  — language detectors (§11) + one module per category (§5)
    src.actions/    — remove / refine / strengthen / replace (§6, §8, §12)
    src.report/     — JSON + Markdown emitters (§10)
"""

__version__ = "0.1.0"
