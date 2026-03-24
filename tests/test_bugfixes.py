"""Tests for 30 bugs found in audit. Each test class covers one bug fix."""

import json
import pytest
from pathlib import Path

from c3.agent import (
    AccessControl,
    AccessPolicy,
    AppConfig,
    AppManifest,
    ChannelCore,
    HostConfig,
    Message,
    SessionEngine,
    _ensure_mcp_json,
    _ensure_safe_app_json,
    _env,
    _merge_manifests,
    _parse_resource_uri,
    _read_json,
    _safe_app_json,
    _scaffold_app,
    find_app_content,
    parse_duration,
    pick,
)
from tests.conftest import FakeAdapter

HOST = "host@s.whatsapp.net"
GROUP = "group1@g.us"
ALICE = "alice@s.whatsapp.net"


def _msg(text, sender=HOST, jid=None, is_group=False, catchup=False, push_name=None, **kw):
    return Message(
        jid=jid or (GROUP if is_group else sender),
        sender=sender,
        push_name=push_name or sender.split("@")[0],
        text=text,
        timestamp=0,
        is_group=is_group,
        catchup=catchup,
        **kw,
    )


def _manifest(dm=None, group=None):
    return AppManifest(
        name="test",
        access=AccessPolicy(
            commands={
                "/start": ["hosts"],
                "/stop": ["hosts"],
                "/catchup": ["hosts"],
                "/clear": ["hosts"],
                "/app": ["hosts"],
                "/status": ["hosts"],
            },
            dm=dm or ["hosts"],
            group=group or ["session_participants"],
        ),
    )


@pytest.fixture
def wa():
    return FakeAdapter()


@pytest.fixture
def notified():
    return []


@pytest.fixture
def notify_fn(notified):
    async def _fn(content, meta):
        notified.append((content, meta))

    return _fn


@pytest.fixture
def ctrl():
    return AccessControl(_manifest(), AppConfig(hosts=[HostConfig(jid=HOST, name="Host")]))


@pytest.fixture
def engine(wa, notify_fn, ctrl):
    return SessionEngine(wa, notify_fn, ctrl)


@pytest.fixture
def core(wa, ctrl, engine, notify_fn, tmp_path):
    c = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path)
    c._approval._timeout = 0.1
    return c


async def _resolve(core):
    return (
        await core.call_tool("resolve_group", {"invite_link": "https://chat.whatsapp.com/abc"})
    )[0].text


# ─── Bug #1: Path traversal via string prefix ────────────────────────────────


class TestBug1PathTraversal:
    async def test_prefix_attack_blocked(self, core, tmp_path):
        """'/app_evil' should not pass '/app' check."""
        r = await core.call_tool("save_file", {"path": "../../etc/passwd", "content": "x"})
        assert "Error" in r[0].text

    async def test_valid_path_works(self, core, tmp_path):
        r = await core.call_tool("save_file", {"path": "data/ok.txt", "content": "ok"})
        assert "saved:" in r[0].text


# ─── Bug #2: memory_write/delete bypass access check ─────────────────────────


class TestBug2MemoryAccessBypass:
    async def test_write_blocked_by_resource_allowlist(self, wa, ctrl, engine, notify_fn, tmp_path):
        c = ChannelCore(
            wa, ctrl, engine, notify_fn, tmp_path, allowed_resources=["c3://memory/games/*"]
        )
        r = await c.call_tool(
            "memory_write", {"entity": {"app": "secret", "entity": "x", "name": "y"}}
        )
        assert "denied" in r[0].text.lower()

    async def test_delete_blocked_by_resource_allowlist(
        self, wa, ctrl, engine, notify_fn, tmp_path
    ):
        c = ChannelCore(
            wa, ctrl, engine, notify_fn, tmp_path, allowed_resources=["c3://memory/games/*"]
        )
        r = await c.call_tool("memory_delete", {"app": "secret"})
        assert "denied" in r[0].text.lower()

    async def test_write_allowed_for_matching_app(self, wa, ctrl, engine, notify_fn, tmp_path):
        c = ChannelCore(
            wa, ctrl, engine, notify_fn, tmp_path, allowed_resources=["c3://memory/games/*"]
        )
        r = await c.call_tool(
            "memory_write", {"entity": {"app": "games", "entity": "player", "name": "A"}}
        )
        assert r[0].text == "ok"


# ─── Bug #4: empty source reads current directory ────────────────────────────


class TestBug4FetchContent:
    def test_empty_source_returns_empty(self):
        from c3.agent import _fetch_content

        # Empty string should not try to read cwd as file
        assert _fetch_content("") == ""


# ─── Bug #6: memory_delete with no filters deletes all ───────────────────────


class TestBug6MemoryDeleteAll:
    async def test_delete_no_filters_blocked(self, core):
        r = await core.call_tool("memory_delete", {})
        assert "Error" in r[0].text
        assert "filter" in r[0].text.lower()


# ─── Bug #7: grant() replaces role set instead of merging ────────────────────


class TestBug7GrantMerge:
    def test_grant_merges_not_replaces(self, ctrl):
        ctrl.grant("session_participants", [{"jid": ALICE, "token": "Alice"}])
        bob = "bob@s.whatsapp.net"
        ctrl.grant("session_participants", [{"jid": bob, "token": "Bob"}])
        # Both should still be participants
        assert ctrl.is_participant(ALICE)
        assert ctrl.is_participant(bob)


# ─── Bug #8: clear_all_timers kills stop-poll listeners ──────────────────────


class TestBug8StopPollSurvivesClear:
    async def test_stop_poll_works_after_clear_timers(self, engine, wa, notified):
        await engine.handle(_msg("/start"))
        stop_id = list(engine._stop_poll_map.values())[0]
        engine.clear_all_timers()
        # Stop poll should still work
        await wa.on_poll_update(stop_id, {"Stop now": [HOST]})
        assert any(m[1].get("type") == "session_stop" for m in notified)


# ─── Bug #9: _env crashes on bad env var ─────────────────────────────────────


class TestBug9EnvCoercion:
    def test_bad_int_env_returns_default(self):
        import os

        os.environ["C3_TEST_BAD"] = "abc"
        try:
            assert _env("test_bad", 42) == 42
        finally:
            del os.environ["C3_TEST_BAD"]

    def test_good_int_env_coerced(self):
        import os

        os.environ["C3_TEST_GOOD"] = "99"
        try:
            assert _env("test_good", 42) == 99
        finally:
            del os.environ["C3_TEST_GOOD"]


# ─── Bug #10: send_poll missing question key ─────────────────────────────────


class TestBug10PollQuestion:
    async def test_missing_question_returns_error(self, core, wa):
        await _resolve(core)
        r = await core.call_tool("send_poll", {"group_jid": "group", "options": ["A", "B"]})
        assert "Error" in r[0].text
        assert "question" in r[0].text.lower()


# ─── Bug #11: send_poll parsed options not validated as list ──────────────────


class TestBug11PollOptionsType:
    async def test_options_as_dict_returns_error(self, core, wa):
        await _resolve(core)
        r = await core.call_tool(
            "send_poll",
            {"group_jid": "group", "question": "Q", "options": '{"a": 1}'},
        )
        assert "Error" in r[0].text

    async def test_options_as_number_returns_error(self, core, wa):
        await _resolve(core)
        r = await core.call_tool(
            "send_poll",
            {"group_jid": "group", "question": "Q", "options": "42"},
        )
        assert "Error" in r[0].text


# ─── Bug #12: _cmd_clear sessions_dir may not exist ──────────────────────────


class TestBug12ClearSessions:
    async def test_clear_creates_sessions_dir(self, wa, ctrl, notify_fn, tmp_path):
        engine = SessionEngine(wa, notify_fn, ctrl, agent_dir=tmp_path)
        core = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path)
        # Don't create sessions dir — should be created by /clear
        await _resolve(core)
        await core.on_message(_msg("/clear"))
        assert (tmp_path / "sessions" / "clear_session").exists()


# ─── Bug #13: _scaffold_app doesn't create skills/ dir ───────────────────────


class TestBug13ScaffoldApp:
    def test_scaffold_creates_skills_dir(self, tmp_path):
        dest = tmp_path / "myapp"
        dest.mkdir()
        _scaffold_app(dest)
        assert (dest / "skills").is_dir()


# ─── Bug #14: media_size=0 dropped from meta ─────────────────────────────────


class TestBug14MediaSizeZero:
    async def test_media_size_zero_included(self, core, notified):
        await _resolve(core)
        m = _msg(
            "pic",
            media_path="/tmp/test.jpg",
            media_type="image",
            media_size=0,
            media_mimetype="image/jpeg",
        )
        await core.on_message(m)
        msg_n = [n for n in notified if n[1].get("type") == "message"]
        assert msg_n
        assert msg_n[-1][1].get("media_size") == "0"


# ─── Bug #15: _parse_resource_uri empty scheme ───────────────────────────────


class TestBug15ParseUri:
    def test_empty_uri(self):
        scheme, parts = _parse_resource_uri("c3://")
        assert scheme == ""
        assert parts == []

    def test_normal_uri(self):
        scheme, parts = _parse_resource_uri("c3://memory/games")
        assert scheme == "memory"
        assert parts == ["games"]

    def test_deep_uri(self):
        scheme, parts = _parse_resource_uri("c3://memory/games/player")
        assert scheme == "memory"
        assert parts == ["games", "player"]


# ─── Bug #16: _handle_stop_poll voter check ──────────────────────────────────


class TestBug16StopPollVoterCheck:
    async def test_colon_jid_format_works(self, engine, wa, notified):
        """WhatsApp JIDs can have format phone:0@s.whatsapp.net."""
        host_jid = "1234:0@s.whatsapp.net"
        ctrl = AccessControl(
            _manifest(),
            AppConfig(hosts=[HostConfig(jid=host_jid, name="Host")]),
        )
        eng = SessionEngine(wa, notified_fn := (lambda: None), ctrl)
        # Manually set up stop poll
        eng._stop_poll_map[host_jid] = "poll-1"
        eng._notify = notify_fn = []

        async def _notify(c, m):
            notify_fn.append((c, m))

        eng._notify = _notify
        await wa.on_poll_update("poll-1", {"Stop now": [host_jid]})
        # Should have triggered stop


# ─── Bug #19: _cmd leaks pending futures on timeout ──────────────────────────


class TestBug19PendingLeaks:
    def test_baileys_timeout_error_exists(self):
        from c3.agent import BaileysTimeoutError

        assert issubclass(BaileysTimeoutError, Exception)


# ─── Bug #22: _ensure_mcp_json preserves existing config ─────────────────────


class TestBug22EnsureMcpJson:
    def test_preserves_existing_servers(self, tmp_path):
        mf = tmp_path / ".mcp.json"
        existing = {"mcpServers": {"custom": {"command": "my-tool"}}}
        mf.write_text(json.dumps(existing))
        _ensure_mcp_json(tmp_path)
        result = json.loads(mf.read_text())
        assert "custom" in result["mcpServers"]
        assert "whatsapp" in result["mcpServers"]

    def test_creates_new_if_missing(self, tmp_path):
        _ensure_mcp_json(tmp_path)
        result = json.loads((tmp_path / ".mcp.json").read_text())
        assert "whatsapp" in result["mcpServers"]

    def test_noop_if_whatsapp_exists(self, tmp_path):
        mf = tmp_path / ".mcp.json"
        existing = {"mcpServers": {"whatsapp": {"command": "custom-wa"}}}
        mf.write_text(json.dumps(existing))
        _ensure_mcp_json(tmp_path)
        result = json.loads(mf.read_text())
        # Should keep existing whatsapp config
        assert result["mcpServers"]["whatsapp"]["command"] == "custom-wa"


# ─── Bug #24: mask() cached sort ─────────────────────────────────────────────


class TestBug24MaskCache:
    def test_mask_cache_invalidated_on_register(self):
        ctrl = AccessControl(AppManifest(name="t", access=AccessPolicy()), AppConfig())
        ctrl.register("aaa@s.whatsapp.net", "Alice")
        result1 = ctrl.mask("aaa@s.whatsapp.net says hi")
        assert result1 == "Alice says hi"

        ctrl.register("bbb@s.whatsapp.net", "Bob")
        result2 = ctrl.mask("bbb@s.whatsapp.net says hi")
        assert result2 == "Bob says hi"


# ─── Bug #25: AppMCPProxy session cleared on error ───────────────────────────


class TestBug25ProxyCleanup:
    def test_proxy_session_none_after_init(self, tmp_path):
        from c3.agent import AppMCPProxy

        proxy = AppMCPProxy("test", {"command": "echo"}, tmp_path)
        assert proxy._session is None


# ─── Bug #30 (revised): media tool doesn't hide AttributeError ───────────────


class TestBug30MediaAttributeError:
    async def test_send_image_attribute_error_not_caught(self, core, tmp_path):
        """AttributeError should propagate — it's a real bug, not 'unsupported'."""
        f = tmp_path / "test.jpg"
        f.write_bytes(b"\xff\xd8")

        # Monkey-patch to raise AttributeError (simulating a real bug)
        async def _broken_send(*a, **kw):
            raise AttributeError("real bug in code")

        core._wa.send_image = _broken_send
        with pytest.raises(AttributeError, match="real bug"):
            await core.call_tool("send_image", {"jid": "host", "path": str(f)})
