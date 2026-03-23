import pytest

from c3.agent import (
    AccessPolicy,
    AppConfig,
    HostConfig,
    PluginController,
    PluginManifest,
    SessionEngine,
    WAMessage,
)
from tests.conftest import FakeWAAdapter


@pytest.fixture
def host_jid():
    return "host@s.whatsapp.net"

@pytest.fixture
def wa():
    return FakeWAAdapter()

@pytest.fixture
def ctrl(host_jid):
    manifest = PluginManifest(
        name="test",
        access=AccessPolicy(commands={"/start": ["hosts"], "/stop": ["hosts"]}, dm=["hosts"], group=[]),
    )
    return PluginController(manifest, AppConfig(hosts=[HostConfig(jid=host_jid, name="Host")]))

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


def msg(text, sender="host@s.whatsapp.net", jid=None, is_group=False):
    return WAMessage(jid=jid or sender, sender=sender, push_name=sender.split("@")[0], text=text, timestamp=0, is_group=is_group)


class TestHandle:
    async def test_group_message_returns_false(self, engine):
        assert await engine.handle(msg("hello", is_group=True, jid="group@g.us")) is False

    async def test_dm_without_active_session_forwarded(self, engine):
        assert await engine.handle(msg("hello")) is False

    async def test_dm_with_active_session_forwarded(self, engine):
        engine.set_active("group@g.us", "test")
        assert await engine.handle(msg("hello")) is False

    async def test_slash_start_returns_true(self, engine, notified):
        assert await engine.handle(msg("/start")) is True
        assert any(m[1].get("type") == "setup_start" for m in notified)

    async def test_slash_start_passes_args(self, engine, notified):
        await engine.handle(msg("/start mafia https://chat.whatsapp.com/abc"))
        assert "mafia" in notified[-1][1].get("args", "")
        assert "chat.whatsapp.com" in notified[-1][0]

    async def test_slash_stop_clears_and_notifies(self, engine, notified, wa):
        engine.set_active("group@g.us", "test")
        assert await engine.handle(msg("/stop")) is True
        assert len(engine._active_sessions) == 0
        assert any(m[1].get("type") == "session_stop" for m in notified)
        assert any(jid == "host@s.whatsapp.net" for jid, _ in wa.sent)

    async def test_slash_status_sends_to_sender(self, engine, wa):
        assert await engine.handle(msg("/status")) is True
        assert wa.sent
        last_jid, last_text = wa.sent[-1]
        assert last_jid == "host@s.whatsapp.net"
        assert "Session" in last_text or "session" in last_text.lower()


class TestStopPoll:
    async def test_stop_poll_triggers_stop(self, engine, notified, wa):
        await engine.handle(msg("/start"))
        stop_id = list(engine._stop_poll_map.values())[0]
        await engine._wa.on_poll_update(stop_id, {"Stop now": ["host@s.whatsapp.net"]})
        assert any(m[1].get("type") == "session_stop" for m in notified)

    async def test_stop_poll_ignored_when_empty_tally(self, engine, notified):
        await engine.handle(msg("/start"))
        stop_id = list(engine._stop_poll_map.values())[0]
        before = len(notified)
        await engine._wa.on_poll_update(stop_id, {"Stop now": []})
        assert not any(m[1].get("type") == "session_stop" for m in notified[before:])
