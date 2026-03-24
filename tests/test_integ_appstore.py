"""Integration tests for appstore app and registry features."""

import json
import pytest
from pathlib import Path
from urllib.parse import urlparse

import c3.agent as _agent_module
from c3.agent import (
    AccessControl,
    AccessPolicy,
    AppConfig,
    AppManifest,
    HostConfig,
    ChannelCore,
    SessionEngine,
)
from tests.conftest import FakeAdapter

HOST = "host@s.whatsapp.net"
GROUP = "group1@g.us"

VALID_REGISTRY_TYPES = {"mcp", "skill", "c3", "prompt", "workflow", "huggingface"}


@pytest.fixture
def setup(tmp_path):
    wa = FakeAdapter()
    notified = []

    async def _notify(c, m):
        notified.append((c, m))

    manifest = AppManifest(
        name="test",
        access=AccessPolicy(
            commands={"/start": ["hosts"], "/stop": ["hosts"]},
            dm=["hosts"],
            group=[],
        ),
    )
    ctrl = AccessControl(manifest, AppConfig(hosts=[HostConfig(jid=HOST, name="Host")]))
    engine = SessionEngine(wa, _notify, ctrl)
    core = ChannelCore(wa, ctrl, engine, _notify, tmp_path)
    return wa, core, ctrl, engine, notified, tmp_path


# ---------------------------------------------------------------------------
# 1. Registry injection test — load_app("appstore") response includes
#    "Registries" and lists entries from _C["registries"].
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_app_appstore_includes_registries_header(setup, tmp_path):
    """load_app('appstore') response must contain 'Registries' header."""
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("load_app", {"name": "appstore"})
    text = result[0].text
    assert "Registries" in text, f"Expected 'Registries' in response, got: {text!r}"


@pytest.mark.asyncio
async def test_load_app_appstore_lists_registry_names(setup, tmp_path):
    """load_app('appstore') response must include registry names from _C config."""
    wa, core, ctrl, engine, notified, tmp_path = setup
    registries = _agent_module._C.get("registries", [])
    assert registries, "Expected at least one registry in _C['registries']"

    result = await core.call_tool("load_app", {"name": "appstore"})
    text = result[0].text

    # At least some registry names should appear in the response
    found = [r["name"] for r in registries if r["name"] in text]
    assert found, (
        f"None of the registry names appeared in load_app('appstore') response.\n"
        f"Expected any of: {[r['name'] for r in registries]}\nGot: {text!r}"
    )


# ---------------------------------------------------------------------------
# 2. Registry format test — every entry must have name, url, type (valid type).
# ---------------------------------------------------------------------------

def test_registry_entries_have_required_fields():
    """Every registry entry must have name (str), url (str), type (str)."""
    registries = _agent_module._C.get("registries", [])
    assert registries, "Expected at least one registry in _C['registries']"

    for i, reg in enumerate(registries):
        assert isinstance(reg.get("name"), str) and reg["name"], (
            f"Registry[{i}] missing or empty 'name': {reg}"
        )
        assert isinstance(reg.get("url"), str) and reg["url"], (
            f"Registry[{i}] missing or empty 'url': {reg}"
        )
        assert isinstance(reg.get("type"), str) and reg["type"], (
            f"Registry[{i}] missing or empty 'type': {reg}"
        )


def test_registry_types_are_valid():
    """Every registry 'type' must be one of the allowed values."""
    registries = _agent_module._C.get("registries", [])
    assert registries, "Expected at least one registry in _C['registries']"

    invalid = [
        (r["name"], r["type"])
        for r in registries
        if r.get("type") not in VALID_REGISTRY_TYPES
    ]
    assert not invalid, (
        f"Registry entries with invalid 'type' (must be one of {VALID_REGISTRY_TYPES}): {invalid}"
    )


# ---------------------------------------------------------------------------
# 3. Registry URL validity test — every url must be a valid https:// URL.
# ---------------------------------------------------------------------------

def test_registry_urls_are_valid_https():
    """Every registry url must start with https:// and parse as a valid URL."""
    registries = _agent_module._C.get("registries", [])
    assert registries, "Expected at least one registry in _C['registries']"

    invalid = []
    for reg in registries:
        url = reg.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            invalid.append((reg.get("name"), url))

    assert not invalid, (
        f"Registry entries with invalid or non-https URLs: {invalid}"
    )


# ---------------------------------------------------------------------------
# 4. Integration: load_app("appstore") returns registry list.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_app_appstore_returns_all_registry_names(setup, tmp_path):
    """Full integration: load_app('appstore') response includes every registry name."""
    wa, core, ctrl, engine, notified, tmp_path = setup
    registries = _agent_module._C.get("registries", [])
    assert registries, "Expected at least one registry in _C['registries']"

    result = await core.call_tool("load_app", {"name": "appstore"})
    text = result[0].text

    missing = [r["name"] for r in registries if r["name"] not in text]
    assert not missing, (
        f"load_app('appstore') response missing registry names: {missing}\nFull response: {text!r}"
    )


# ---------------------------------------------------------------------------
# 5. load_app non-appstore does NOT get registry list.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_app_non_appstore_does_not_include_registries(setup, tmp_path):
    """load_app('games') must NOT include 'Registries' in the response."""
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("load_app", {"name": "games"})
    text = result[0].text
    assert "Registries" not in text, (
        f"Expected 'Registries' to be absent from load_app('games') response, got: {text!r}"
    )


@pytest.mark.asyncio
async def test_load_app_calendar_does_not_include_registries(setup, tmp_path):
    """load_app('calendar') must NOT include 'Registries' in the response."""
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("load_app", {"name": "calendar"})
    text = result[0].text
    assert "Registries" not in text, (
        f"Expected 'Registries' to be absent from load_app('calendar') response, got: {text!r}"
    )


# ---------------------------------------------------------------------------
# 6. appstore app.json has correct structure.
# ---------------------------------------------------------------------------

def test_appstore_app_json_is_valid_json():
    """c3/apps/appstore/app.json must be valid JSON."""
    app_json_path = Path(__file__).parent.parent / "c3" / "apps" / "appstore" / "app.json"
    assert app_json_path.exists(), f"app.json not found at {app_json_path}"
    data = json.loads(app_json_path.read_text())
    assert isinstance(data, dict), "app.json must be a JSON object"


def test_appstore_app_json_has_required_fields():
    """c3/apps/appstore/app.json must contain name, description, trust_level, access."""
    app_json_path = Path(__file__).parent.parent / "c3" / "apps" / "appstore" / "app.json"
    data = json.loads(app_json_path.read_text())

    required_fields = ["name", "description", "trust_level", "access"]
    missing = [f for f in required_fields if f not in data]
    assert not missing, f"app.json missing required fields: {missing}"


def test_appstore_app_json_name_is_appstore():
    """c3/apps/appstore/app.json name field must equal 'appstore'."""
    app_json_path = Path(__file__).parent.parent / "c3" / "apps" / "appstore" / "app.json"
    data = json.loads(app_json_path.read_text())
    assert data["name"] == "appstore", (
        f"Expected name='appstore', got: {data.get('name')!r}"
    )


def test_appstore_app_json_trust_level_is_builtin():
    """c3/apps/appstore/app.json trust_level must be 'builtin'."""
    app_json_path = Path(__file__).parent.parent / "c3" / "apps" / "appstore" / "app.json"
    data = json.loads(app_json_path.read_text())
    assert data["trust_level"] == "builtin", (
        f"Expected trust_level='builtin', got: {data.get('trust_level')!r}"
    )


def test_appstore_app_json_access_has_required_keys():
    """c3/apps/appstore/app.json access field must contain 'commands', 'dm', 'group'."""
    app_json_path = Path(__file__).parent.parent / "c3" / "apps" / "appstore" / "app.json"
    data = json.loads(app_json_path.read_text())
    access = data.get("access", {})
    for key in ("commands", "dm", "group"):
        assert key in access, f"app.json access missing '{key}' key"
