"""Loader for the plugin manifest (`plugin.toml`, spec §3).

The manifest is the plugin's declared contract — name, version, entrypoint,
triggers, permissions. `src/cli.py` reads it through this loader so the CLI's
self-description (`--version`, `--help`) can never drift from the manifest,
and `validate()` fails loudly if a required key is missing or a safety
invariant (default_mode must be `audit`; cleanup must stay opt-in, §9) is
violated.

See ASSUMPTIONS.md A1 for why the schema follows the skill spec rather than
the Cordis bundle format (the Cordis wrapper lives in `dsh-host-plugin/`).
"""

from pathlib import Path
from typing import Any

import tomllib

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "plugin.toml"

REQUIRED = ("name", "version", "description", "entrypoint", "author",
            "license")


class ManifestError(Exception):
    """The plugin.toml contract is broken — refuse to run (§0 conservative)."""


def load(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else MANIFEST_PATH
    if not p.exists():
        raise ManifestError(f"plugin manifest not found: {p}")
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{p}: malformed TOML: {exc}") from exc

    plugin = data.get("plugin")
    if not isinstance(plugin, dict):
        raise ManifestError(f"{p}: missing [plugin] table")
    for key in REQUIRED:
        if not plugin.get(key):
            raise ManifestError(f"{p}: [plugin] {key} must be non-empty")

    triggers = data.get("triggers", {})
    if triggers.get("default_mode", "audit") != "audit":
        # §9: audit is the default mode; anything destructive is opt-in. A
        # manifest that declares otherwise is a contract violation.
        raise ManifestError(
            f"{p}: triggers.default_mode must be 'audit' (got "
            f"{triggers.get('default_mode')!r}); cleanup is opt-in (§9)")

    perms = data.get("permissions", {})
    if perms.get("destructive") not in (None, "quarantine-then-approve"):
        raise ManifestError(
            f"{p}: permissions.destructive must be 'quarantine-then-approve' "
            "(§8: never delete; quarantine + human sign-off)")

    data["plugin"] = plugin
    data.setdefault("triggers", triggers)
    data.setdefault("permissions", perms)
    return data


def identity(path: Path | None = None) -> dict[str, str]:
    """The `[plugin]` table alone (name/version/description/entrypoint)."""
    return dict(load(path)["plugin"])
