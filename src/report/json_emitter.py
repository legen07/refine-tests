"""Machine-readable report (§10.1): stable schema, sorted output."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import AuditResult

SCHEMA_VERSION = "1.0.0"


def build_document(result: AuditResult) -> dict:
    by_tier: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for decision in result.decisions:
        by_tier[decision.tier] = by_tier.get(decision.tier, 0) + 1
        for finding in decision.findings:
            by_category[finding.category] = by_category.get(
                finding.category, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "run": result.run,
        "summary": {
            "total_tests": result.summary.get("total_tests", 0),
            "by_action": result.by_action(),
            "by_tier": dict(sorted(by_tier.items())),
            "by_category": dict(sorted(by_category.items())),
            "quarantined": len(result.quarantine),
            "proposals": len(result.proposals),
        },
        "tests": [d.to_dict() for d in
                  sorted(result.decisions, key=lambda d: d.test_id)],
        "quarantine": [q.to_dict() for q in
                       sorted(result.quarantine, key=lambda q: q.id)],
        "proposals": sorted(result.proposals,
                            key=lambda p: (p.get("kind", ""), p.get("id", ""))),
        "what_would_this_catch": result.summary.get("what_would_this_catch", []),
        "assumptions": result.assumptions,
    }


def emit_json(result: AuditResult, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_document(result), indent=2, sort_keys=True,
                   ensure_ascii=False) + "\n",
        encoding="utf-8")
    return path
