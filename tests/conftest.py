import pytest

from c3.agent import (
    AccessPolicy,
    AppConfig,
    ChannelCore,
    GroupMember,
    HostConfig,
    PluginController,
    PluginManifest,
    SessionEngine,
    WAAdapter,
)


class FakeWAAdapter(WAAdapter):
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.polls: list[tuple[str, str, list]] = []
        self._members: list[GroupMember] = [
            GroupMember(jid="alice@s.whatsapp.net", name="Alice", is_admin=False),
            GroupMember(jid="bob@s.whatsapp.net", name="Bob", is_admin=False),
        ]
        self._group_jid    = "group1@g.us"
        self.admin_jid     = "admin@s.whatsapp.net"
        self._poll_counter = 0

    async def connect(self) -> None: pass

    async def send(self, jid: str, text: str) -> None:
        self.sent.append((jid, text))

    async def send_poll(self, jid: str, question: str, options: list[str]) -> str:
        self.polls.append((jid, question, options))
        self._poll_counter += 1
        return f"poll-{self._poll_counter}"

    async def resolve_group(self, invite_link: str) -> str:
        return self._group_jid

    async def get_group_members(self, group_jid: str) -> list[GroupMember]:
        return self._members

    def get_name(self, jid: str) -> str:
        return jid.split("@")[0]


@pytest.fixture
def fake_wa() -> FakeWAAdapter:
    return FakeWAAdapter()

@pytest.fixture
def host_jid() -> str:
    return "host1@s.whatsapp.net"

@pytest.fixture
def app_config(host_jid) -> AppConfig:
    return AppConfig(hosts=[HostConfig(jid=host_jid, name="Host")])

@pytest.fixture
def manifest() -> PluginManifest:
    return PluginManifest(
        name="test",
        access=AccessPolicy(
            commands={"/start": ["hosts", "admins"], "/stop": ["hosts", "admins"]},
            dm=["hosts", "admins"],
            group=[],
        ),
    )

@pytest.fixture
def ctrl(manifest, app_config) -> PluginController:
    return PluginController(manifest, app_config)

@pytest.fixture
def notified() -> list:
    return []

@pytest.fixture
def notify_fn(notified):
    async def _notify(content: str, meta: dict) -> None:
        notified.append((content, meta))
    return _notify

@pytest.fixture
def engine(fake_wa, notify_fn, ctrl) -> SessionEngine:
    return SessionEngine(fake_wa, notify_fn, ctrl)

@pytest.fixture
def core(fake_wa, ctrl, engine, notify_fn, tmp_path) -> ChannelCore:
    session = ctrl.create_session()
    return ChannelCore(fake_wa, ctrl, session, engine, notify_fn, tmp_path)
