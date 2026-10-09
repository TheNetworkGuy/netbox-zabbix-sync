"""Tests for the Hostgroup class in the hostgroups module."""

import unittest
from unittest.mock import MagicMock, patch

from netbox_zabbix_sync.modules.exceptions import HostgroupError
from netbox_zabbix_sync.modules.hostgroups import Hostgroup, HostgroupFormat
from tests.fakes import FakeNetBox


class TestHostgroups(unittest.TestCase):
    """Test class for Hostgroup functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_logger = MagicMock()
        self.netbox = FakeNetBox()
        nb = self.netbox

        # Shared between the device and the VM, as in a real NetBox
        site = nb.site(
            "TestSite",
            region=nb.region("TestRegion", parent="ParentRegion"),
            group=nb.site_group("TestSiteGroup", parent="ParentSiteGroup"),
        )
        self.role = nb.role("TestRole")
        tenant = nb.tenant("TestTenant", group="TestTenantGroup")
        platform = nb.platform("TestPlatform")

        # *** NetBox Device ***
        # empty_cf is intentionally None to test the empty CF path
        self.mock_device = nb.device(
            "test-device",
            site=site,
            role=self.role,
            tenant=tenant,
            platform=platform,
            device_type=nb.device_type("TestModel", manufacturer="TestManufacturer"),
            location=nb.location("TestLocation", site=site),
            rack=nb.rack("TestRack", site=site),
            custom_fields={"test_cf": "TestCF", "empty_cf": None},
        )

        # *** NetBox VM ***
        self.mock_vm = nb.virtual_machine(
            "test-vm",
            site=site,
            role=self.role,
            tenant=tenant,
            platform=platform,
            cluster=nb.cluster("TestCluster", type="TestClusterType"),
            custom_fields={"test_cf": "TestCF"},
        )

        # What convert_recordset makes of dcim.regions / dcim.site_groups.all()
        self.mock_regions_data = [vars(r) for r in nb.all("dcim/regions")]
        self.mock_groups_data = [vars(g) for g in nb.all("dcim/site-groups")]

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
        self.assertEqual(hostgroup.format_options["rack"], "TestRack")

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

    def test_format_options_match_hostgroup_format(self):
        """Every variable HostgroupFormat allows is resolved, and nothing more.

        HostgroupFormat decides what verification accepts, while
        _set_format_options decides what a host can resolve. If they differ, a
        format passes verification at startup and then fails per host.
        """
        for obj_type, nb_obj in (("dev", self.mock_device), ("vm", self.mock_vm)):
            with self.subTest(obj_type=obj_type):
                hostgroup = Hostgroup(obj_type, nb_obj, "4.0", self.mock_logger)
                self.assertCountEqual(
                    hostgroup.format_options, HostgroupFormat.valid_options(obj_type)
                )

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
        """Test hostgroup generation with different NetBox versions.

        NetBox 2/3 serialise a device's role as `device_role` and 4+ as
        `role`; each payload carries only its own field, so reading the wrong
        one fails instead of silently picking up a value.
        """
        for version, field in (("2.11", "device_role"), ("3.5", "device_role")):
            netbox = FakeNetBox(version=version)
            device = netbox.device(role=netbox.role("OldRole"))
            self.assertIn(field, dict(device))
            self.assertNotIn("role", dict(device))
            hostgroup = Hostgroup("dev", device, version, self.mock_logger)
            self.assertEqual(hostgroup.format_options["role"], "OldRole")

        netbox = FakeNetBox(version="4.0")
        device = netbox.device(role=netbox.role("NewRole"))
        self.assertNotIn("device_role", dict(device))
        hostgroup_v4 = Hostgroup("dev", device, "4.0", self.mock_logger)
        self.assertEqual(hostgroup_v4.format_options["role"], "NewRole")

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

        # Test custom field exists but has no value (None)
        cf_result = hostgroup.custom_field_lookup("empty_cf")
        self.assertTrue(cf_result["result"])  # key is present
        self.assertIsNone(cf_result["cf"])  # value is empty

    def test_hostgroup_with_custom_field(self):
        """Test hostgroup generation including a custom field."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Generate with custom field included
        result = hostgroup.generate("site/test_cf/role")
        self.assertEqual(result, "TestSite/TestCF/TestRole")

    def test_missing_hostgroup_format_item(self):
        """Test handling of missing hostgroup format items."""
        # A device with no site, tenant or platform
        minimal_device = self.netbox.device(
            "minimal-device",
            site=None,
            role=self.netbox.role("MinimalRole"),
            device_type=self.netbox.device_type(
                "Minimal", manufacturer="MinimalManufacturer"
            ),
        )

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

    def test_literal_string_in_format(self):
        """Test that quoted literal strings in a format are used verbatim."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # Single-quoted literal
        result = hostgroup.generate("'MyDevices'/role")
        self.assertEqual(result, "MyDevices/TestRole")

        # Double-quoted literal
        result = hostgroup.generate('"MyDevices"/role')
        self.assertEqual(result, "MyDevices/TestRole")

    def test_generate_returns_none_when_all_fields_empty(self):
        """Test that generate() returns None when every format field resolves to no value."""
        empty_device = self.netbox.device("empty-device", site=None, role=None)

        hostgroup = Hostgroup("dev", empty_device, "4.0", self.mock_logger)
        # site, tenant and platform all have no value → hg_output stays empty → None
        result = hostgroup.generate("site/tenant/platform")
        self.assertIsNone(result)

    def test_vm_without_cluster(self):
        """Test that cluster/cluster_type are left out of the hostgroup when VM has no cluster."""
        clusterless_vm = self.netbox.virtual_machine(
            "clusterless-vm", role=self.role, cluster=None
        )

        hostgroup = Hostgroup("vm", clusterless_vm, "4.0", self.mock_logger)

        # cluster and cluster_type have no value
        self.assertIsNone(hostgroup.format_options["cluster"])
        self.assertIsNone(hostgroup.format_options["cluster_type"])

        # Empty cluster levels are skipped, like other empty fields
        self.assertEqual(hostgroup.generate("cluster_type/cluster/role"), "TestRole")

    def test_tenant_without_group(self):
        """Test that tenant_group is left out when the tenant has no group.

        NetBox returns null for an ungrouped tenant's group; it used to be
        stringified into a "None" hostgroup level.
        """
        device = self.netbox.device("test-device", role=self.role, tenant="TestTenant")

        hostgroup = Hostgroup("dev", device, "4.0", self.mock_logger)

        self.assertIsNone(hostgroup.format_options["tenant_group"])
        self.assertEqual(
            hostgroup.generate("tenant_group/tenant/role"), "TestTenant/TestRole"
        )

    def test_empty_custom_field_skipped_in_format(self):
        """Test that an empty (None) custom field is silently omitted from the hostgroup name."""
        hostgroup = Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        # empty_cf has no value → it is skipped; only site and role appear
        result = hostgroup.generate("site/empty_cf/role")
        self.assertEqual(result, "TestSite/TestRole")

    def test_region_and_site_group_come_from_the_full_site(self):
        """A nested site has no region or group, so reading them loads the site.

        NetBox nests related objects in a brief form; pynetbox fetches the full
        site on first access to a field it lacks. This is the extra request the
        sync makes per device unless extended_site_properties preloads it.
        """
        site_url = self.mock_device.site.url
        self.assertNotIn("region", dict(self.mock_device.site))

        Hostgroup("dev", self.mock_device, "4.0", self.mock_logger)

        self.assertIn(("GET", site_url, None), self.netbox.requests)


if __name__ == "__main__":
    unittest.main()
