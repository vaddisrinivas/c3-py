"""Tests for PluginMCPProxy and the load_plugin tool."""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from c3.agent import PluginMCPProxy


# ─── PluginMCPProxy ───────────────────────────────────────────────────────────

class TestPluginMCPProxy:
    def test_tool_names_empty_before_ready(self, tmp_path):
        proxy = PluginMCPProxy("test", {"command": "echo"}, tmp_path)
        assert proxy.tool_names == set()

    def test_tool_names_after_population(self, tmp_path):
        from mcp.types import Tool
        proxy = PluginMCPProxy("test", {"command": "echo"}, tmp_path)
        proxy.tools = [
            Tool(name="add_expense", description="x", inputSchema={"type": "object"}),
            Tool(name="split_bill",  description="y", inputSchema={"type": "object"}),
        ]
        assert proxy.tool_names == {"add_expense", "split_bill"}

    def test_env_substitution(self, tmp_path):
        proxy = PluginMCPProxy("test", {
            "command": "uvx",
            "env": {"DATA_DIR": "${plugin_dir}/data"},
        }, tmp_path)
        # env is expanded inside run() — verify the pattern is in params
        assert "${plugin_dir}" in proxy._params["env"]["DATA_DIR"]

    @pytest.mark.asyncio
    async def test_call_tool_raises_before_ready(self, tmp_path):
        proxy = PluginMCPProxy("test", {"command": "echo"}, tmp_path)
        with pytest.raises((asyncio.TimeoutError, Exception)):
            await proxy.call_tool("noop", {})


# ─── load_plugin tool (via ChannelCore) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_load_plugin_skill_only(core, notified, tmp_path):
    """load_plugin for a skill-only plugin sends skill_load notifications."""
    # Create a minimal plugin dir in core's plugin_dir
    plugin_dir = core._base
    games_dir = plugin_dir / "games"
    games_dir.mkdir(exist_ok=True)
    (games_dir / "trivia.md").write_text("# Trivia rules")

    result = await core.call_tool("load_plugin", {"name": "trivia"})
    assert any("trivia" in r.text.lower() for r in result)
    assert any(m.get("type") == "skill_load" for _, m in notified)


@pytest.mark.asyncio
async def test_load_plugin_not_found(core, notified):
    """load_plugin returns an error for unknown plugins."""
    result = await core.call_tool("load_plugin", {"name": "nonexistent_xyz"})
    assert any("not found" in r.text.lower() for r in result)
    assert not any(m.get("type") == "skill_load" for _, m in notified)


@pytest.mark.asyncio
async def test_load_plugin_empty_name(core):
    result = await core.call_tool("load_plugin", {"name": ""})
    assert any("required" in r.text.lower() for r in result)


@pytest.mark.asyncio
async def test_load_plugin_whole_category(core, notified, tmp_path):
    """load_plugin for a category dir loads all .md files inside it."""
    plugin_dir = core._base
    cat = plugin_dir / "social"
    cat.mkdir(exist_ok=True)
    (cat / "CLAUDE.md").write_text("## Social\nCapabilities.")
    (cat / "skills").mkdir(exist_ok=True)
    (cat / "skills" / "icebreaker.md").write_text("# Icebreaker rules")
    (cat / "skills" / "roast.md").write_text("# Roast rules")

    result = await core.call_tool("load_plugin", {"name": "social"})
    loaded_skills = [m.get("skill") for _, m in notified if m.get("type") == "skill_load"]
    assert any("CLAUDE.md" in s for s in loaded_skills)
    assert "icebreaker" in loaded_skills
    assert "roast" in loaded_skills


@pytest.mark.asyncio
async def test_load_plugin_skips_bundled_when_local_exists(core, notified, tmp_path):
    """Local plugin dir takes priority over bundled."""
    plugin_dir = core._base
    games_dir = plugin_dir / "games"
    (games_dir / "skills").mkdir(parents=True, exist_ok=True)
    (games_dir / "skills" / "custom.md").write_text("# Custom game — local override")

    result = await core.call_tool("load_plugin", {"name": "custom"})
    assert any(m.get("type") == "skill_load" for _, m in notified)
