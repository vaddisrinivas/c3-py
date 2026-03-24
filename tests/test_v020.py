"""Tests for v0.2.0 features: roles, tagging, send protection, single-active
group, tool allowlist, catchup, /clear, and participant auto-admit."""

import pytest

from c3.agent import (
    AccessPolicy,
    AppConfig,
    ChannelCore,
    HostConfig,
    AccessControl,
    AppManifest,
    SessionEngine,
    WAMessage,
)
from tests.conftest import FakeWAAdapter


# ── shared helpers ────────────────────────────────────────────────────────────

HOST_JID = "host@s.whatsapp.net"
GROUP_JID = "group1@g.us"
STRANGER_JID = "stranger@s.whatsapp.net"
ALICE_JID = "alice@s.whatsapp.net"


def _manifest(dm=None, group=None):
    return AppManifest(
        name="test",
        access=AccessPolicy(
            commands={
                "/start": ["hosts"],
                "/stop": ["hosts"],
                "/catchup": ["hosts"],
                "/clear": ["hosts"],
            },
            dm=dm or ["hosts"],
            group=group or ["session_participants"],
        ),
    )


def _config():
    return AppConfig(hosts=[HostConfig(jid=HOST_JID, name="Host")])


def _msg(text, sender=HOST_JID, jid=None, is_group=False, catchup=False, push_name=None):
    return WAMessage(
        jid=jid or (GROUP_JID if is_group else sender),
        sender=sender,
        push_name=push_name or sender.split("@")[0],
        text=text,
        timestamp=0,
        is_group=is_group,
        catchup=catchup,
    )


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def wa():
    return FakeWAAdapter()


@pytest.fixture
def ctrl():
    return AccessControl(_manifest(), _config())


@pytest.fixture
def notified():
    return []


@pytest.fixture
def notify_fn(notified):
    async def _fn(content, meta):
        notified.append((content, meta))

    return _fn


@pytest.fixture
def engine(wa, notify_fn, ctrl):
    return SessionEngine(wa, notify_fn, ctrl)


@pytest.fixture
def core(wa, ctrl, engine, notify_fn, tmp_path):
    c = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path)
    c._approval._timeout = 0.1
    return c


async def _resolve(core):
    """Resolve default group so session is active."""
    result = await core.call_tool(
        "resolve_group",
        {
            "invite_link": "https://chat.whatsapp.com/abc",
        },
    )
    return result[0].text


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AccessControl Roles
# ═══════════════════════════════════════════════════════════════════════════════


class TestAccessControlRoles:
    def test_is_host_true_for_configured_host(self, ctrl):
        assert ctrl.is_host(HOST_JID) is True

    def test_is_host_false_for_stranger(self, ctrl):
        assert ctrl.is_host(STRANGER_JID) is False

    def test_is_participant_after_grant(self, ctrl):
        ctrl.grant("session_participants", [{"jid": ALICE_JID, "token": "Alice"}])
        assert ctrl.is_participant(ALICE_JID) is True

    def test_is_participant_false_before_grant(self, ctrl):
        assert ctrl.is_participant(ALICE_JID) is False

    def test_is_elevated_after_grant_jid(self, ctrl):
        ctrl.grant_jid("elevated_participants", ALICE_JID)
        assert ctrl.is_elevated(ALICE_JID) is True

    def test_revoke_all_session_clears_participants_and_elevated(self, ctrl):
        ctrl.grant("session_participants", [{"jid": ALICE_JID, "token": "Alice"}])
        ctrl.grant_jid("elevated_participants", ALICE_JID)
        ctrl.revoke_all_session()
        assert ctrl.is_participant(ALICE_JID) is False
        assert ctrl.is_elevated(ALICE_JID) is False

    def test_grant_jid_adds_to_existing_role(self, ctrl):
        ctrl.grant("session_participants", [{"jid": ALICE_JID, "token": "Alice"}])
        bob = "bob@s.whatsapp.net"
        ctrl.grant_jid("session_participants", bob)
        assert ctrl.is_participant(ALICE_JID) is True
        assert ctrl.is_participant(bob) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Role Tagging  (ChannelCore.on_message)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoleTagging:
    async def test_host_message_has_role_host_in_meta(self, core, notified):
        await _resolve(core)
        await core.on_message(_msg("hello"))
        msg_notes = [(c, m) for c, m in notified if m.get("type") == "message"]
        assert msg_notes
        assert msg_notes[-1][1]["role"] == "host"

    async def test_host_message_prefixed_with_host_tag(self, core, notified):
        await _resolve(core)
        await core.on_message(_msg("hello"))
        msg_notes = [(c, m) for c, m in notified if m.get("type") == "message"]
        assert msg_notes[-1][0].startswith("[host]")

    async def test_group_participant_has_role_participant(self, core, notified):
        await _resolve(core)
        await core.on_message(_msg("hi", sender=ALICE_JID, is_group=True, push_name="Alice"))
        msg_notes = [(c, m) for c, m in notified if m.get("type") == "message"]
        assert msg_notes
        assert msg_notes[-1][1]["role"] == "participant"

    async def test_group_participant_has_read_only(self, core, notified):
        await _resolve(core)
        await core.on_message(_msg("hi", sender=ALICE_JID, is_group=True, push_name="Alice"))
        msg_notes = [(c, m) for c, m in notified if m.get("type") == "message"]
        assert msg_notes[-1][1].get("read_only") == "true"

    async def test_group_host_not_read_only(self, core, notified):
        await _resolve(core)
        await core.on_message(_msg("hi", sender=HOST_JID, is_group=True))
        msg_notes = [(c, m) for c, m in notified if m.get("type") == "message"]
        assert msg_notes
        assert "read_only" not in msg_notes[-1][1]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Send Protection
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendProtection:
    async def test_reply_to_host_allowed(self, core, wa):
        result = await core.call_tool("reply", {"jid": "host", "text": "hi"})
        assert result[0].text == "sent"
        assert wa.sent[-1] == (HOST_JID, "hi")

    async def test_reply_to_group_allowed(self, core, wa):
        await _resolve(core)
        result = await core.call_tool("reply", {"jid": "group", "text": "hi"})
        assert result[0].text == "sent"
        assert wa.sent[-1] == (GROUP_JID, "hi")

    async def test_reply_to_stranger_blocked(self, core):
        result = await core.call_tool("reply", {"jid": STRANGER_JID, "text": "hi"})
        assert "Error" in result[0].text

    async def test_send_private_to_participant_needs_approval(self, core, wa):
        await _resolve(core)
        # Set short timeout so test doesn't hang
        core._approval._timeout = 0.1
        result = await core.call_tool("send_private", {"jid": "Alice", "text": "secret"})
        # No one voted on approval poll → denied after timeout
        assert "denied" in result[0].text.lower()
        # A poll should have been sent to the host for approval
        assert wa.polls

    async def test_send_private_to_elevated_participant_allowed(self, core, wa, ctrl):
        await _resolve(core)
        ctrl.grant_jid("elevated_participants", ALICE_JID)
        result = await core.call_tool("send_private", {"jid": "Alice", "text": "secret"})
        assert result[0].text == "sent"
        assert wa.sent[-1] == (ALICE_JID, "secret")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Single Active Group
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleActiveGroup:
    def test_second_group_clears_first(self, engine):
        engine.set_active("g1@g.us", "game1")
        engine.set_active("g2@g.us", "game2")
        assert "g2@g.us" in engine._active_sessions
        assert "g1@g.us" not in engine._active_sessions

    def test_same_group_doesnt_clear(self, engine):
        engine.set_active("g1@g.us", "game1")
        engine.set_active("g1@g.us", "game1-v2")
        assert "g1@g.us" in engine._active_sessions


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Tool Allowlist
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolAllowlist:
    async def test_allowed_tool_passes(self, wa, ctrl, engine, notify_fn, tmp_path):
        c = ChannelCore(
            wa, ctrl, engine, notify_fn, tmp_path, allowed_tools={"reply", "memory_write"}
        )
        result = await c.call_tool("reply", {"jid": "host", "text": "hi"})
        assert result[0].text == "sent"

    async def test_blocked_tool_returns_error(self, wa, ctrl, engine, notify_fn, tmp_path):
        c = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path, allowed_tools={"reply"})
        result = await c.call_tool(
            "memory_write", {"entity": {"app": "x", "entity": "y", "name": "z"}}
        )
        assert "Error" in result[0].text
        assert "not allowed" in result[0].text

    async def test_none_allowlist_permits_all(self, wa, ctrl, engine, notify_fn, tmp_path):
        c = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path, allowed_tools=None)
        result = await c.call_tool("reply", {"jid": "host", "text": "hi"})
        assert result[0].text == "sent"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Catchup
# ═══════════════════════════════════════════════════════════════════════════════


class TestCatchup:
    async def test_catchup_messages_buffered(self, core, notified):
        await _resolve(core)
        m = _msg("old msg", catchup=True)
        await core.on_message(m)
        # catchup message should NOT trigger a notification
        msg_notes = [n for n in notified if n[1].get("type") == "message"]
        assert not msg_notes

    async def test_catchup_flush_sends_summary(self, core, notified, wa):
        await _resolve(core)
        # Buffer a catchup message
        await core.on_message(_msg("old1", catchup=True))
        await core.on_message(_msg("old2", catchup=True))
        # Trigger /catchup command from host
        await core.on_message(_msg("/catchup"))
        # Should have a catchup notification with count
        catchup_notes = [n for n in notified if n[1].get("type") == "catchup"]
        assert catchup_notes
        assert catchup_notes[-1][1]["count"] == "2"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. /clear Command
# ═══════════════════════════════════════════════════════════════════════════════


class TestClearCommand:
    async def test_clear_writes_flag_file(self, wa, ctrl, notify_fn, tmp_path):
        engine = SessionEngine(wa, notify_fn, ctrl, agent_dir=tmp_path)
        core = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path)
        await _resolve(core)
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(exist_ok=True)
        await core.on_message(_msg("/clear"))
        assert (sessions_dir / "clear_session").exists()

    async def test_clear_sends_confirmation(self, wa, ctrl, notify_fn, tmp_path):
        engine = SessionEngine(wa, notify_fn, ctrl, agent_dir=tmp_path)
        core = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path)
        (tmp_path / "sessions").mkdir(exist_ok=True)
        await _resolve(core)
        await core.on_message(_msg("/clear"))
        # Host should receive a confirmation message
        confirms = [
            t
            for j, t in wa.sent
            if j == HOST_JID and "clear" in t.lower() or "session" in t.lower()
        ]
        assert confirms


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Participant Auto-Admit
# ═══════════════════════════════════════════════════════════════════════════════


class TestParticipantAutoAdmit:
    async def test_group_message_from_session_group_auto_admits(self, core, ctrl, notified):
        await _resolve(core)
        new_jid = "newguy@s.whatsapp.net"
        # newguy is NOT in session_participants yet, but message arrives from
        # the resolved group (which has role session_group)
        m = _msg("hey", sender=new_jid, is_group=True, push_name="NewGuy")
        await core.on_message(m)
        # After auto-admit, newguy should be a participant
        assert ctrl.is_participant(new_jid)
        # And the message should have been forwarded
        msg_notes = [n for n in notified if n[1].get("type") == "message"]
        assert msg_notes

    async def test_participant_dm_without_elevation_dropped(self, core, ctrl, notified, wa):
        await _resolve(core)
        # Alice is a participant (via resolve_group) but not elevated
        assert ctrl.is_participant(ALICE_JID)
        assert not ctrl.is_elevated(ALICE_JID)
        m = _msg("secret dm", sender=ALICE_JID, is_group=False, push_name="Alice")
        await core.on_message(m)
        # DM should be dropped (no message notification), but a poll
        # should be sent to host for approval
        msg_notes = [n for n in notified if n[1].get("type") == "message"]
        assert not msg_notes
        assert wa.polls  # host should have received an elevation poll
