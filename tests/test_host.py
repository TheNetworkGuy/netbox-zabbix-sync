"""Tests for the Host abstract base class, exercised via PhysicalDevice."""

import unittest
from unittest.mock import MagicMock, patch

from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.exceptions import TemplateError


def _make_device(mock_nb_device, mock_zabbix, mock_nb_journal, mock_logger, config=None):
    """Helper to construct a PhysicalDevice with a minimal default config."""
    default_config = {"device_cf": "zabbix_hostid", "preferred_ip": "auto"}
    if config is not None:
        default_config.update(config)
    return PhysicalDevice(
        mock_nb_device,
        mock_zabbix,
        mock_nb_journal,
        "3.0",
        logger=mock_logger,
        config=default_config,
    )


class TestHostInit(unittest.TestCase):
    """Test Host.__init__ and _set_basics via PhysicalDevice."""

    def setUp(self):
        self.mock_nb_device = MagicMock()
        self.mock_nb_device.id = 123
        self.mock_nb_device.name = "test-device"
        self.mock_nb_device.status.label = "Active"
        self.mock_nb_device.custom_fields = {"zabbix_hostid": None}
        self.mock_nb_device.config_context = {}
        self.mock_nb_device.oob_ip = None

        primary_ip = MagicMock()
        primary_ip.address = "192.168.1.1/24"
        self.mock_nb_device.primary_ip = primary_ip
        self.mock_nb_device.primary_ip4 = primary_ip
        self.mock_nb_device.primary_ip6 = None

        self.mock_zabbix = MagicMock()
        self.mock_zabbix.version = "6.0"
        self.mock_nb_journal = MagicMock()
        self.mock_logger = MagicMock()

    def test_init(self):
        """Basic attributes are set correctly from the NetBox record."""
        device = _make_device(
            self.mock_nb_device, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        self.assertEqual(device.name, "test-device")
        self.assertEqual(device.id, 123)
        self.assertEqual(device.status, "Active")
        self.assertEqual(device.ip, "192.168.1.1")
        self.assertEqual(device.cidr, "192.168.1.1/24")
        self.assertIsNone(device.zabbix_id)
        self.assertEqual(device.hostgroup_type, "dev")

    def test_set_basics_with_special_characters(self):
        """Device name with special characters triggers NETBOX_ID renaming."""
        self.mock_nb_device.name = "test-devïce"

        with patch("netbox_zabbix_sync.modules.host.search") as mock_search:
            mock_search.return_value = True
            device = _make_device(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                self.mock_logger,
            )

        self.assertEqual(device.name, f"NETBOX_ID{self.mock_nb_device.id}")
        self.assertEqual(device.visible_name, "test-devïce")
        self.assertTrue(device.use_visible_name)


class TestHostTemplates(unittest.TestCase):
    """Test Host template methods via PhysicalDevice."""

    def setUp(self):
        self.mock_nb_device = MagicMock()
        self.mock_nb_device.id = 1
        self.mock_nb_device.name = "test-device"
        self.mock_nb_device.status.label = "Active"
        self.mock_nb_device.custom_fields = {"zabbix_hostid": None}
        self.mock_nb_device.oob_ip = None

        primary_ip = MagicMock()
        primary_ip.address = "192.168.1.1/24"
        self.mock_nb_device.primary_ip = primary_ip
        self.mock_nb_device.primary_ip4 = primary_ip
        self.mock_nb_device.primary_ip6 = None

        self.mock_zabbix = MagicMock()
        self.mock_nb_journal = MagicMock()
        self.mock_logger = MagicMock()

    def _make(self, config_context):
        self.mock_nb_device.config_context = config_context
        return _make_device(
            self.mock_nb_device, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )

    def test_get_templates_context(self):
        """Valid config context returns the template list."""
        device = self._make({"zabbix": {"templates": ["Template1", "Template2"]}})
        self.assertEqual(device._get_templates_context(), ["Template1", "Template2"])

    def test_get_templates_context_with_string(self):
        """A string template value is wrapped in a list."""
        device = self._make({"zabbix": {"templates": "Template1"}})
        self.assertEqual(device._get_templates_context(), ["Template1"])

    def test_get_templates_context_no_zabbix_key(self):
        """Missing 'zabbix' key raises TemplateError."""
        device = self._make({})
        with self.assertRaises(TemplateError):
            device._get_templates_context()

    def test_get_templates_context_no_templates_key(self):
        """Missing 'templates' key inside 'zabbix' raises TemplateError."""
        device = self._make({"zabbix": {}})
        with self.assertRaises(TemplateError):
            device._get_templates_context()

    def test_set_template_with_config_context(self):
        """prefer_config_context=True uses _get_templates_context."""
        self.mock_nb_device.config_context = {"zabbix": {"templates": ["Template1"]}}
        with patch.object(PhysicalDevice, "_get_templates_context", return_value=["Template1"]):
            device = _make_device(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                self.mock_logger,
            )
            result = device.set_template(prefer_config_context=True, overrule_custom=False)

        self.assertTrue(result)
        self.assertEqual(device.zbx_template_names, ["Template1"])


class TestHostInventory(unittest.TestCase):
    """Test Host.set_inventory via PhysicalDevice."""

    def setUp(self):
        self.mock_nb_device = MagicMock()
        self.mock_nb_device.id = 1
        self.mock_nb_device.name = "test-device"
        self.mock_nb_device.status.label = "Active"
        self.mock_nb_device.custom_fields = {"zabbix_hostid": None}
        self.mock_nb_device.config_context = {}
        self.mock_nb_device.oob_ip = None

        primary_ip = MagicMock()
        primary_ip.address = "192.168.1.1/24"
        self.mock_nb_device.primary_ip = primary_ip
        self.mock_nb_device.primary_ip4 = primary_ip
        self.mock_nb_device.primary_ip6 = None

        self.mock_zabbix = MagicMock()
        self.mock_nb_journal = MagicMock()
        self.mock_logger = MagicMock()

    def test_set_inventory_disabled_mode(self):
        """Disabled inventory mode leaves inventory_mode at -1."""
        device = _make_device(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            self.mock_logger,
            config={"inventory_mode": "disabled", "inventory_sync": False},
        )
        self.assertTrue(device.set_inventory({}))
        self.assertEqual(device.inventory_mode, -1)

    def test_set_inventory_manual_mode(self):
        """Manual inventory mode sets inventory_mode to 0."""
        device = _make_device(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            self.mock_logger,
            config={"inventory_mode": "manual", "inventory_sync": False},
        )
        self.assertTrue(device.set_inventory({}))
        self.assertEqual(device.inventory_mode, 0)

    def test_set_inventory_automatic_mode(self):
        """Automatic inventory mode sets inventory_mode to 1."""
        device = _make_device(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            self.mock_logger,
            config={"inventory_mode": "automatic", "inventory_sync": False},
        )
        self.assertTrue(device.set_inventory({}))
        self.assertEqual(device.inventory_mode, 1)

    def test_set_inventory_with_inventory_sync(self):
        """inventory_sync=True maps NetBox fields to Zabbix inventory fields."""
        device = _make_device(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            self.mock_logger,
            config={
                "inventory_mode": "manual",
                "inventory_sync": True,
                "device_inventory_map": {"name": "name", "serial": "serialno_a"},
            },
        )
        mock_nb_data = {"name": "test-device", "serial": "ABC123"}
        self.assertTrue(device.set_inventory(mock_nb_data))
        self.assertEqual(device.inventory_mode, 0)
        self.assertEqual(device.inventory, {"name": "test-device", "serialno_a": "ABC123"})
