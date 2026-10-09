import unittest
from unittest.mock import MagicMock, patch

from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.usermacros import ZabbixUsermacros
from tests.fakes import FakeNetBox, physical_device


class TestUsermacroSync(unittest.TestCase):
    def setUp(self):
        self.netbox = FakeNetBox()
        self.usermacro_map = {"serial": "{$HW_SERIAL}"}

    def create_mock_device(self, **config):
        """A PhysicalDevice for a plain NetBox device."""
        return physical_device(self.netbox.device("dummy", serial="1234"), **config)

    @patch.object(PhysicalDevice, "_usermacro_map")
    def test_usermacro_sync_false(self, mock_usermacro_map):
        mock_usermacro_map.return_value = self.usermacro_map
        device = self.create_mock_device(usermacro_sync=False)

        # Call set_usermacros
        result = device.set_usermacros()

        self.assertEqual(device.usermacros, [])
        self.assertTrue(result is True or result is None)

    @patch("netbox_zabbix_sync.modules.host.ZabbixUsermacros")
    @patch.object(PhysicalDevice, "_usermacro_map")
    def test_usermacro_sync_true(self, mock_usermacro_map, mock_usermacros_class):
        mock_usermacro_map.return_value = self.usermacro_map
        # Mock the ZabbixUsermacros class to return some test data
        mock_macros_instance = MagicMock()
        mock_macros_instance.sync = True  # This is important - sync must be True
        mock_macros_instance.generate.return_value = [
            {"macro": "{$HW_SERIAL}", "value": "1234"}
        ]
        mock_usermacros_class.return_value = mock_macros_instance

        device = self.create_mock_device(usermacro_sync=True)

        # Call set_usermacros
        device.set_usermacros()

        self.assertIsInstance(device.usermacros, list)
        self.assertGreater(len(device.usermacros), 0)

    @patch("netbox_zabbix_sync.modules.host.ZabbixUsermacros")
    @patch.object(PhysicalDevice, "_usermacro_map")
    def test_usermacro_sync_full(self, mock_usermacro_map, mock_usermacros_class):
        mock_usermacro_map.return_value = self.usermacro_map
        # Mock the ZabbixUsermacros class to return some test data
        mock_macros_instance = MagicMock()
        mock_macros_instance.sync = True  # This is important - sync must be True
        mock_macros_instance.generate.return_value = [
            {"macro": "{$HW_SERIAL}", "value": "1234"}
        ]
        mock_usermacros_class.return_value = mock_macros_instance

        device = self.create_mock_device(usermacro_sync="full")

        # Call set_usermacros
        device.set_usermacros()

        self.assertIsInstance(device.usermacros, list)
        self.assertGreater(len(device.usermacros), 0)


class TestZabbixUsermacros(unittest.TestCase):
    def setUp(self):
        self.netbox = FakeNetBox()
        self.nb = self.netbox.device("dummy")
        self.logger = MagicMock()

    def test_validate_macro_valid(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        self.assertTrue(macros.validate_macro("{$TEST_MACRO}"))
        self.assertTrue(macros.validate_macro("{$A1_2.3}"))
        self.assertTrue(macros.validate_macro("{$FOO:bar}"))

    def test_validate_macro_invalid(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        self.assertFalse(macros.validate_macro("$TEST_MACRO"))
        self.assertFalse(macros.validate_macro("{TEST_MACRO}"))
        self.assertFalse(macros.validate_macro("{$test}"))  # lower-case not allowed
        self.assertFalse(macros.validate_macro(""))

    def test_render_macro_dict(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        macro = macros.render_macro(
            "{$FOO}", {"value": "bar", "type": "secret", "description": "desc"}
        )
        self.assertEqual(macro["macro"], "{$FOO}")
        self.assertEqual(macro["value"], "bar")
        self.assertEqual(macro["type"], "1")
        self.assertEqual(macro["description"], "desc")

    def test_render_macro_dict_type_any_case(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        for macro_type, expected in (("Secret", "1"), ("VAULT", "2"), ("Text", "0")):
            macro = macros.render_macro("{$FOO}", {"value": "bar", "type": macro_type})
            self.assertEqual(macro["type"], expected)

    def test_render_macro_dict_invalid_type(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        macro = macros.render_macro("{$FOO}", {"value": "bar", "type": 1})
        self.assertEqual(macro["type"], "0")

    def test_render_macro_dict_missing_value(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        result = macros.render_macro("{$FOO}", {"type": "text"})
        self.assertFalse(result)
        self.logger.info.assert_called()

    def test_render_macro_str(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        macro = macros.render_macro("{$FOO}", "bar")
        self.assertEqual(macro["macro"], "{$FOO}")
        self.assertEqual(macro["value"], "bar")
        self.assertEqual(macro["type"], "0")
        self.assertEqual(macro["description"], "")

    def test_render_macro_invalid_name(self):
        macros = ZabbixUsermacros(self.nb, {}, False, logger=self.logger)
        result = macros.render_macro("FOO", "bar")
        self.assertFalse(result)
        self.logger.warning.assert_called()

    def test_generate_from_map(self):
        nb = self.netbox.virtual_machine(memory=2048, role=self.netbox.role("baz"))
        usermacro_map = {"memory": "{$FOO}", "role/name": "{$BAR}"}
        macros = ZabbixUsermacros(nb, usermacro_map, True, logger=self.logger)
        result = macros.generate()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["macro"], "{$FOO}")
        self.assertEqual(result[0]["value"], "2048")
        self.assertEqual(result[1]["macro"], "{$BAR}")
        self.assertEqual(result[1]["value"], "baz")

    def test_generate_from_config_context(self):
        config_context = {"zabbix": {"usermacros": {"{$TEST_MACRO}": "test_value"}}}
        nb = self.netbox.device(config_context=config_context)
        macros = ZabbixUsermacros(nb, {}, True, logger=self.logger)
        result = macros.generate()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["macro"], "{$TEST_MACRO}")
        self.assertEqual(result[0]["value"], "test_value")


if __name__ == "__main__":
    unittest.main()
