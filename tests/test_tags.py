"""Tests for the ZabbixTags class in the tags module."""

import unittest
from unittest.mock import MagicMock

from netbox_zabbix_sync.modules.tags import ZabbixTags


class DummyNBForTags:
    """Minimal NetBox object that supports field_mapper's dict-style access."""

    def __init__(self, name="test-host", config_context=None, tags=None, site=None):
        self.name = name
        self.config_context = config_context or {}
        self.tags = tags or []
        # Stored as a plain dict so field_mapper can traverse "site/name"
        self.site = site if site is not None else {"name": "TestSite"}

    def __getitem__(self, key):
        return getattr(self, key)


class TestZabbixTagsInit(unittest.TestCase):
    """Tests for ZabbixTags initialisation."""

    def test_sync_true_when_tag_sync_enabled(self):
        """sync flag should be True when tag_sync=True."""
        nb = DummyNBForTags()
        tags = ZabbixTags(nb, tag_map={}, tag_sync=True, logger=MagicMock())
        self.assertTrue(tags.sync)

    def test_sync_false_when_tag_sync_disabled(self):
        """sync flag should be False when tag_sync=False (default)."""
        nb = DummyNBForTags()
        tags = ZabbixTags(nb, tag_map={}, logger=MagicMock())
        self.assertFalse(tags.sync)

    def test_repr_and_str_return_host_name(self):
        nb = DummyNBForTags(name="my-host")
        tags = ZabbixTags(nb, tag_map={}, host="my-host", logger=MagicMock())
        self.assertEqual(repr(tags), "my-host")
        self.assertEqual(str(tags), "my-host")


class TestRenderTag(unittest.TestCase):
    """Tests for ZabbixTags.render_tag()."""

    def setUp(self):
        nb = DummyNBForTags()
        self.logger = MagicMock()
        self.tags = ZabbixTags(
            nb, tag_map={}, tag_sync=True, tag_lower=True, logger=self.logger
        )

    def test_valid_tag_lowercased(self):
        """Valid name+value with tag_lower=True should produce lowercase keys."""
        result = self.tags.render_tag("Site", "Production")
        self.assertEqual(result, {"tag": "site", "value": "production"})

    def test_valid_tag_not_lowercased(self):
        """tag_lower=False should preserve original case."""
        nb = DummyNBForTags()
        tags = ZabbixTags(
            nb, tag_map={}, tag_sync=True, tag_lower=False, logger=self.logger
        )
        result = tags.render_tag("Site", "Production")
        self.assertEqual(result, {"tag": "Site", "value": "Production"})

    def test_invalid_name_none_returns_false(self):
        """None as tag name should return False."""
        result = self.tags.render_tag(None, "somevalue")
        self.assertFalse(result)

    def test_invalid_name_too_long_returns_false(self):
        """Name exceeding 256 characters should return False."""
        long_name = "x" * 257
        result = self.tags.render_tag(long_name, "somevalue")
        self.assertFalse(result)

    def test_invalid_value_none_returns_false(self):
        """None as tag value should return False."""
        result = self.tags.render_tag("site", None)
        self.assertFalse(result)

    def test_invalid_value_empty_string_returns_false(self):
        """Empty string as tag value should return False."""
        result = self.tags.render_tag("site", "")
        self.assertFalse(result)

    def test_invalid_value_too_long_returns_false(self):
        """Value exceeding 256 characters should return False."""
        long_value = "x" * 257
        result = self.tags.render_tag("site", long_value)
        self.assertFalse(result)


class TestGenerateFromTagMap(unittest.TestCase):
    """Tests for the field_mapper-driven tag generation path."""

    def setUp(self):
        self.logger = MagicMock()

    def test_generate_tag_from_field_map(self):
        """Tags derived from tag_map fields are lowercased and returned correctly."""
        nb = DummyNBForTags(name="router01")
        # "site/name" → nb["site"]["name"] → "TestSite", mapped to tag name "site"
        tag_map = {"site/name": "site"}
        tags = ZabbixTags(
            nb,
            tag_map=tag_map,
            tag_sync=True,
            tag_lower=True,
            logger=self.logger,
        )
        result = tags.generate()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tag"], "site")
        self.assertEqual(result[0]["value"], "testsite")

    def test_generate_empty_field_map_produces_no_tags(self):
        """An empty tag_map with no context or NB tags should return an empty list."""
        nb = DummyNBForTags()
        tags = ZabbixTags(nb, tag_map={}, tag_sync=True, logger=self.logger)
        result = tags.generate()
        self.assertEqual(result, [])

    def test_generate_deduplicates_tags(self):
        """Duplicate tags produced by the map should be deduplicated."""
        # Two map entries that resolve to the same tag/value pair
        nb = DummyNBForTags(name="router01")
        tag_map = {"site/name": "site", "site/name": "site"}  # noqa: F601
        tags = ZabbixTags(
            nb,
            tag_map=tag_map,
            tag_sync=True,
            tag_lower=True,
            logger=self.logger,
        )
        result = tags.generate()
        self.assertEqual(len(result), 1)


class TestGenerateFromConfigContext(unittest.TestCase):
    """Tests for the config_context-driven tag generation path."""

    def setUp(self):
        self.logger = MagicMock()

    def test_generates_tags_from_config_context(self):
        """Tags listed in config_context['zabbix']['tags'] are added correctly."""
        nb = DummyNBForTags(
            config_context={
                "zabbix": {
                    "tags": [
                        {"environment": "production"},
                        {"location": "DC1"},
                    ]
                }
            }
        )
        tags = ZabbixTags(
            nb, tag_map={}, tag_sync=True, tag_lower=True, logger=self.logger
        )
        result = tags.generate()
        self.assertEqual(len(result), 2)
        tag_names = [t["tag"] for t in result]
        self.assertIn("environment", tag_names)
        self.assertIn("location", tag_names)

    def test_skips_config_context_tags_with_invalid_values(self):
        """Config context tags with None value should be silently dropped."""
        nb = DummyNBForTags(
            config_context={
                "zabbix": {
                    "tags": [
                        {"environment": None},  # invalid value
                        {"location": "DC1"},
                    ]
                }
            }
        )
        tags = ZabbixTags(
            nb, tag_map={}, tag_sync=True, tag_lower=True, logger=self.logger
        )
        result = tags.generate()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tag"], "location")

    def test_ignores_zabbix_tags_key_missing(self):
        """Missing 'tags' key inside config_context['zabbix'] produces no tags."""
        nb = DummyNBForTags(config_context={"zabbix": {"templates": ["T1"]}})
        tags = ZabbixTags(nb, tag_map={}, tag_sync=True, logger=self.logger)
        result = tags.generate()
        self.assertEqual(result, [])

    def test_ignores_config_context_tags_not_a_list(self):
        """Non-list value for config_context['zabbix']['tags'] produces no tags."""
        nb = DummyNBForTags(config_context={"zabbix": {"tags": "not-a-list"}})
        tags = ZabbixTags(nb, tag_map={}, tag_sync=True, logger=self.logger)
        result = tags.generate()
        self.assertEqual(result, [])


class TestGenerateFromNetboxTags(unittest.TestCase):
    """Tests for the NetBox device tags forwarding path."""

    def setUp(self):
        self.logger = MagicMock()
        # Simulate a list of NetBox tag objects (as dicts, matching real API shape)
        self.nb_tags = [
            {"name": "ping", "slug": "ping", "display": "ping"},
            {"name": "snmp", "slug": "snmp", "display": "snmp"},
        ]

    def test_generates_tags_from_netbox_tags_using_name(self):
        """NetBox device tags are forwarded using tag_name label and tag_value='name'."""
        nb = DummyNBForTags(tags=self.nb_tags)
        tags = ZabbixTags(
            nb,
            tag_map={},
            tag_sync=True,
            tag_lower=True,
            tag_name="NetBox",
            tag_value="name",
            logger=self.logger,
        )
        result = tags.generate()
        self.assertEqual(len(result), 2)
        for t in result:
            self.assertEqual(t["tag"], "netbox")
        values = {t["value"] for t in result}
        self.assertIn("ping", values)
        self.assertIn("snmp", values)

    def test_generates_tags_from_netbox_tags_using_slug(self):
        """tag_value='slug' should use the slug field from each NetBox tag."""
        nb = DummyNBForTags(tags=self.nb_tags)
        tags = ZabbixTags(
            nb,
            tag_map={},
            tag_sync=True,
            tag_lower=False,
            tag_name="NetBox",
            tag_value="slug",
            logger=self.logger,
        )
        result = tags.generate()
        values = {t["value"] for t in result}
        self.assertIn("ping", values)
        self.assertIn("snmp", values)

    def test_generates_tags_from_netbox_tags_default_value_field(self):
        """When tag_value is not a recognised field name, falls back to 'name'."""
        nb = DummyNBForTags(tags=self.nb_tags)
        tags = ZabbixTags(
            nb,
            tag_map={},
            tag_sync=True,
            tag_lower=True,
            tag_name="NetBox",
            tag_value="invalid_field",  # not display/name/slug → fall back to "name"
            logger=self.logger,
        )
        result = tags.generate()
        values = {t["value"] for t in result}
        self.assertIn("ping", values)

    def test_skips_netbox_tags_when_tag_name_not_set(self):
        """NetBox tag forwarding is skipped when tag_name is not configured."""
        nb = DummyNBForTags(tags=self.nb_tags)
        tags = ZabbixTags(
            nb,
            tag_map={},
            tag_sync=True,
            tag_lower=True,
            tag_name=None,
            logger=self.logger,
        )
        result = tags.generate()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
