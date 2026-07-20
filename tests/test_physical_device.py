"""Tests for PhysicalDevice-specific behaviour (cluster handling, map methods)."""

import unittest
from unittest.mock import MagicMock

from netbox_zabbix_sync.modules.device import PhysicalDevice


class TestPhysicalDevice(unittest.TestCase):
    """Tests for methods that are specific to PhysicalDevice (not inherited from Host)."""

    def setUp(self):
        """Set up a minimal PhysicalDevice instance."""
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
        self.mock_nb_journal = MagicMock()
        self.mock_logger = MagicMock()

        self.device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
            config={"device_cf": "zabbix_hostid", "preferred_ip": "auto"},
        )

    def test_init(self):
        """PhysicalDevice sets hostgroup_type to 'dev'."""
        self.assertEqual(self.device.hostgroup_type, "dev")

    # ------------------------------------------------------------------
    # Map methods
    # ------------------------------------------------------------------

    def test_inventory_map_uses_device_key(self):
        """_inventory_map returns the device-specific inventory map from config."""
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
            config={
                "device_cf": "zabbix_hostid",
                "preferred_ip": "auto",
                "device_inventory_map": {"serial": "serialno_a"},
            },
        )
        self.assertEqual(device._inventory_map(), {"serial": "serialno_a"})

    def test_usermacro_map_uses_device_key(self):
        """_usermacro_map returns the device-specific usermacro map from config."""
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
            config={
                "device_cf": "zabbix_hostid",
                "preferred_ip": "auto",
                "device_usermacro_map": {"{$SITE}": "site.name"},
            },
        )
        self.assertEqual(device._usermacro_map(), {"{$SITE}": "site.name"})

    def test_tag_map_uses_device_key(self):
        """_tag_map returns the device-specific tag map from config."""
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
            config={
                "device_cf": "zabbix_hostid",
                "preferred_ip": "auto",
                "device_tag_map": {"env": "config_context.env"},
            },
        )
        self.assertEqual(device._tag_map(), {"env": "config_context.env"})

    # ------------------------------------------------------------------
    # Cluster detection
    # ------------------------------------------------------------------

    def test_iscluster_true(self):
        """is_cluster returns True when the device has a virtual_chassis."""
        self.mock_nb_device.virtual_chassis = MagicMock()
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
            config={"device_cf": "zabbix_hostid", "preferred_ip": "auto"},
        )
        self.assertTrue(device.is_cluster())

    def test_is_cluster_false(self):
        """is_cluster returns False when virtual_chassis is None."""
        self.mock_nb_device.virtual_chassis = None
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
            config={"device_cf": "zabbix_hostid", "preferred_ip": "auto"},
        )
        self.assertFalse(device.is_cluster())

    # ------------------------------------------------------------------
    # Cluster promotion
    # ------------------------------------------------------------------

    def test_promote_master_device_primary(self):
        """Primary cluster member has its name updated to the virtual chassis name."""
        mock_vc = MagicMock()
        mock_vc.name = "virtual-chassis-1"
        mock_vc.master.id = self.mock_nb_device.id  # same ID → primary
        self.mock_nb_device.virtual_chassis = mock_vc

        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
        )

        self.assertTrue(device.promote_primary_device())
        self.assertEqual(device.name, "virtual-chassis-1")

    def test_promote_master_device_secondary(self):
        """Secondary cluster member is skipped and its name is not modified."""
        mock_vc = MagicMock()
        mock_vc.name = "virtual-chassis-1"
        mock_vc.master.id = self.mock_nb_device.id + 1  # different ID → secondary
        self.mock_nb_device.virtual_chassis = mock_vc

        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
        )

        self.assertFalse(device.promote_primary_device())
        self.assertEqual(device.name, "test-device")
