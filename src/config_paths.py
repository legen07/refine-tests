"""Shared filesystem locations (kept import-light; no config parsing here)."""

from __future__ import annotations

from pathlib import Path


def plugin_root() -> Path:
    """The directory holding plugin.toml (this checkout's root)."""
    return Path(__file__).resolve().parent.parent
