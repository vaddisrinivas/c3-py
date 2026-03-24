"""Integration tests for AccessControl, masking, roles, and on_message access flow."""

import pytest

from c3.agent import (
    AccessControl,
    AccessPolicy,
    AppConfig,
    AppManifest,
    ChannelCore,
    HostConfig,
    Message,
    SessionEngine,
)
from tests.conftest import FakeAdapter

pytestmark = pytest.mark.asyncio

HOST = "host@s.whatsapp.net"
GROUP = "group1@g.us"
ALICE = "alice@s.whatsapp.net"
BOB = "bob@s.whatsapp.net"


def _msg(text, sender=HOST, jid=None, is_group=False, **kw):
    return Message(
        jid=jid or (GROUP if is_group else sender),
        sender=sender,
        push_name=sender.split("@")[0],
        text=text,
        timestamp=0,
        is_group=is_group,
        **kw,
    )


def _make_ctrl(dm=None, group=None, commands=None):
    manifest = AppManifest(
        name="test",
        access=AccessPolicy(
            commands=commands or {"/start": ["hosts"], "/stop": ["hosts"]},
            dm=dm or ["hosts"],
            group=group or [],
        ),
    )
    config = AppConfig(hosts=[HostConfig(jid=HOST, name="Host")])
    return AccessControl(manifest, config)


def _make_core(ctrl, tmp_path):
    wa = FakeAdapter()
    notified = []

    async def notify_fn(content, meta):
        notified.append((content, meta))

    engine = SessionEngine(wa, notify_fn, ctrl)
    core = ChannelCore(wa, ctrl, engine, notify_fn, tmp_path)
    core._approval._timeout = 0.1
    return core, wa, notified


# ---------- 1-4: register ----------

async def test_register_stores_jid_to_token():
    ctrl = _make_ctrl()
    ctrl.register(ALICE, "alice-tok")
    assert ctrl._jid_to_token[ALICE] == "alice-tok"


async def test_register_stores_token_to_jid_first_wins():
    ctrl = _make_ctrl()
    ctrl.register(ALICE, "shared-tok")
    ctrl.register(BOB, "shared-tok")
    assert ctrl._token_to_jid["shared-tok"] == ALICE


async def test_register_empty_jid_noop():
    ctrl = _make_ctrl()
    before = dict(ctrl._jid_to_token)
    ctrl.register("", "tok")
    assert ctrl._jid_to_token == before


async def test_register_empty_token_noop():
    ctrl = _make_ctrl()
    before = dict(ctrl._jid_to_token)
    ctrl.register(ALICE, "")
    assert ctrl._jid_to_token == before


# ---------- 5-8: mask / unmask ----------

async def test_mask_replaces_jid_with_token():
    ctrl = _make_ctrl()
    ctrl.register(ALICE, "ALICE_TOK")
    assert ctrl.mask(f"hello {ALICE} world") == "hello ALICE_TOK world"


async def test_mask_replaces_multiple_jids_longest_first():
    ctrl = _make_ctrl()
    ctrl.register("short@s.whatsapp.net", "SHORT")
    ctrl.register("muchlonger@s.whatsapp.net", "LONG")
    text = "a muchlonger@s.whatsapp.net b short@s.whatsapp.net c"
    result = ctrl.mask(text)
    assert "LONG" in result
    assert "SHORT" in result
    assert "muchlonger@s.whatsapp.net" not in result
    assert "short@s.whatsapp.net" not in result


async def test_mask_unknown_jid_unchanged():
    ctrl = _make_ctrl()
    text = f"hello {ALICE} world"
    assert ctrl.mask(text) == text


async def test_mask_meta_masks_strings_preserves_types():
    ctrl = _make_ctrl()
    ctrl.register(ALICE, "ALICE_TOK")
    meta = {"sender": ALICE, "count": 42, "flag": True}
    result = ctrl.mask_meta(meta)
    assert result["sender"] == "ALICE_TOK"
    assert result["count"] == 42  # non-strings preserved
    assert result["flag"] is True


# ---------- 9-10: unmask ----------

async def test_unmask_returns_jid_for_known_token():
    ctrl = _make_ctrl()
    ctrl.register(ALICE, "ALICE_TOK")
    assert ctrl.unmask("ALICE_TOK") == ALICE


async def test_unmask_returns_token_unchanged_if_unknown():
    ctrl = _make_ctrl()
    assert ctrl.unmask("unknown-tok") == "unknown-tok"


# ---------- 11-14: has_role / is_host ----------

async def test_has_role_true_for_static():
    ctrl = _make_ctrl()
    assert ctrl.has_role(HOST, "hosts") is True


async def test_has_role_false_for_unknown():
    ctrl = _make_ctrl()
    assert ctrl.has_role(ALICE, "hosts") is False


async def test_is_host_true_for_configured_host():
    ctrl = _make_ctrl()
    assert ctrl.is_host(HOST) is True


async def test_is_host_false_for_non_host():
    ctrl = _make_ctrl()
    assert ctrl.is_host(ALICE) is False


# ---------- 15-18: is_participant / is_elevated ----------

async def test_is_participant_false_before_grant():
    ctrl = _make_ctrl()
    assert ctrl.is_participant(ALICE) is False


async def test_is_participant_true_after_grant():
    ctrl = _make_ctrl()
    ctrl.grant("session_participants", [{"jid": ALICE, "token": "Alice"}])
    assert ctrl.is_participant(ALICE) is True


async def test_is_elevated_false_before_grant():
    ctrl = _make_ctrl()
    assert ctrl.is_elevated(ALICE) is False


async def test_is_elevated_true_after_grant_jid():
    ctrl = _make_ctrl()
    ctrl.grant_jid("elevated_participants", ALICE)
    assert ctrl.is_elevated(ALICE) is True


# ---------- 19-23: can_reach ----------

async def test_can_reach_host_dm_allowed():
    ctrl = _make_ctrl()
    assert ctrl.can_reach(HOST, False, "hello") is True


async def test_can_reach_non_host_dm_blocked():
    ctrl = _make_ctrl()
    assert ctrl.can_reach(ALICE, False, "hello") is False


async def test_can_reach_host_command_start_allowed():
    ctrl = _make_ctrl()
    assert ctrl.can_reach(HOST, False, "/start") is True


async def test_can_reach_non_host_command_start_blocked():
    ctrl = _make_ctrl()
    assert ctrl.can_reach(ALICE, False, "/start") is False


async def test_can_reach_participant_in_group_after_grant():
    ctrl = _make_ctrl(group=["session_participants"])
    ctrl.grant("session_participants", [{"jid": ALICE, "token": "Alice"}])
    ctrl.grant("session_group", [{"jid": GROUP, "token": "group1"}])
    assert ctrl.can_reach(ALICE, True, "hello") is True


# ---------- 24-27: grant / revoke ----------

async def test_grant_merges_entries():
    ctrl = _make_ctrl()
    ctrl.grant("session_participants", [{"jid": ALICE, "token": "Alice"}])
    ctrl.grant("session_participants", [{"jid": BOB, "token": "Bob"}])
    assert ctrl.is_participant(ALICE) is True
    assert ctrl.is_participant(BOB) is True


async def test_grant_jid_adds_single_jid():
    ctrl = _make_ctrl()
    ctrl.grant_jid("elevated_participants", ALICE)
    assert ALICE in ctrl._dynamic["elevated_participants"]


async def test_revoke_removes_dynamic_role():
    ctrl = _make_ctrl()
    ctrl.grant("session_participants", [{"jid": ALICE, "token": "Alice"}])
    assert ctrl.is_participant(ALICE) is True
    ctrl.revoke("session_participants")
    assert ctrl.is_participant(ALICE) is False


async def test_revoke_all_session_clears_all_session_roles():
    ctrl = _make_ctrl()
    ctrl.grant("session_participants", [{"jid": ALICE, "token": "Alice"}])
    ctrl.grant_jid("elevated_participants", ALICE)
    ctrl.grant("session_group", [{"jid": GROUP, "token": "group1"}])
    ctrl.revoke_all_session()
    assert ctrl.is_participant(ALICE) is False
    assert ctrl.is_elevated(ALICE) is False
    assert "session_group" not in ctrl._dynamic


# ---------- 28-30: on_message flow ----------

async def test_on_message_host_dm_notified(tmp_path):
    ctrl = _make_ctrl()
    core, wa, notified = _make_core(ctrl, tmp_path)
    msg = _msg("hi from host", sender=HOST)
    await core.on_message(msg)
    assert len(notified) == 1
    assert notified[0][1]["type"] == "message"


async def test_on_message_unknown_sender_dropped(tmp_path):
    ctrl = _make_ctrl()
    core, wa, notified = _make_core(ctrl, tmp_path)
    msg = _msg("hi from stranger", sender=ALICE)
    await core.on_message(msg)
    assert len(notified) == 0


async def test_on_message_catchup_buffered(tmp_path):
    ctrl = _make_ctrl()
    core, wa, notified = _make_core(ctrl, tmp_path)
    msg = _msg("catchup msg", sender=HOST, catchup=True)
    await core.on_message(msg)
    assert len(notified) == 0
    assert len(core._catchup_buffer) == 1
    assert core._catchup_buffer[0].text == "catchup msg"
