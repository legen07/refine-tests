"""Unit tests for the plugin manifest contract (§3, ASSUMPTIONS A1)."""

import pytest

from src.plugin_loader import ManifestError, identity, load


def test_manifest_loads_and_identity_reads_the_plugin_table():
    data = load()
    assert data["plugin"]["name"] == "refine-tests"
    ident = identity()
    assert ident["name"] == "refine-tests"
    assert ident["entrypoint"] == "src.cli:main"


def test_manifest_declares_audit_as_the_default_mode():
    triggers = load()["triggers"]
    assert triggers["default_mode"] == "audit"
    assert "refine-tests" in triggers["commands"]


def test_manifest_declares_quarantine_only_destructive_permission():
    perms = load()["permissions"]
    assert perms["destructive"] == "quarantine-then-approve"
    assert "quarantine/**" in perms["write"]


def _write(tmp_path, body: str):
    p = tmp_path / "plugin.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_manifest_is_a_hard_error(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load(tmp_path / "nope.toml")


def test_missing_required_key_is_rejected(tmp_path):
    p = _write(tmp_path, '[plugin]\nname = "x"\n')
    with pytest.raises(ManifestError, match="must be non-empty"):
        load(p)


def test_non_audit_default_mode_is_rejected(tmp_path):
    p = _write(tmp_path, """
[plugin]
name = "x"
version = "1"
description = "d"
entrypoint = "e"
author = "a"
license = "MIT"

[triggers]
default_mode = "cleanup"
""")
    with pytest.raises(ManifestError, match="must be 'audit'"):
        load(p)


def test_unrestricted_destructive_permission_is_rejected(tmp_path):
    p = _write(tmp_path, """
[plugin]
name = "x"
version = "1"
description = "d"
entrypoint = "e"
author = "a"
license = "MIT"

[permissions]
destructive = "delete"
""")
    with pytest.raises(ManifestError, match="quarantine-then-approve"):
        load(p)
