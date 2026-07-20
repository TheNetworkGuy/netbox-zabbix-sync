"""Tests for the custom Jinja2 filters offered to config context rendering.

These are the filters users reach for when a config context has to reshape
NetBox data before it becomes a Zabbix macro or item. They are registered by
name into the Jinja environment (tools.py:89), so a rename here is a silent
break in every config context that used the old name -- worth pinning even
where the implementation is a one-liner.

The functional suite proves the filters are *loaded*; these prove they are
correct, including the edge cases a live sync would only hit occasionally.
"""

import pytest

from netbox_zabbix_sync.modules.jinja_filters import (
    dict_keys,
    json_path,
    regex_replace,
    regex_search,
    strftime,
)


class TestDictKeys:
    """dict_keys filters a list of dicts down to the named keys."""

    def test_single_key_as_string_is_accepted(self):
        """A bare string is treated as a one-key list, not iterated per char."""
        data = [{"name": "eth0", "type": "1000base-t"}]
        assert dict_keys(data, "name") == [{"name": "eth0"}]

    def test_multiple_keys(self):
        data = [
            {"name": "eth0", "type": "1000base-t", "enabled": True},
            {"name": "eth1", "type": "10gbase-x-sfpp", "enabled": False},
        ]
        assert dict_keys(data, ["name", "type"]) == [
            {"name": "eth0", "type": "1000base-t"},
            {"name": "eth1", "type": "10gbase-x-sfpp"},
        ]

    def test_missing_keys_are_omitted_rather_than_nulled(self):
        """A dict lacking the key contributes only what it has."""
        data = [{"name": "eth0"}, {"name": "eth1", "type": "virtual"}]
        assert dict_keys(data, ["name", "type"]) == [
            {"name": "eth0"},
            {"name": "eth1", "type": "virtual"},
        ]

    def test_falsy_values_are_dropped(self):
        """Documents a real edge: the filter keeps truthy values only.

        `enabled: False` and an empty name are dropped rather than carried
        through, so a config context cannot use this filter to render a
        boolean. Pinned because it is surprising, not because it is desirable.
        """
        data = [{"name": "eth0", "enabled": False, "mtu": 0}]
        assert dict_keys(data, ["name", "enabled", "mtu"]) == [{"name": "eth0"}]

    def test_dict_reduced_to_nothing_is_dropped_entirely(self):
        data = [{"name": "eth0"}, {"other": "value"}]
        assert dict_keys(data, ["name"]) == [{"name": "eth0"}]

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param([], id="empty-list"),
            pytest.param([{"other": "value"}], id="no-matching-keys"),
            pytest.param("not-a-list", id="not-a-list"),
            pytest.param({"name": "eth0"}, id="bare-dict"),
        ],
    )
    def test_returns_empty_string_when_nothing_matches(self, data):
        """The no-op return is `""`, not `[]` or None.

        It is what lands in the rendered JSON, so it has to stay a value the
        json.loads round trip survives (tools.py:91-93).
        """
        assert dict_keys(data, ["name"]) == ""

    def test_no_keys_requested_is_a_noop(self):
        assert dict_keys([{"name": "eth0"}]) == ""


class TestJsonPath:
    """json_path exposes JSONPath queries to a config context."""

    def test_finds_nested_values(self):
        data = {"interfaces": [{"name": "eth0"}, {"name": "eth1"}]}
        assert json_path(data, "$.interfaces[*].name") == ["eth0", "eth1"]

    def test_filter_expression(self):
        data = {
            "interfaces": [
                {"name": "eth0", "enabled": True},
                {"name": "eth1", "enabled": False},
            ]
        }
        assert json_path(data, "$.interfaces[?@.enabled == true].name") == ["eth0"]

    def test_no_match_returns_empty_list(self):
        assert json_path({"a": 1}, "$.nope") == []


class TestRegexReplace:
    def test_replaces_all_occurrences(self):
        assert regex_replace("a-b-c", "-", "_") == "a_b_c"

    def test_anchored_pattern(self):
        assert regex_replace("SN-ABC-123", "^SN-", "") == "ABC-123"

    def test_capture_group_backreference(self):
        assert regex_replace("host01.example.com", r"^([^.]+)\..*$", r"\1") == "host01"

    def test_no_match_returns_input_unchanged(self):
        assert regex_replace("hostname", "^SN-", "") == "hostname"


class TestRegexSearch:
    def test_returns_the_match_only(self):
        assert regex_search("device-42-rack", r"\d+") == "42"

    def test_no_match_returns_empty_string(self):
        """`""` rather than None: the result is rendered into JSON."""
        assert regex_search("device", r"\d+") == ""

    def test_returns_first_match(self):
        assert regex_search("a1 b2", r"[a-z]\d") == "a1"


class TestStrftime:
    def test_default_format(self):
        assert strftime("2024-03-01T14:30:00Z") == "2024-03-01 14:30:00"

    def test_custom_format(self):
        assert strftime("2024-03-01T14:30:00Z", "%d/%m/%Y") == "01/03/2024"

    def test_timezone_is_dropped_not_converted(self):
        """The offset is discarded and the wall-clock time kept as written.

        `date.replace(tzinfo=None)` drops the zone without converting, so a
        NetBox timestamp renders as its original local time. Worth pinning: the
        alternative reading -- that it converts to UTC -- would shift every
        rendered timestamp by the offset.
        """
        assert strftime("2024-03-01T14:30:00+02:00") == "2024-03-01 14:30:00"

    def test_parses_a_netbox_style_timestamp(self):
        """NetBox serialises `last_updated` with microseconds and an offset."""
        assert strftime("2024-03-01T14:30:00.123456+00:00", "%Y-%m-%d") == "2024-03-01"

    def test_invalid_date_raises(self):
        """A bad date is an error, not a silent empty string.

        The raise is what core.py turns into a skipped host with a logged
        reason (core.py:198), rather than a host synced with a wrong value.
        """
        with pytest.raises(ValueError, match="Unknown string format"):
            strftime("not-a-date")
