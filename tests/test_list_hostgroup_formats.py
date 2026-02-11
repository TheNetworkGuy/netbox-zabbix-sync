"""Tests for list-based hostgroup formats in configuration."""

import unittest
from unittest.mock import MagicMock

from modules.exceptions import HostgroupError
from modules.hostgroups import Hostgroup
from modules.tools import verify_hg_format


class TestListHostgroupFormats(unittest.TestCase):
    """Test class for list-based hostgroup format functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock logger
        self.mock_logger = MagicMock()

        # Create mock device
        self.mock_device = MagicMock()
        self.mock_device.name = "test-device"

        # Set up site information
        site = MagicMock()
        site.name = "TestSite"

        # Set up region information
        region = MagicMock()
        region.name = "TestRegion"
        region.__str__.return_value = "TestRegion"
        site.region = region

        # Set device site
        self.mock_device.site = site

        # Set up role information
        self.mock_device_role = MagicMock()
        self.mock_device_role.name = "TestRole"
        self.mock_device_role.__str__.return_value = "TestRole"
        self.mock_device.role = self.mock_device_role

        # Set up rack information
        rack = MagicMock()
        rack.name = "TestRack"
        self.mock_device.rack = rack

        # Set up platform information
        platform = MagicMock()
        platform.name = "TestPlatform"
        self.mock_device.platform = platform

        # Device-specific properties
        device_type = MagicMock()
        manufacturer = MagicMock()
        manufacturer.name = "TestManufacturer"
        device_type.manufacturer = manufacturer
        self.mock_device.device_type = device_type

        # Create mock VM
        self.mock_vm = MagicMock()
        self.mock_vm.name = "test-vm"

        # Reuse site from device
        self.mock_vm.site = site

        # Set up role for VM
        self.mock_vm.role = self.mock_device_role

        # Set up platform for VM
        self.mock_vm.platform = platform

        # VM-specific properties
        cluster = MagicMock()
        cluster.name = "TestCluster"
        cluster_type = MagicMock()
        cluster_type.name = "TestClusterType"
        cluster.type = cluster_type
        self.mock_vm.cluster = cluster

    def test_verify_list_based_hostgroup_format(self):
        """Test verification of list-based hostgroup formats."""
        # List format with valid items
        valid_format = ["region", "site", "rack"]

        # List format with nested path
        valid_nested_format = ["region", "site/rack"]

        # List format with invalid item
        invalid_format = ["region", "invalid_item", "rack"]

        # Should not raise exception for valid formats
        verify_hg_format(valid_format, hg_type="dev", logger=self.mock_logger)
        verify_hg_format(valid_nested_format, hg_type="dev", logger=self.mock_logger)

        # Should raise exception for invalid format
        with self.assertRaises(HostgroupError):
            verify_hg_format(invalid_format, hg_type="dev", logger=self.mock_logger)

    def test_simulate_hostgroup_generation_from_config(self):
        """Simulate how the main script would generate hostgroups from list-based config."""
        # Mock configuration with list-based hostgroup format
        config_format = ["region", "site", "rack"]
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Simulate the main script's hostgroup generation process
        hostgroups = []
        for fmt in config_format:
            result = hostgroup.generate(fmt)
            if result:
                hostgroups.append(result)

        # Check results
        self.assertEqual(len(hostgroups), 3)
        self.assertIn("TestRegion", hostgroups)
        self.assertIn("TestSite", hostgroups)
        self.assertIn("TestRack", hostgroups)

    def test_vm_hostgroup_format_from_config(self):
        """Test VM hostgroup generation with list-based format."""
        # Mock VM configuration with mixed format
        config_format = ["platform", "role", "cluster_type/cluster"]
        hostgroup = Hostgroup("vm", self.mock_vm, "4.0", self.mock_logger)

        # Simulate the main script's hostgroup generation process
        hostgroups = []
        for fmt in config_format:
            result = hostgroup.generate(fmt)
            if result:
                hostgroups.append(result)

        # Check results
        self.assertEqual(len(hostgroups), 3)
        self.assertIn("TestPlatform", hostgroups)
        self.assertIn("TestRole", hostgroups)
        self.assertIn("TestClusterType/TestCluster", hostgroups)


if __name__ == "__main__":
    unittest.main()
