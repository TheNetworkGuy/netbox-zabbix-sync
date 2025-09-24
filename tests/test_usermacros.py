import unittest
from unittest.mock import MagicMock, patch
from modules.device import PhysicalDevice
from modules.usermacros import ZabbixUsermacros

class DummyNB:
    def __init__(self, name="dummy", config_context=None, **kwargs):
        self.name = name
        self.config_context = config_context or {}
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        # Allow dict-style access for test compatibility
        if hasattr(self, key):
            return getattr(self, key)
        if key in self.config_context:
            return self.config_context[key]
        raise KeyError(key)

class TestUsermacroSync(unittest.TestCase):
    def setUp(self):
        self.nb = DummyNB(serial="1234")
        self.logger = MagicMock()
        self.usermacro_map = {"serial": "{$HW_SERIAL}"}

    @patch("modules.device.config", {"usermacro_sync": False})
    def test_usermacro_sync_false(self):
        device = PhysicalDevice.__new__(PhysicalDevice)
        device.nb = self.nb
        device.logger = self.logger
        device.name = "dummy"
        device._usermacro_map = MagicMock(return_value=self.usermacro_map)
        # call set_usermacros
        result = device.set_usermacros()
        self.assertEqual(device.usermacros, [])
        self.assertTrue(result is True or result is None)

    @patch("modules.device.config", {"usermacro_sync": True})
    def test_usermacro_sync_true(self):
        device = PhysicalDevice.__new__(PhysicalDevice)
        device.nb = self.nb
        device.logger = self.logger
        device.name = "dummy"
        device._usermacro_map = MagicMock(return_value=self.usermacro_map)
        result = device.set_usermacros()
        self.assertIsInstance(device.usermacros, list)
        self.assertGreater(len(device.usermacros), 0)

    @patch("modules.device.config", {"usermacro_sync": "full"})
    def test_usermacro_sync_full(self):
        device = PhysicalDevice.__new__(PhysicalDevice)
        device.nb = self.nb
        device.logger = self.logger
        device.name = "dummy"
        device._usermacro_map = MagicMock(return_value=self.usermacro_map)
        result = device.set_usermacros()
        self.assertIsInstance(device.usermacros, list)
        self.assertGreater(len(device.usermacros), 0)

class TestZabbixUsermacros(unittest.TestCase):
    def setUp(self):
        self.nb = DummyNB()
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
        macro = macros.render_macro("{$FOO}", {"value": "bar", "type": "secret", "description": "desc"})
        self.assertEqual(macro["macro"], "{$FOO}")
        self.assertEqual(macro["value"], "bar")
        self.assertEqual(macro["type"], "1")
        self.assertEqual(macro["description"], "desc")

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
        nb = DummyNB(memory="bar", role="baz")
        usermacro_map = {"memory": "{$FOO}", "role": "{$BAR}"}
        macros = ZabbixUsermacros(nb, usermacro_map, True, logger=self.logger)
        result = macros.generate()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["macro"], "{$FOO}")
        self.assertEqual(result[1]["macro"], "{$BAR}")

    def test_generate_from_config_context(self):
        config_context = {"zabbix": {"usermacros": {"{$FOO}": {"value": "bar"}}}}
        nb = DummyNB(config_context=config_context)
        macros = ZabbixUsermacros(nb, {}, True, logger=self.logger)
        result = macros.generate()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["macro"], "{$FOO}")

if __name__ == "__main__":
    unittest.main()
