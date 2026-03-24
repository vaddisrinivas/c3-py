from c3.agent import parse_duration, pick, AccessControl, AccessPolicy, AppManifest, AppConfig

class TestParseDuration:
    def test_integer(self): assert parse_duration(300) == 300
    def test_string_seconds(self): assert parse_duration("30s") == 30
    def test_string_minutes(self): assert parse_duration("5m") == 300
    def test_bare_number_string(self): assert parse_duration("60") == 60
    def test_invalid_with_fallback(self): assert parse_duration("bad", 99) == 99
    def test_invalid_no_fallback(self): assert parse_duration("bad") == 600

class TestPick:
    def test_first_match(self): assert pick({"a": 1, "b": 2}, "a", "b") == 1
    def test_second_match(self): assert pick({"b": 2}, "a", "b") == 2
    def test_no_match(self): assert pick({"c": 3}, "a", "b") is None

class TestAccessControlMasking:
    def test_mask_replaces_jid(self):
        ctrl = AccessControl(AppManifest(name="t", access=AccessPolicy()), AppConfig())
        ctrl.register("123@s.whatsapp.net", "alice")
        assert ctrl.mask("msg from 123@s.whatsapp.net") == "msg from alice"
    def test_unmask_returns_jid(self):
        ctrl = AccessControl(AppManifest(name="t", access=AccessPolicy()), AppConfig())
        ctrl.register("123@s.whatsapp.net", "alice")
        assert ctrl.unmask("alice") == "123@s.whatsapp.net"
