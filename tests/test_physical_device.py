"""Tests for the PhysicalDevice class in the device module."""

import unittest
from unittest.mock import MagicMock, patch

from modules.device import PhysicalDevice
from modules.exceptions import TemplateError


class TestPhysicalDevice(unittest.TestCase):
    """Test class for PhysicalDevice functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock NetBox device
        self.mock_nb_device = MagicMock()
        self.mock_nb_device.id = 123
        self.mock_nb_device.name = "test-device"
        self.mock_nb_device.status.label = "Active"
        self.mock_nb_device.custom_fields = {"zabbix_hostid": None}
        self.mock_nb_device.config_context = {}

        # Set up a primary IP
        primary_ip = MagicMock()
        primary_ip.address = "192.168.1.1/24"
        self.mock_nb_device.primary_ip = primary_ip

        # Create mock Zabbix API
        self.mock_zabbix = MagicMock()
        self.mock_zabbix.version = "6.0"

        # Mock NetBox journal class
        self.mock_nb_journal = MagicMock()

        # Create logger mock
        self.mock_logger = MagicMock()

        # Create PhysicalDevice instance with mocks
        with patch(
            "modules.device.config",
            {
                "device_cf": "zabbix_hostid",
                "template_cf": "zabbix_template",
                "templates_config_context": False,
                "templates_config_context_overrule": False,
                "traverse_regions": False,
                "traverse_site_groups": False,
                "inventory_mode": "disabled",
                "inventory_sync": False,
                "device_inventory_map": {},
            },
        ):
            self.device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                journal=True,
                logger=self.mock_logger,
            )

    def test_init(self):
        """Test the initialization of the PhysicalDevice class."""
        # Check that basic properties are set correctly
        self.assertEqual(self.device.name, "test-device")
        self.assertEqual(self.device.id, 123)
        self.assertEqual(self.device.status, "Active")
        self.assertEqual(self.device.ip, "192.168.1.1")
        self.assertEqual(self.device.cidr, "192.168.1.1/24")

    def test_set_basics_with_special_characters(self):
        """Test _setBasics when device name contains special characters."""
        # Set name with special characters that
        # will actually trigger the special character detection
        self.mock_nb_device.name = "test-devïce"

        # We need to patch the search function to simulate finding special characters
        with (
            patch("modules.device.search") as mock_search,
            patch("modules.device.config", {"device_cf": "zabbix_hostid"}),
        ):
            # Make the search function return True to simulate special characters
            mock_search.return_value = True

            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # With the mocked search function, the name should be changed to NETBOX_ID format
        self.assertEqual(device.name, f"NETBOX_ID{self.mock_nb_device.id}")
        # And visible_name should be set to the original name
        self.assertEqual(device.visible_name, "test-devïce")
        # use_visible_name flag should be set
        self.assertTrue(device.use_visible_name)

    def test_get_templates_context(self):
        """Test get_templates_context with valid config."""
        # Set up config_context with valid template data
        self.mock_nb_device.config_context = {
            "zabbix": {"templates": ["Template1", "Template2"]}
        }

        # Create device with the updated mock
        with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # Test that templates are returned correctly
        templates = device.get_templates_context()
        self.assertEqual(templates, ["Template1", "Template2"])

    def test_get_templates_context_with_string(self):
        """Test get_templates_context with a string instead of list."""
        # Set up config_context with a string template
        self.mock_nb_device.config_context = {"zabbix": {"templates": "Template1"}}

        # Create device with the updated mock
        with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # Test that template is wrapped in a list
        templates = device.get_templates_context()
        self.assertEqual(templates, ["Template1"])

    def test_get_templates_context_no_zabbix_key(self):
        """Test get_templates_context when zabbix key is missing."""
        # Set up config_context without zabbix key
        self.mock_nb_device.config_context = {}

        # Create device with the updated mock
        with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # Test that TemplateError is raised
        with self.assertRaises(TemplateError):
            device.get_templates_context()

    def test_get_templates_context_no_templates_key(self):
        """Test get_templates_context when templates key is missing."""
        # Set up config_context without templates key
        self.mock_nb_device.config_context = {"zabbix": {}}

        # Create device with the updated mock
        with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # Test that TemplateError is raised
        with self.assertRaises(TemplateError):
            device.get_templates_context()

    def test_set_template_with_config_context(self):
        """Test set_template with templates_config_context=True."""
        # Set up config_context with templates
        self.mock_nb_device.config_context = {"zabbix": {"templates": ["Template1"]}}

        # Mock get_templates_context to return expected templates
        with patch.object(
            PhysicalDevice, "get_templates_context", return_value=["Template1"]
        ):
            with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
                device = PhysicalDevice(
                    self.mock_nb_device,
                    self.mock_zabbix,
                    self.mock_nb_journal,
                    "3.0",
                    logger=self.mock_logger,
                )

            # Call set_template with prefer_config_context=True
            result = device.set_template(
                prefer_config_context=True, overrule_custom=False
            )

            # Check result and template names
            self.assertTrue(result)
            self.assertEqual(device.zbx_template_names, ["Template1"])

    def test_set_inventory_disabled_mode(self):
        """Test set_inventory with inventory_mode=disabled."""
        # Configure with disabled inventory mode
        config_patch = {
            "device_cf": "zabbix_hostid",
            "inventory_mode": "disabled",
            "inventory_sync": False,
        }

        with patch("modules.device.config", config_patch):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

            # Call set_inventory with the config patch still active
            with patch("modules.device.config", config_patch):
                result = device.set_inventory({})

            # Check result
            self.assertTrue(result)
            # Default value for disabled inventory
            self.assertEqual(device.inventory_mode, -1)

    def test_set_inventory_manual_mode(self):
        """Test set_inventory with inventory_mode=manual."""
        # Configure with manual inventory mode
        config_patch = {
            "device_cf": "zabbix_hostid",
            "inventory_mode": "manual",
            "inventory_sync": False,
        }

        with patch("modules.device.config", config_patch):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

            # Call set_inventory with the config patch still active
            with patch("modules.device.config", config_patch):
                result = device.set_inventory({})

            # Check result
            self.assertTrue(result)
            self.assertEqual(device.inventory_mode, 0)  # Manual mode

    def test_set_inventory_automatic_mode(self):
        """Test set_inventory with inventory_mode=automatic."""
        # Configure with automatic inventory mode
        config_patch = {
            "device_cf": "zabbix_hostid",
            "inventory_mode": "automatic",
            "inventory_sync": False,
        }

        with patch("modules.device.config", config_patch):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

            # Call set_inventory with the config patch still active
            with patch("modules.device.config", config_patch):
                result = device.set_inventory({})

            # Check result
            self.assertTrue(result)
            self.assertEqual(device.inventory_mode, 1)  # Automatic mode

    def test_set_inventory_with_inventory_sync(self):
        """Test set_inventory with inventory_sync=True."""
        # Configure with inventory sync enabled
        config_patch = {
            "device_cf": "zabbix_hostid",
            "inventory_mode": "manual",
            "inventory_sync": True,
            "device_inventory_map": {"name": "name", "serial": "serialno_a"},
        }

        with patch("modules.device.config", config_patch):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

            # Create a mock device with the required attributes
            mock_device_data = {"name": "test-device", "serial": "ABC123"}

            # Call set_inventory with the config patch still active
            with patch("modules.device.config", config_patch):
                result = device.set_inventory(mock_device_data)

            # Check result
            self.assertTrue(result)
            self.assertEqual(device.inventory_mode, 0)  # Manual mode
            self.assertEqual(
                device.inventory, {"name": "test-device", "serialno_a": "ABC123"}
            )

    def test_iscluster_true(self):
        """Test isCluster when device is part of a cluster."""
        # Set up virtual_chassis
        self.mock_nb_device.virtual_chassis = MagicMock()

        # Create device with the updated mock
        with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # Check isCluster result
        self.assertTrue(device.is_cluster())

    def test_is_cluster_false(self):
        """Test isCluster when device is not part of a cluster."""
        # Set virtual_chassis to None
        self.mock_nb_device.virtual_chassis = None

        # Create device with the updated mock
        with patch("modules.device.config", {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                logger=self.mock_logger,
            )

        # Check isCluster result
        self.assertFalse(device.is_cluster())

    def test_promote_master_device_primary(self):
        """Test promoteMasterDevice when device is primary in cluster."""
        # Set up virtual chassis with master device
        mock_vc = MagicMock()
        mock_vc.name = "virtual-chassis-1"
        mock_master = MagicMock()
        mock_master.id = (
            self.mock_nb_device.id
        )  # Set master ID to match the current device
        mock_vc.master = mock_master
        self.mock_nb_device.virtual_chassis = mock_vc

        # Create device with the updated mock
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
        )

        # Call promoteMasterDevice and check the result
        result = device.promote_primary_device()

        # Should return True for primary device
        self.assertTrue(result)
        # Device name should be updated to virtual chassis name
        self.assertEqual(device.name, "virtual-chassis-1")

    def test_promote_master_device_secondary(self):
        """Test promoteMasterDevice when device is secondary in cluster."""
        # Set up virtual chassis with a different master device
        mock_vc = MagicMock()
        mock_vc.name = "virtual-chassis-1"
        mock_master = MagicMock()
        mock_master.id = (
            self.mock_nb_device.id + 1
        )  # Different ID than the current device
        mock_vc.master = mock_master
        self.mock_nb_device.virtual_chassis = mock_vc

        # Create device with the updated mock
        device = PhysicalDevice(
            self.mock_nb_device,
            self.mock_zabbix,
            self.mock_nb_journal,
            "3.0",
            logger=self.mock_logger,
        )

        # Call promoteMasterDevice and check the result
        result = device.promote_primary_device()

        # Should return False for secondary device
        self.assertFalse(result)
        # Device name should not be modified
        self.assertEqual(device.name, "test-device")
