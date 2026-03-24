"""Integration tests for c3 utility functions, config loading, env vars, parsing, and manifest merging."""

import json
import os

import pytest
from pathlib import Path

from c3.agent import (
    _env,
    _read_json,
    _load_manifest,
    _parse_resource_uri,
    _merge_manifests,
    _ensure_safe_app_json,
    _scaffold_app,
    _ensure_mcp_json,
    _safe_app_json,
    _fetch_content,
    find_app_content,
    pick,
    parse_duration,
    AccessPolicy,
    AppConfig,
    AppManifest,
    HostConfig,
)

pytestmark = pytest.mark.asyncio(mode="auto")


# ---------------------------------------------------------------------------
# 1-7  _env
# ---------------------------------------------------------------------------

def test_env_returns_default_when_not_set():
    key = "TEST_INTEG_NOTSET_XYZ"
    os.environ.pop(f"C3_{key}", None)
    assert _env(key, 42) == 42


def test_env_coerces_string_to_int():
    key = "TEST_INTEG_INT"
    os.environ[f"C3_{key.upper()}"] = "123"
    try:
        assert _env(key, 0) == 123
    finally:
        os.environ.pop(f"C3_{key.upper()}", None)


def test_env_returns_default_on_bad_int():
    key = "TEST_INTEG_BADINT"
    os.environ[f"C3_{key.upper()}"] = "not_a_number"
    try:
        assert _env(key, 99) == 99
    finally:
        os.environ.pop(f"C3_{key.upper()}", None)


def test_env_bool_true():
    key = "TEST_INTEG_BTRUE"
    os.environ[f"C3_{key.upper()}"] = "true"
    try:
        assert _env(key, False) is True
    finally:
        os.environ.pop(f"C3_{key.upper()}", None)


def test_env_bool_false():
    key = "TEST_INTEG_BFALSE"
    os.environ[f"C3_{key.upper()}"] = "false"
    try:
        assert _env(key, True) is False
    finally:
        os.environ.pop(f"C3_{key.upper()}", None)


def test_env_bool_1():
    key = "TEST_INTEG_B1"
    os.environ[f"C3_{key.upper()}"] = "1"
    try:
        assert _env(key, False) is True
    finally:
        os.environ.pop(f"C3_{key.upper()}", None)


def test_env_bool_0():
    key = "TEST_INTEG_B0"
    os.environ[f"C3_{key.upper()}"] = "0"
    try:
        assert _env(key, True) is False
    finally:
        os.environ.pop(f"C3_{key.upper()}", None)


# ---------------------------------------------------------------------------
# 8-10  pick
# ---------------------------------------------------------------------------

def test_pick_returns_first_matching_key():
    d = {"a": 1, "b": 2}
    assert pick(d, "a", "b") == 1


def test_pick_returns_none_when_no_keys_match():
    d = {"a": 1}
    assert pick(d, "x", "y") is None


def test_pick_skips_none_values():
    d = {"a": None, "b": 5}
    assert pick(d, "a", "b") == 5


# ---------------------------------------------------------------------------
# 11-15  parse_duration
# ---------------------------------------------------------------------------

def test_parse_duration_int_string():
    assert parse_duration("60", 0) == 60


def test_parse_duration_5m():
    assert parse_duration("5m", 0) == 300


def test_parse_duration_2h():
    # "2h" is not supported by the regex (only s/m), so falls back to default
    assert parse_duration("2h", 99) == 99


def test_parse_duration_none_returns_default():
    assert parse_duration(None, 42) == 42


def test_parse_duration_invalid_returns_default():
    assert parse_duration("abc", 77) == 77


# ---------------------------------------------------------------------------
# 16-18  _read_json
# ---------------------------------------------------------------------------

def test_read_json_valid(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"key": "val"}))
    assert _read_json(p) == {"key": "val"}


def test_read_json_missing(tmp_path):
    assert _read_json(tmp_path / "nope.json") == {}


def test_read_json_invalid(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json!!")
    assert _read_json(p) == {}


# ---------------------------------------------------------------------------
# 19-21  _load_manifest
# ---------------------------------------------------------------------------

def test_load_manifest_prefers_app_json(tmp_path):
    (tmp_path / "app.json").write_text(json.dumps({"name": "app"}))
    (tmp_path / "agent.json").write_text(json.dumps({"name": "agent"}))
    assert _load_manifest(tmp_path)["name"] == "app"


def test_load_manifest_falls_back_to_agent_json(tmp_path):
    (tmp_path / "agent.json").write_text(json.dumps({"name": "agent"}))
    assert _load_manifest(tmp_path)["name"] == "agent"


def test_load_manifest_returns_empty_for_empty_dir(tmp_path):
    assert _load_manifest(tmp_path) == {}


# ---------------------------------------------------------------------------
# 22-24  _parse_resource_uri
# ---------------------------------------------------------------------------

def test_parse_resource_uri_normal():
    scheme, parts = _parse_resource_uri("c3://memory/myapp")
    assert scheme == "memory"
    assert parts == ["myapp"]


def test_parse_resource_uri_deep():
    scheme, parts = _parse_resource_uri("c3://memory/myapp/entity")
    assert scheme == "memory"
    assert parts == ["myapp", "entity"]


def test_parse_resource_uri_empty_scheme():
    scheme, parts = _parse_resource_uri("c3://")
    assert scheme == ""
    assert parts == []


# ---------------------------------------------------------------------------
# 25-26  _merge_manifests
# ---------------------------------------------------------------------------

def test_merge_manifests_merges_dm_roles():
    extra = {"name": "test", "access": {"dm": ["everyone"], "group": [], "commands": {}}}
    result = _merge_manifests([extra])
    assert "everyone" in result.access.dm
    # default also has "hosts"
    assert "hosts" in result.access.dm


def test_merge_manifests_merges_commands():
    extra = {
        "name": "test",
        "access": {"dm": [], "group": [], "commands": {"/custom": ["admins"]}},
    }
    result = _merge_manifests([extra])
    assert "/custom" in result.access.commands
    assert "admins" in result.access.commands["/custom"]
    # built-in commands should still be present
    assert "/start" in result.access.commands


# ---------------------------------------------------------------------------
# 27  _ensure_safe_app_json
# ---------------------------------------------------------------------------

def test_ensure_safe_app_json_creates_file(tmp_path):
    _ensure_safe_app_json(tmp_path, "demo")
    p = tmp_path / "app.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["name"] == "demo"
    assert data["sandboxed"] is True


# ---------------------------------------------------------------------------
# 28  _scaffold_app
# ---------------------------------------------------------------------------

def test_scaffold_app_creates_skills_dir_and_claude_md(tmp_path):
    dest = tmp_path / "mybot"
    dest.mkdir()
    _scaffold_app(dest)
    assert (dest / "CLAUDE.md").exists()
    assert (dest / "skills").is_dir()
    assert (dest / "app.json").exists()


# ---------------------------------------------------------------------------
# 29  _ensure_mcp_json
# ---------------------------------------------------------------------------

def test_ensure_mcp_json_preserves_existing_servers(tmp_path):
    mf = tmp_path / ".mcp.json"
    mf.write_text(json.dumps({"mcpServers": {"other": {"command": "other-cmd"}}}))
    _ensure_mcp_json(tmp_path)
    data = json.loads(mf.read_text())
    assert "other" in data["mcpServers"]
    assert "whatsapp" in data["mcpServers"]


# ---------------------------------------------------------------------------
# 30  _fetch_content
# ---------------------------------------------------------------------------

def test_fetch_content_returns_empty_for_empty_string():
    result = _fetch_content("")
    assert result == ""
