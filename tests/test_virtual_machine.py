"""Tests for VirtualMachine-specific behaviour."""

import unittest
from unittest.mock import MagicMock

from netbox_zabbix_sync.modules.virtual_machine import VirtualMachine


def _make_vm(mock_nb_vm, mock_zabbix, mock_nb_journal, mock_logger, config=None):
    """Helper to construct a VirtualMachine with a minimal default config."""
    default_config = {"device_cf": "zabbix_hostid", "preferred_ip": "auto"}
    if config is not None:
        default_config.update(config)
    return VirtualMachine(
        mock_nb_vm,
        mock_zabbix,
        mock_nb_journal,
        "3.0",
        logger=mock_logger,
        config=default_config,
    )


class _VMSetUp(unittest.TestCase):
    """Shared setUp for VirtualMachine tests."""

    def setUp(self):
        self.mock_nb_vm = MagicMock()
        self.mock_nb_vm.id = 42
        self.mock_nb_vm.name = "test-vm"
        self.mock_nb_vm.status.label = "Active"
        self.mock_nb_vm.custom_fields = {"zabbix_hostid": None}
        self.mock_nb_vm.config_context = {"zabbix": {"templates": ["TestTemplate"]}}
        self.mock_nb_vm.oob_ip = None

        primary_ip = MagicMock()
        primary_ip.address = "10.0.0.1/24"
        self.mock_nb_vm.primary_ip = primary_ip
        self.mock_nb_vm.primary_ip4 = primary_ip
        self.mock_nb_vm.primary_ip6 = None

        self.mock_zabbix = MagicMock()
        self.mock_nb_journal = MagicMock()
        self.mock_logger = MagicMock()


class TestVirtualMachineInit(_VMSetUp):
    """Test VirtualMachine.__init__ overrides."""

    def test_hostgroup_type_is_vm(self):
        """VirtualMachine overrides hostgroup_type to 'vm'."""
        vm = _make_vm(
            self.mock_nb_vm, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        self.assertEqual(vm.hostgroup_type, "vm")

    def test_zbx_template_names_is_none(self):
        """VirtualMachine initialises zbx_template_names to None (not [])."""
        vm = _make_vm(
            self.mock_nb_vm, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        self.assertIsNone(vm.zbx_template_names)


class TestVirtualMachineMaps(_VMSetUp):
    """Test that abstract map methods return the VM-specific config keys."""

    def _vm_with_maps(self, inventory=None, usermacro=None, tag=None):
        return _make_vm(
            self.mock_nb_vm,
            self.mock_zabbix,
            self.mock_nb_journal,
            self.mock_logger,
            config={
                "vm_inventory_map": inventory or {},
                "vm_usermacro_map": usermacro or {},
                "vm_tag_map": tag or {},
            },
        )

    def test_inventory_map_uses_vm_key(self):
        """_inventory_map returns config['vm_inventory_map']."""
        vm = self._vm_with_maps(inventory={"name": "name"})
        self.assertEqual(vm._inventory_map(), {"name": "name"})

    def test_usermacro_map_uses_vm_key(self):
        """_usermacro_map returns config['vm_usermacro_map']."""
        vm = self._vm_with_maps(usermacro={"{$CLUSTER}": "cluster.name"})
        self.assertEqual(vm._usermacro_map(), {"{$CLUSTER}": "cluster.name"})

    def test_tag_map_uses_vm_key(self):
        """_tag_map returns config['vm_tag_map']."""
        vm = self._vm_with_maps(tag={"env": "config_context.env"})
        self.assertEqual(vm._tag_map(), {"env": "config_context.env"})


class TestVirtualMachineTemplate(_VMSetUp):
    """Test VirtualMachine.set_vm_template."""

    def test_set_vm_template_from_config_context(self):
        """set_vm_template reads templates from config_context['zabbix']['templates']."""
        self.mock_nb_vm.config_context = {"zabbix": {"templates": ["VMTemplate"]}}
        vm = _make_vm(
            self.mock_nb_vm, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        result = vm.set_vm_template()
        self.assertTrue(result)
        self.assertEqual(vm.zbx_template_names, ["VMTemplate"])

    def test_set_vm_template_no_context(self):
        """set_vm_template warns and returns True when config context has no templates key."""
        self.mock_nb_vm.config_context = {}
        vm = _make_vm(
            self.mock_nb_vm, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        # zbx_template_names was set to None by __init__; should stay None after warning
        result = vm.set_vm_template()
        self.assertTrue(result)
        self.assertIsNone(vm.zbx_template_names)
        self.mock_logger.warning.assert_called_once()


class TestVirtualMachineInterface(_VMSetUp):
    """Test VirtualMachine.set_interface_details."""

    def test_set_interface_details_defaults_to_agent(self):
        """No config context produces a default agent (type='1') interface."""
        self.mock_nb_vm.config_context = {}
        vm = _make_vm(
            self.mock_nb_vm, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        interface = vm.set_interface_details()
        self.assertEqual(interface["type"], "1")
        self.assertEqual(interface["port"], "10050")
        self.assertEqual(interface["ip"], "10.0.0.1")

    def test_set_interface_details_with_snmp_context(self):
        """Config context specifying SNMP interface type with full params produces type=2."""
        self.mock_nb_vm.config_context = {
            "zabbix": {
                "interface_type": "snmp",
                "snmp": {"version": "2", "community": "public"},
            }
        }
        vm = _make_vm(
            self.mock_nb_vm, self.mock_zabbix, self.mock_nb_journal, self.mock_logger
        )
        interface = vm.set_interface_details()
        self.assertEqual(interface["type"], 2)
        self.assertEqual(interface["port"], "161")
