"""Tests for list-based hostgroup formats in configuration."""

import unittest
from unittest.mock import MagicMock

from netbox_zabbix_sync.modules.exceptions import HostgroupError
from netbox_zabbix_sync.modules.hostgroups import Hostgroup
from netbox_zabbix_sync.modules.tools import verify_hg_format
from tests.fakes import FakeNetBox


class TestListHostgroupFormats(unittest.TestCase):
    """Test class for list-based hostgroup format functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_logger = MagicMock()
        self.netbox = FakeNetBox()
        nb = self.netbox

        site = nb.site("TestSite", region=nb.region("TestRegion"))
        role = nb.role("TestRole")
        platform = nb.platform("TestPlatform")

        self.mock_device = nb.device(
            "test-device",
            site=site,
            role=role,
            platform=platform,
            rack=nb.rack("TestRack", site=site),
            device_type=nb.device_type("TestModel", manufacturer="TestManufacturer"),
        )
        self.mock_vm = nb.virtual_machine(
            "test-vm",
            site=site,
            role=role,
            platform=platform,
            cluster=nb.cluster("TestCluster", type="TestClusterType"),
        )

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
