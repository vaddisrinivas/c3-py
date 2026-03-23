import pytest

from c3.agent import (
    AccessPolicy,
    PluginController,
    PluginManifest,
    _merge_manifests,
)


@pytest.fixture
def ctrl(manifest, app_config):
    return PluginController(manifest, app_config)


class TestCanReachDM:
    def test_host_plain_dm(self, ctrl, host_jid):
        assert ctrl.can_reach(host_jid, False, "hello") is True

    def test_stranger_plain_dm_blocked(self, ctrl):
        assert ctrl.can_reach("stranger@s.whatsapp.net", False, "hello") is False

    def test_host_slash_start_allowed(self, ctrl, host_jid):
        assert ctrl.can_reach(host_jid, False, "/start") is True

    def test_stranger_slash_start_blocked(self, ctrl):
        assert ctrl.can_reach("stranger@s.whatsapp.net", False, "/start") is False

    def test_nobody_allowed_in_group_by_default(self, ctrl, host_jid):
        assert ctrl.can_reach(host_jid, True, "hello") is False


class TestGroupAccess:
    def test_dynamic_role_grants_group_access(self, app_config):
        manifest = PluginManifest(name="test", access=AccessPolicy(commands={}, dm=["hosts"], group=["session_participants"]))
        ctrl = PluginController(manifest, app_config)
        session = ctrl.create_session()
        player_jid = "player@s.whatsapp.net"
        session.grant("session_participants", [{"jid": player_jid, "token": "Player"}])
        assert ctrl.can_reach(player_jid, True, "hello") is True

    def test_revoke_removes_group_access(self, app_config):
        manifest = PluginManifest(name="test", access=AccessPolicy(commands={}, dm=["hosts"], group=["session_participants"]))
        ctrl = PluginController(manifest, app_config)
        session = ctrl.create_session()
        player_jid = "player@s.whatsapp.net"
        session.grant("session_participants", [{"jid": player_jid, "token": "Player"}])
        assert ctrl.can_reach(player_jid, True, "hello") is True
        session.revoke("session_participants")
        assert ctrl.can_reach(player_jid, True, "hello") is False


class TestMergeManifests:
    def test_empty_extras_still_has_default(self):
        assert "hosts" in _merge_manifests([]).access.dm

    def test_extra_dm_roles_added(self):
        extra = {"name": "plugin", "access": {"commands": {}, "dm": ["players"], "group": []}}
        m = _merge_manifests([extra])
        assert "players" in m.access.dm
        assert "hosts" in m.access.dm

    def test_extra_group_roles_added(self):
        extra = {"name": "plugin", "access": {"commands": {}, "dm": [], "group": ["game_group"]}}
        assert "game_group" in _merge_manifests([extra]).access.group

    def test_command_union(self):
        extra = {"name": "p", "access": {"commands": {"/vote": ["players"]}, "dm": [], "group": []}}
        assert "/vote" in _merge_manifests([extra]).access.commands

    def test_name_concatenated(self):
        a = {"name": "A", "access": {"commands": {}, "dm": [], "group": []}}
        b = {"name": "B", "access": {"commands": {}, "dm": [], "group": []}}
        assert _merge_manifests([a, b]).name == "A+B"
