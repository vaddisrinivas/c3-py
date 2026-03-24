"""Integration tests for ChannelCore tool handlers."""

import json
import pytest
from pathlib import Path

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

pytestmark = pytest.mark.asyncio

HOST = "host@s.whatsapp.net"
GROUP = "group1@g.us"


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
    core._approval._timeout = 0.1
    return wa, core, ctrl, engine, notified, tmp_path


async def _resolve(core):
    return (await core.call_tool("resolve_group", {"invite_link": "https://chat.whatsapp.com/abc"}))[0].text


# 1. reply to host sends directly
async def test_reply_to_host_sends_directly(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("reply", {"jid": "host", "text": "hello"})
    assert result[0].text == "sent"
    assert (HOST, "hello") in wa.sent


# 2. reply with empty text returns error
async def test_reply_empty_text_returns_error(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("reply", {"jid": "host", "text": ""})
    assert "error" in result[0].text.lower()


# 3. reply to non-participant returns error
async def test_reply_to_non_participant_returns_error(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("reply", {"jid": "stranger@s.whatsapp.net", "text": "hi"})
    assert "not in session" in result[0].text.lower()


# 4. reply to participant needs approval (times out -> denied)
async def test_reply_to_participant_times_out_denied(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    result = await core.call_tool("reply", {"jid": "Alice", "text": "hello participant"})
    assert "denied" in result[0].text.lower()


# 5. send_private aliases to reply
async def test_send_private_aliases_to_reply(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("send_private", {"jid": "host", "text": "private msg"})
    assert result[0].text == "sent"
    assert (HOST, "private msg") in wa.sent


# 6. send_poll with valid args sends poll
async def test_send_poll_valid_args(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    result = await core.call_tool(
        "send_poll",
        {"question": "Pick one?", "options": ["A", "B"], "group_jid": "group"},
    )
    assert "poll" in result[0].text.lower()
    assert len(wa.polls) == 1


# 7. send_poll missing question returns error
async def test_send_poll_missing_question(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("send_poll", {"options": ["A", "B"], "group_jid": GROUP})
    assert "question required" in result[0].text.lower()


# 8. send_poll with string options parses JSON
async def test_send_poll_string_options_parsed(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    result = await core.call_tool(
        "send_poll",
        {"question": "Pick?", "options": '["X", "Y"]', "group_jid": "group"},
    )
    assert "poll" in result[0].text.lower()
    assert wa.polls[-1][2] == ["X", "Y"]


# 9. send_poll with invalid options returns error
async def test_send_poll_invalid_options(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    result = await core.call_tool(
        "send_poll",
        {"question": "Pick?", "options": "not json", "group_jid": "group"},
    )
    assert "options" in result[0].text.lower()


# 10. send_poll without resolved group returns error
async def test_send_poll_no_resolved_group(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool(
        "send_poll",
        {"question": "Pick?", "options": ["A", "B"], "group_jid": "group"},
    )
    assert "group not resolved" in result[0].text.lower()


# 11. resolve_group grants session_participants
async def test_resolve_group_grants_participants(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await _resolve(core)
    assert "Alice" in result
    assert ctrl.is_participant("alice@s.whatsapp.net")
    assert ctrl.is_participant("bob@s.whatsapp.net")


# 12. resolve_group with invalid link returns error
async def test_resolve_group_invalid_link(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("resolve_group", {"invite_link": "not-a-link"})
    assert "invalid" in result[0].text.lower()


# 13. set_timer creates timer
async def test_set_timer_creates_timer(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    result = await core.call_tool(
        "set_timer", {"seconds": 30, "name": "round1", "group_jid": "group"}
    )
    assert "timer" in result[0].text.lower()
    assert "round1" in result[0].text


# 14. set_timer without active session returns error
async def test_set_timer_no_session(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("set_timer", {"seconds": 30, "name": "round1"})
    assert "no active session" in result[0].text.lower()


# 15. end_session clears everything
async def test_end_session_clears(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    assert ctrl.is_participant("alice@s.whatsapp.net")
    result = await core.call_tool("end_session", {})
    assert "session ended" in result[0].text.lower()
    assert not ctrl.is_participant("alice@s.whatsapp.net")


# 16. memory_write with valid entity succeeds
async def test_memory_write_valid(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool(
        "memory_write",
        {"entity": {"app": "test", "entity": "player", "name": "Alice", "score": 10}},
    )
    assert result[0].text == "ok"


# 17. memory_write without entity returns error
async def test_memory_write_no_entity(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("memory_write", {})
    assert "entity required" in result[0].text.lower()


# 18. memory_write missing app/entity fields returns error
async def test_memory_write_missing_fields(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("memory_write", {"entity": {"name": "Alice"}})
    assert "app" in result[0].text.lower() and "entity" in result[0].text.lower()


# 19. memory_read returns stored entities
async def test_memory_read_returns_stored(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await core.call_tool(
        "memory_write",
        {"entity": {"app": "myapp", "entity": "player", "name": "Bob", "score": 5}},
    )
    result = await core.call_tool("memory_read", {"app": "myapp"})
    data = json.loads(result[0].text)
    assert len(data) >= 1
    assert data[0]["name"] == "Bob"


# 20. memory_search finds matching entities
async def test_memory_search_finds(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await core.call_tool(
        "memory_write",
        {"entity": {"app": "search_app", "entity": "item", "name": "UniqueSearchTerm"}},
    )
    result = await core.call_tool("memory_search", {"query": "UniqueSearchTerm"})
    data = json.loads(result[0].text)
    assert len(data) >= 1
    assert any("UniqueSearchTerm" in str(r) for r in data)


# 21. memory_delete with filter succeeds
async def test_memory_delete_with_filter(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await core.call_tool(
        "memory_write",
        {"entity": {"app": "del_app", "entity": "item", "name": "to_delete"}},
    )
    result = await core.call_tool("memory_delete", {"app": "del_app", "name": "to_delete"})
    assert result[0].text == "ok"


# 22. memory_delete without filter blocked
async def test_memory_delete_no_filter_blocked(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("memory_delete", {})
    assert "filter" in result[0].text.lower()


# 23. save_file writes to valid path
async def test_save_file_valid_path(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("save_file", {"path": "notes.txt", "content": "hello world"})
    assert "saved" in result[0].text.lower()
    assert (tmp_path / "notes.txt").read_text() == "hello world"


# 24. save_file blocks path traversal
async def test_save_file_blocks_traversal(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("save_file", {"path": "../../etc/passwd", "content": "hack"})
    assert "error" in result[0].text.lower()


# 25. save_file blocks protected files
async def test_save_file_blocks_protected(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("save_file", {"path": "config.json", "content": "{}"})
    assert "protected" in result[0].text.lower()


# 26. save_file blocks content over 1MB
async def test_save_file_blocks_large_content(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    big = "x" * 1_100_000
    result = await core.call_tool("save_file", {"path": "big.txt", "content": big})
    assert "too large" in result[0].text.lower() or "1mb" in result[0].text.lower()


# 27. save_file restricts .md in app dirs
async def test_save_file_restricts_md_in_app_dirs(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    # Create an app dir with app.json to trigger the restriction
    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}")
    result = await core.call_tool("save_file", {"path": "myapp/random.md", "content": "# test"})
    assert "error" in result[0].text.lower()


# 28. unknown tool returns error (not exception)
async def test_unknown_tool_returns_error(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    result = await core.call_tool("nonexistent_tool", {})
    assert "unknown tool" in result[0].text.lower()


# 29. blocked tool returns error when allowed_tools set
async def test_blocked_tool_with_allowed_tools(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    core._allowed_tools = {"reply"}
    result = await core.call_tool("send_poll", {"question": "Q?", "options": ["A", "B"]})
    assert "not allowed" in result[0].text.lower()


# 30. get_group_members returns member list
async def test_get_group_members_returns_list(setup):
    wa, core, ctrl, engine, notified, tmp_path = setup
    await _resolve(core)
    result = await core.call_tool("get_group_members", {"group_jid": "group"})
    text = result[0].text
    assert "Alice" in text
    assert "Bob" in text
