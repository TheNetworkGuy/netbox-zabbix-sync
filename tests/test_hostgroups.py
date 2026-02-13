"""Tests for the Hostgroup class in the hostgroups module."""

import unittest
from unittest.mock import MagicMock, patch

from netbox_zabbix_sync.modules.exceptions import HostgroupError
from netbox_zabbix_sync.modules.hostgroups import Hostgroup


class TestHostgroups(unittest.TestCase):
    """Test class for Hostgroup functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock logger
        self.mock_logger = MagicMock()

        # *** Mock NetBox Device setup ***
        # Create mock device with all properties
        self.mock_device = MagicMock()
        self.mock_device.name = "test-device"

        # Set up site information
        site = MagicMock()
        site.name = "TestSite"

        # Set up region information
        region = MagicMock()
        region.name = "TestRegion"
        # Ensure region string representation returns the name
        region.__str__.return_value = "TestRegion"
        site.region = region

        # Set up site group information
        site_group = MagicMock()
        site_group.name = "TestSiteGroup"
        # Ensure site group string representation returns the name
        site_group.__str__.return_value = "TestSiteGroup"
        site.group = site_group

        self.mock_device.site = site

        # Set up role information (varies based on NetBox version)
        self.mock_device_role = MagicMock()
        self.mock_device_role.name = "TestRole"
        # Ensure string representation returns the name
        self.mock_device_role.__str__.return_value = "TestRole"
        self.mock_device.device_role = self.mock_device_role
        self.mock_device.role = self.mock_device_role

        # Set up tenant information
        tenant = MagicMock()
        tenant.name = "TestTenant"
        # Ensure tenant string representation returns the name
        tenant.__str__.return_value = "TestTenant"
        tenant_group = MagicMock()
        tenant_group.name = "TestTenantGroup"
        # Ensure tenant group string representation returns the name
        tenant_group.__str__.return_value = "TestTenantGroup"
        tenant.group = tenant_group
        self.mock_device.tenant = tenant

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

        location = MagicMock()
        location.name = "TestLocation"
        # Ensure location string representation returns the name
        location.__str__.return_value = "TestLocation"
        self.mock_device.location = location

        # Custom fields
        self.mock_device.custom_fields = {"test_cf": "TestCF"}

        # *** Mock NetBox VM setup ***
        # Create mock VM with all properties
        self.mock_vm = MagicMock()
        self.mock_vm.name = "test-vm"

        # Reuse site from device
        self.mock_vm.site = site

        # Set up role for VM
        self.mock_vm.role = self.mock_device_role

        # Set up tenant for VM (same as device)
        self.mock_vm.tenant = tenant

        # Set up platform for VM (same as device)
        self.mock_vm.platform = platform

        # VM-specific properties
        cluster = MagicMock()
        cluster.name = "TestCluster"
        cluster_type = MagicMock()
        cluster_type.name = "TestClusterType"
        cluster.type = cluster_type
        self.mock_vm.cluster = cluster

        # Custom fields
        self.mock_vm.custom_fields = {"test_cf": "TestCF"}

        # Mock data for nesting tests
        self.mock_regions_data = [
            {"name": "ParentRegion", "parent": None, "_depth": 0},
            {"name": "TestRegion", "parent": "ParentRegion", "_depth": 1},
        ]

        self.mock_groups_data = [
            {"name": "ParentSiteGroup", "parent": None, "_depth": 0},
            {"name": "TestSiteGroup", "parent": "ParentSiteGroup", "_depth": 1},
        ]

    def test_device_hostgroup_creation(self):
        """Test basic device hostgroup creation."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Test the string representation
        self.assertEqual(str(hostgroup), "Hostgroup for dev test-device")

        # Check format options were set correctly
        self.assertEqual(hostgroup.format_options["site"], "TestSite")
        self.assertEqual(hostgroup.format_options["region"], "TestRegion")
        self.assertEqual(hostgroup.format_options["site_group"], "TestSiteGroup")
        self.assertEqual(hostgroup.format_options["role"], "TestRole")
        self.assertEqual(hostgroup.format_options["tenant"], "TestTenant")
        self.assertEqual(hostgroup.format_options["tenant_group"], "TestTenantGroup")
        self.assertEqual(hostgroup.format_options["platform"], "TestPlatform")
        self.assertEqual(hostgroup.format_options["manufacturer"], "TestManufacturer")
        self.assertEqual(hostgroup.format_options["location"], "TestLocation")

    def test_vm_hostgroup_creation(self):
        """Test basic VM hostgroup creation."""
        hostgroup = Hostgroup("vm", self.mock_vm, "4.0", self.mock_logger)

        # Test the string representation
        self.assertEqual(str(hostgroup), "Hostgroup for vm test-vm")

        # Check format options were set correctly
        self.assertEqual(hostgroup.format_options["site"], "TestSite")
        self.assertEqual(hostgroup.format_options["region"], "TestRegion")
        self.assertEqual(hostgroup.format_options["site_group"], "TestSiteGroup")
        self.assertEqual(hostgroup.format_options["role"], "TestRole")
        self.assertEqual(hostgroup.format_options["tenant"], "TestTenant")
        self.assertEqual(hostgroup.format_options["tenant_group"], "TestTenantGroup")
        self.assertEqual(hostgroup.format_options["platform"], "TestPlatform")
        self.assertEqual(hostgroup.format_options["cluster"], "TestCluster")
        self.assertEqual(hostgroup.format_options["cluster_type"], "TestClusterType")

    def test_invalid_object_type(self):
        """Test that an invalid object type raises an exception."""
        with self.assertRaises(HostgroupError):
            Hostgroup("invalid", self.mock_device, "4.0", self.mock_logger)

    def test_device_hostgroup_formats(self):
        """Test different hostgroup formats for devices."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Custom format: site/region
        custom_result = hostgroup.generate("site/region")
        self.assertEqual(custom_result, "TestSite/TestRegion")

        # Custom format: site/tenant/platform/location
        complex_result = hostgroup.generate("site/tenant/platform/location")
        self.assertEqual(
            complex_result, "TestSite/TestTenant/TestPlatform/TestLocation"
        )

    def test_vm_hostgroup_formats(self):
        """Test different hostgroup formats for VMs."""
        hostgroup = Hostgroup("vm", self.mock_vm, "4.0", self.mock_logger)

        # Default format: cluster/role
        default_result = hostgroup.generate("cluster/role")
        self.assertEqual(default_result, "TestCluster/TestRole")

        # Custom format: site/tenant
        custom_result = hostgroup.generate("site/tenant")
        self.assertEqual(custom_result, "TestSite/TestTenant")

        # Custom format: cluster/cluster_type/platform
        complex_result = hostgroup.generate("cluster/cluster_type/platform")
        self.assertEqual(complex_result, "TestCluster/TestClusterType/TestPlatform")

    def test_device_netbox_version_differences(self):
        """Test hostgroup generation with different NetBox versions."""
        # NetBox v2.x
        hostgroup_v2 = Hostgroup("dev", self.mock_device, "2.11", self.mock_logger)
        self.assertEqual(hostgroup_v2.format_options["role"], "TestRole")

        # NetBox v3.x
        hostgroup_v3 = Hostgroup("dev", self.mock_device, "3.5", self.mock_logger)
        self.assertEqual(hostgroup_v3.format_options["role"], "TestRole")

        # NetBox v4.x (already tested in other methods)

    def test_custom_field_lookup(self):
        """Test custom field lookup functionality."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Test custom field exists and is populated
        cf_result = hostgroup.custom_field_lookup("test_cf")
        self.assertTrue(cf_result["result"])
        self.assertEqual(cf_result["cf"], "TestCF")

        # Test custom field doesn't exist
        cf_result = hostgroup.custom_field_lookup("nonexistent_cf")
        self.assertFalse(cf_result["result"])
        self.assertIsNone(cf_result["cf"])

    def test_hostgroup_with_custom_field(self):
        """Test hostgroup generation including a custom field."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Generate with custom field included
        result = hostgroup.generate("site/test_cf/role")
        self.assertEqual(result, "TestSite/TestCF/TestRole")

    def test_missing_hostgroup_format_item(self):
        """Test handling of missing hostgroup format items."""
        # Create a device with minimal attributes
        minimal_device = MagicMock()
        minimal_device.name = "minimal-device"
        minimal_device.site = None
        minimal_device.tenant = None
        minimal_device.platform = None
        minimal_device.custom_fields = {}

        # Create role
        role = MagicMock()
        role.name = "MinimalRole"
        minimal_device.role = role

        # Create device_type with manufacturer
        device_type = MagicMock()
        manufacturer = MagicMock()
        manufacturer.name = "MinimalManufacturer"
        device_type.manufacturer = manufacturer
        minimal_device.device_type = device_type

        # Create hostgroup
        hostgroup = Hostgroup("dev", minimal_device, "4.0", self.mock_logger)

        # Generate with default format
        result = hostgroup.generate("site/manufacturer/role")
        # Site is missing, so only manufacturer and role should be included
        self.assertEqual(result, "MinimalManufacturer/MinimalRole")

        # Test with invalid format
        with self.assertRaises(HostgroupError):
            hostgroup.generate("site/nonexistent/role")

    def test_nested_region_hostgroups(self):
        """Test hostgroup generation with nested regions."""
        # Mock the build_path function to return a predictable result
        with patch(
            "netbox_zabbix_sync.modules.hostgroups.build_path"
        ) as mock_build_path:
            # Configure the mock to return a list of regions in the path
            mock_build_path.return_value = ["ParentRegion", "TestRegion"]

            # Create hostgroup with nested regions enabled
            hostgroup = Hostgroup(
                "dev",
                self.mock_device,
                "4.0",
                self.mock_logger,
                nested_region_flag=True,
                nb_regions=self.mock_regions_data,
            )

            # Generate hostgroup with region
            result = hostgroup.generate("site/region/role")
            # Should include the parent region
            self.assertEqual(result, "TestSite/ParentRegion/TestRegion/TestRole")

    def test_nested_sitegroup_hostgroups(self):
        """Test hostgroup generation with nested site groups."""
        # Mock the build_path function to return a predictable result
        with patch(
            "netbox_zabbix_sync.modules.hostgroups.build_path"
        ) as mock_build_path:
            # Configure the mock to return a list of site groups in the path
            mock_build_path.return_value = ["ParentSiteGroup", "TestSiteGroup"]

            # Create hostgroup with nested site groups enabled
            hostgroup = Hostgroup(
                "dev",
                self.mock_device,
                "4.0",
                self.mock_logger,
                nested_sitegroup_flag=True,
                nb_groups=self.mock_groups_data,
            )

            # Generate hostgroup with site_group
            result = hostgroup.generate("site/site_group/role")
            # Should include the parent site group
            self.assertEqual(result, "TestSite/ParentSiteGroup/TestSiteGroup/TestRole")

    def test_vm_list_based_hostgroup_format(self):
        """Test VM hostgroup generation with a list-based format."""
        hostgroup = Hostgroup("vm", self.mock_vm, "4.0", self.mock_logger)

        # Test with a list of format strings
        format_list = ["platform", "role", "cluster_type/cluster"]

        # Generate hostgroups for each format in the list
        hostgroups = []
        for fmt in format_list:
            result = hostgroup.generate(fmt)
            if result:  # Only add non-None results
                hostgroups.append(result)

        # Verify each expected hostgroup is generated
        self.assertEqual(len(hostgroups), 3)  # Should have 3 hostgroups
        self.assertIn("TestPlatform", hostgroups)
        self.assertIn("TestRole", hostgroups)
        self.assertIn("TestClusterType/TestCluster", hostgroups)

    def test_nested_format_splitting(self):
        """Test that formats with slashes correctly split and resolve each component."""
        hostgroup = Hostgroup("vm", self.mock_vm, "4.0", self.mock_logger)

        # Test a format with slashes that should be split
        complex_format = "cluster_type/cluster"
        result = hostgroup.generate(complex_format)

        # Verify the format is correctly split and each component resolved
        self.assertEqual(result, "TestClusterType/TestCluster")

    def test_multiple_hostgroup_formats_device(self):
        """Test device hostgroup generation with multiple formats."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Test with various formats that would be in a list
        formats = [
            "site",
            "manufacturer/role",
            "platform/location",
            "tenant_group/tenant",
        ]

        # Generate and check each format
        results = {}
        for fmt in formats:
            results[fmt] = hostgroup.generate(fmt)

        # Verify results
        self.assertEqual(results["site"], "TestSite")
        self.assertEqual(results["manufacturer/role"], "TestManufacturer/TestRole")
        self.assertEqual(results["platform/location"], "TestPlatform/TestLocation")
        self.assertEqual(results["tenant_group/tenant"], "TestTenantGroup/TestTenant")


if __name__ == "__main__":
    unittest.main()
