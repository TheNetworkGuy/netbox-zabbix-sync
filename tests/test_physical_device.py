"""Tests for PhysicalDevice-specific behaviour (cluster handling, map methods)."""

import unittest

from tests.fakes import FakeNetBox, physical_device


class TestPhysicalDevice(unittest.TestCase):
    """Tests for methods that are specific to PhysicalDevice (not inherited from Host)."""

    def setUp(self):
        """Set up a minimal PhysicalDevice instance."""
        self.netbox = FakeNetBox()
        self.nb = self.netbox.device("test-device", id=123)
        self.device = physical_device(self.nb)

    def test_init(self):
        """PhysicalDevice sets hostgroup_type to 'dev'."""
        self.assertEqual(self.device.hostgroup_type, "dev")

    def test_journal_entry_targets_device(self):
        """Journal entries are assigned to a dcim.device."""
        self.device.journal = True
        self.device.create_journal_entry("info", "test")
        journal = self.netbox.journal_entries.create.call_args.args[0]
        self.assertEqual(journal["assigned_object_type"], "dcim.device")
        self.assertEqual(journal["assigned_object_id"], 123)

    # ------------------------------------------------------------------
    # Map methods
    # ------------------------------------------------------------------

    def test_inventory_map_uses_device_key(self):
        """_inventory_map returns the device-specific inventory map from config."""
        device = physical_device(self.nb, device_inventory_map={"serial": "serialno_a"})
        self.assertEqual(device._inventory_map(), {"serial": "serialno_a"})

    def test_usermacro_map_uses_device_key(self):
        """_usermacro_map returns the device-specific usermacro map from config."""
        device = physical_device(self.nb, device_usermacro_map={"{$SITE}": "site/name"})
        self.assertEqual(device._usermacro_map(), {"{$SITE}": "site/name"})

    def test_tag_map_uses_device_key(self):
        """_tag_map returns the device-specific tag map from config."""
        device = physical_device(self.nb, device_tag_map={"site/name": "site"})
        self.assertEqual(device._tag_map(), {"site/name": "site"})

    # ------------------------------------------------------------------
    # Cluster detection
    # ------------------------------------------------------------------

    def test_iscluster_true(self):
        """is_cluster returns True when the device has a virtual_chassis."""
        vc = self.netbox.virtual_chassis("virtual-chassis-1", master=1)
        device = physical_device(self.netbox.device(virtual_chassis=vc))
        self.assertTrue(device.is_cluster())

    def test_is_cluster_false(self):
        """is_cluster returns False when virtual_chassis is None."""
        self.assertFalse(self.device.is_cluster())

    # ------------------------------------------------------------------
    # Cluster promotion
    # ------------------------------------------------------------------

    def test_promote_master_device_primary(self):
        """Primary cluster member has its name updated to the virtual chassis name."""
        vc = self.netbox.virtual_chassis("virtual-chassis-1", master=7)
        device = physical_device(self.netbox.device(id=7, virtual_chassis=vc))

        self.assertTrue(device.promote_primary_device())
        self.assertEqual(device.name, "virtual-chassis-1")

    def test_promote_master_device_secondary(self):
        """Secondary cluster member is skipped and its name is not modified."""
        vc = self.netbox.virtual_chassis("virtual-chassis-1", master=8)
        device = physical_device(self.netbox.device(id=7, virtual_chassis=vc))

        self.assertFalse(device.promote_primary_device())
        self.assertEqual(device.name, "test-device")

    # ------------------------------------------------------------------
    # adopt_cluster_host
    # ------------------------------------------------------------------

    def _member(self, member_id, name, zabbix_id):
        return self.netbox.device(
            name, id=member_id, custom_fields={"zabbix_hostid": zabbix_id}
        )

    def test_adopt_cluster_host_moves_id_from_former_primary(self):
        """The Zabbix ID moves from the former primary to this device."""
        former = self._member(1, "SW01N0", 42)
        self.assertTrue(self.device.adopt_cluster_host([self.nb, former]))
        self.assertEqual(self.device.zabbix_id, 42)
        self.assertEqual(
            self.netbox.patches,
            [
                (self.nb.url, {"custom_fields": {"zabbix_hostid": 42}}),
                (former.url, {"custom_fields": {"zabbix_hostid": None}}),
            ],
        )

    def test_adopt_cluster_host_without_holder_does_nothing(self):
        """No other member has an ID: nothing to transfer."""
        other = self._member(1, "SW01N0", None)
        self.assertFalse(self.device.adopt_cluster_host([other]))
        self.assertIsNone(self.device.zabbix_id)
        self.assertEqual(self.netbox.patches, [])

    def test_adopt_cluster_host_with_multiple_holders_does_nothing(self):
        """Ambiguous: more than one member has an ID, so nothing is moved."""
        members = [self._member(1, "SW01N0", 42), self._member(3, "SW01N2", 43)]
        self.assertFalse(self.device.adopt_cluster_host(members))
        self.assertIsNone(self.device.zabbix_id)
        self.assertEqual(self.netbox.patches, [])
