"""Tests for VirtualMachine-specific behaviour."""

import unittest

from tests.fakes import FakeNetBox, virtual_machine


class _VMSetUp(unittest.TestCase):
    """Shared setUp for VirtualMachine tests."""

    def setUp(self):
        self.netbox = FakeNetBox()

    def _vm(self, config_context=None, **config):
        nb = self.netbox.virtual_machine(
            "test-vm",
            id=42,
            primary_ip="10.0.0.1/24",
            config_context=config_context,
        )
        return virtual_machine(nb, **config)


class TestVirtualMachineInit(_VMSetUp):
    """Test VirtualMachine.__init__ overrides."""

    def test_hostgroup_type_is_vm(self):
        """VirtualMachine overrides hostgroup_type to 'vm'."""
        self.assertEqual(self._vm().hostgroup_type, "vm")

    def test_zbx_template_names_is_none(self):
        """VirtualMachine initialises zbx_template_names to None (not [])."""
        self.assertIsNone(self._vm().zbx_template_names)

    def test_journal_entry_targets_virtual_machine(self):
        """Journal entries are assigned to the VM, not to a device with the same ID."""
        vm = self._vm()
        vm.journal = True
        vm.create_journal_entry("info", "test")
        journal = self.netbox.journal_entries.create.call_args.args[0]
        self.assertEqual(
            journal["assigned_object_type"], "virtualization.virtualmachine"
        )
        self.assertEqual(journal["assigned_object_id"], 42)


class TestVirtualMachineMaps(_VMSetUp):
    """Test that abstract map methods return the VM-specific config keys."""

    def test_inventory_map_uses_vm_key(self):
        """_inventory_map returns config['vm_inventory_map']."""
        vm = self._vm(vm_inventory_map={"name": "name"})
        self.assertEqual(vm._inventory_map(), {"name": "name"})

    def test_usermacro_map_uses_vm_key(self):
        """_usermacro_map returns config['vm_usermacro_map']."""
        vm = self._vm(vm_usermacro_map={"cluster/name": "{$CLUSTER}"})
        self.assertEqual(vm._usermacro_map(), {"cluster/name": "{$CLUSTER}"})

    def test_tag_map_uses_vm_key(self):
        """_tag_map returns config['vm_tag_map']."""
        vm = self._vm(vm_tag_map={"cluster/name": "cluster"})
        self.assertEqual(vm._tag_map(), {"cluster/name": "cluster"})


class TestVirtualMachineTemplate(_VMSetUp):
    """Test VirtualMachine.set_vm_template."""

    def test_set_vm_template_from_config_context(self):
        """set_vm_template reads templates from config_context['zabbix']['templates']."""
        vm = self._vm({"zabbix": {"templates": ["VMTemplate"]}})
        self.assertTrue(vm.set_vm_template())
        self.assertEqual(vm.zbx_template_names, ["VMTemplate"])

    def test_set_vm_template_no_context(self):
        """set_vm_template warns and returns True when config context has no templates key."""
        vm = self._vm({})
        # zbx_template_names was set to None by __init__; should stay None after warning
        self.assertTrue(vm.set_vm_template())
        self.assertIsNone(vm.zbx_template_names)
        vm.logger.warning.assert_called_once()


class TestVirtualMachineInterface(_VMSetUp):
    """Test VirtualMachine.set_interface_details."""

    def test_set_interface_details_defaults_to_agent(self):
        """No config context produces a default agent (type='1') interface."""
        vm = self._vm({})
        vm.set_ips()
        interface = vm.set_interface_details()
        self.assertEqual(interface["type"], "1")
        self.assertEqual(interface["port"], "10050")
        self.assertEqual(interface["ip"], "10.0.0.1")

    def test_set_interface_details_with_snmp_context(self):
        """Config context specifying SNMP interface type with full params produces type=2."""
        vm = self._vm(
            {
                "zabbix": {
                    "interface_type": "snmp",
                    "snmp": {"version": "2", "community": "public"},
                }
            }
        )
        vm.set_ips()
        interface = vm.set_interface_details()
        self.assertEqual(interface["type"], 2)
        self.assertEqual(interface["port"], "161")


class TestVirtualMachineProxy(_VMSetUp):
    """Test proxy selection for VMs."""

    def test_proxy_cf_without_site(self):
        """A VM without a site falls back to config context for its proxy."""
        nb = self.netbox.virtual_machine(
            site=None,
            custom_fields={"zabbix_proxy": None},
            config_context={"zabbix": {"proxy": "proxy1"}},
        )
        vm = virtual_machine(nb, proxy_cf="zabbix_proxy", proxy_group_cf=False)
        proxy = {"name": "proxy1", "type": "proxy", "id": "1"}
        self.assertTrue(vm._set_proxy([proxy]))
        self.assertEqual(vm.zbxproxy, proxy)
