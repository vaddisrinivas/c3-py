from c3.agent import JidMask, parse_duration, pick


class TestParseDuration:
    def test_integer(self):             assert parse_duration(42) == 42
    def test_float_truncated(self):     assert parse_duration(3.9) == 3
    def test_string_seconds(self):      assert parse_duration("30s") == 30
    def test_string_minutes(self):      assert parse_duration("5m") == 300
    def test_string_bare_digits(self):  assert parse_duration("120") == 120
    def test_none_returns_fallback(self):        assert parse_duration(None) == 600
    def test_none_custom_fallback(self):         assert parse_duration(None, 10) == 10
    def test_empty_string_returns_fallback(self): assert parse_duration("") == 600
    def test_invalid_string_returns_fallback(self): assert parse_duration("abc") == 600
    def test_case_insensitive_M(self):      assert parse_duration("10M") == 600
    def test_string_minutes_lower(self):    assert parse_duration("10m") == 600
    def test_zero(self):                assert parse_duration(0) == 0
    def test_string_zero(self):         assert parse_duration("0") == 0


class TestPick:
    def test_returns_first_present_key(self):    assert pick({"a": 1, "b": 2}, "a", "b") == 1
    def test_returns_second_when_first_missing(self): assert pick({"b": 2}, "a", "b") == 2
    def test_skips_none_values(self):            assert pick({"a": None, "b": 2}, "a", "b") == 2
    def test_returns_none_when_all_missing(self): assert pick({}, "a", "b") is None
    def test_empty_dict(self):                   assert pick({}, "x") is None
    def test_falsy_zero_returned(self):          assert pick({"a": 0}, "a", "b") == 0
    def test_empty_string_returned(self):        assert pick({"a": ""}, "a", "b") == ""


class TestJidMask:
    def test_mask_replaces_jid(self):
        m = JidMask(); m.register("1234@s.whatsapp.net", "host")
        assert m.mask("hi 1234@s.whatsapp.net") == "hi host"

    def test_unmask_returns_jid(self):
        m = JidMask(); m.register("1234@s.whatsapp.net", "host")
        assert m.unmask("host") == "1234@s.whatsapp.net"

    def test_first_token_wins_reverse(self):
        m = JidMask()
        m.register("aaa@s.whatsapp.net", "host"); m.register("bbb@s.whatsapp.net", "host")
        assert m.unmask("host") == "aaa@s.whatsapp.net"

    def test_empty_jid_noop(self):
        m = JidMask(); m.register("", "host")
        assert m.unmask("host") == "host"

    def test_empty_token_noop(self):
        m = JidMask(); m.register("1234@s.whatsapp.net", "")
        assert m.mask("1234@s.whatsapp.net") == "1234@s.whatsapp.net"

    def test_mask_multiple_jids(self):
        m = JidMask()
        m.register("aaa@s.whatsapp.net", "Alice"); m.register("bbb@s.whatsapp.net", "Bob")
        assert m.mask("aaa@s.whatsapp.net and bbb@s.whatsapp.net") == "Alice and Bob"

    def test_alias_affects_forward_only(self):
        m = JidMask()
        m.register("phone@s.whatsapp.net", "host"); m.alias("lid@lid.net", "host")
        assert m.mask("lid@lid.net says hi") == "host says hi"
        assert m.unmask("host") == "phone@s.whatsapp.net"

    def test_alias_on_unregistered_jid(self):
        m = JidMask(); m.alias("lid@lid.net", "host")
        assert m.mask("lid@lid.net") == "host"

    def test_passthrough_when_no_match(self):
        assert JidMask().mask("hello world") == "hello world"

    def test_mask_meta_converts_to_str(self):
        m = JidMask(); m.register("1234@s.whatsapp.net", "host")
        result = m.mask_meta({"jid": "1234@s.whatsapp.net", "count": 5})
        assert result["jid"] == "host" and result["count"] == "5"

    def test_unknown_token_passthrough(self):
        assert JidMask().unmask("unknown") == "unknown"
