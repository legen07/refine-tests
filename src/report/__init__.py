"""Report emitters (§10): stable JSON + human-readable Markdown.

Both emitters are pure functions of `AuditResult` (no wall-clock, no
environment-dependent values) so that running audit twice yields
byte-identical reports (§14.6).
"""

from .json_emitter import emit_json
from .markdown_emitter import emit_markdown

__all__ = ["emit_json", "emit_markdown"]
