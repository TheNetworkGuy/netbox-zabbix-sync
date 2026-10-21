"""Tests for the Host abstract base class, exercised via PhysicalDevice."""
import unittest
from ipaddress import ip_interface
from typing import Literal
from unittest.mock import patch

from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.exceptions import TemplateError
from tests.fakes import FakeNetBox, physical_device


class TestHostInit(unittest.TestCase):
    """Test Host.__init__ and _set_basics via PhysicalDevice."""

    def setUp(self):
        self.netbox = FakeNetBox()

    def test_init(self):
        """Basic attributes are set correctly from the NetBox record."""
        device = physical_device(self.netbox.device("test-device", id=123))
        device.set_ips()
        self.assertEqual(device.name, "test-device")
        self.assertEqual(device.id, 123)
        self.assertEqual(device.status, "Active")
        self.assertEqual(device.ip, "192.168.1.1")
        self.assertEqual(device.cidr, "192.168.1.1/24")
        self.assertIsNone(device.zabbix_id)
        self.assertEqual(device.hostgroup_type, "dev")

    def test_set_basics_with_special_characters(self):
        """Device name with special characters triggers NETBOX_ID renaming."""
        nb = self.netbox.device("test-devïce", id=123)

        with patch("netbox_zabbix_sync.modules.host.search") as mock_search:
            mock_search.return_value = True
            device = physical_device(nb)

        self.assertEqual(device.name, "NETBOX_ID123")
        self.assertEqual(device.visible_name, "test-devïce")
        self.assertTrue(device.use_visible_name)


class TestHostTemplates(unittest.TestCase):
    """Test Host template methods via PhysicalDevice."""

    def setUp(self):
        self.netbox = FakeNetBox()

    def _make(self, config_context):
        return physical_device(self.netbox.device(config_context=config_context))

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
        with patch.object(
            PhysicalDevice, "_get_templates_context", return_value=["Template1"]
        ):
            device = self._make({"zabbix": {"templates": ["Template1"]}})
            result = device.set_template(
                prefer_config_context=True, overrule_custom=False
            )

        self.assertTrue(result)
        self.assertEqual(device.zbx_template_names, ["Template1"])

    def test_set_template_from_device_type_custom_field(self):
        """Without config context, the template comes from the device type.

        The device type is nested in its brief form, which has no custom
        fields, so this read goes through pynetbox's lazy load.
        """
        device = self._make({})

        self.assertTrue(
            device.set_template(prefer_config_context=False, overrule_custom=False)
        )
        self.assertEqual(device.zbx_template_names, ["TestTemplate"])


class TestHostInventory(unittest.TestCase):
    """Test Host.set_inventory via PhysicalDevice."""

    def setUp(self):
        self.netbox = FakeNetBox()
        self.nb = self.netbox.device("test-device", serial="ABC123")

    def test_set_inventory_disabled_mode(self):
        """Disabled inventory mode leaves inventory_mode at -1."""
        device = physical_device(self.nb, inventory_mode="disabled")
        self.assertTrue(device.set_inventory(self.nb))
        self.assertEqual(device.inventory_mode, -1)

    def test_set_inventory_manual_mode(self):
        """Manual inventory mode sets inventory_mode to 0."""
        device = physical_device(self.nb, inventory_mode="manual")
        self.assertTrue(device.set_inventory(self.nb))
        self.assertEqual(device.inventory_mode, 0)

    def test_set_inventory_automatic_mode(self):
        """Automatic inventory mode sets inventory_mode to 1."""
        device = physical_device(self.nb, inventory_mode="automatic")
        self.assertTrue(device.set_inventory(self.nb))
        self.assertEqual(device.inventory_mode, 1)

    def test_set_inventory_with_inventory_sync(self):
        """inventory_sync=True maps NetBox fields to Zabbix inventory fields."""
        device = physical_device(
            self.nb,
            inventory_mode="manual",
            inventory_sync=True,
            device_inventory_map={"name": "name", "serial": "serialno_a"},
        )
        self.assertTrue(device.set_inventory(self.nb))
        self.assertEqual(device.inventory_mode, 0)
        self.assertEqual(
            device.inventory, {"name": "test-device", "serialno_a": "ABC123"}
        )

    def test_default_inventory_map_on_a_real_device(self):
        """The shipped device map resolves against what NetBox actually returns.

        Several of its paths (virtual_chassis/name, location/name, rack/name,
        oob_ip/address) cross a field that is null on a typical device. Those
        must map to "" rather than raise.
        """
        device = physical_device(self.nb, inventory_mode="manual", inventory_sync=True)

        self.assertTrue(device.set_inventory(self.nb))
        self.assertEqual(device.inventory["serialno_a"], "ABC123")
        self.assertEqual(device.inventory["vendor"], "TestManufacturer")
        self.assertEqual(device.inventory["deployment_status"], "Active")
        self.assertEqual(device.inventory["chassis"], "")
        self.assertEqual(device.inventory["oob_ip"], "")


class TestHostIpmi(unittest.TestCase):
    """Test Host.set_ipmi."""

    def setUp(self):
        self.netbox = FakeNetBox()

    def _set_ipmi(self, ipmi):
        nb = self.netbox.device(config_context={"zabbix": {"ipmi": ipmi}})
        device = physical_device(nb)
        device.set_ipmi()
        return device.ipmi

    def test_set_ipmi_values(self):
        """Authtype and privilege names are converted to Zabbix values."""
        ipmi = self._set_ipmi(
            {
                "username": "user",
                "password": "pass",
                "authtype": "md5",
                "privilege": "operator",
            }
        )
        self.assertEqual(ipmi["authtype"], 2)
        self.assertEqual(ipmi["privilege"], 3)

    def test_set_ipmi_oem_any_case(self):
        """OEM is accepted for authtype and privilege, regardless of case."""
        for value in ("OEM", "oem", "Oem"):
            ipmi = self._set_ipmi(
                {
                    "username": "user",
                    "password": "pass",
                    "authtype": value,
                    "privilege": value,
                }
            )
            self.assertEqual(ipmi["authtype"], 5)
            self.assertEqual(ipmi["privilege"], 5)


class TestHostInterface(unittest.TestCase):
    """Test Host.set_ip."""

    def setUp(self):
        self.netbox = FakeNetBox()

        self.ipv4 = self.netbox.ip_address("192.168.1.1/24")
        self.ipv6 = self.netbox.ip_address("2001:db8::370:7334/64")
        self.oob = self.netbox.ip_address("172.17.1.1/24")

    def _nb_device(self, primary: Literal["ipv4", "ipv6"]):
        return self.netbox.device(
            primary_ip=self.ipv4 if primary == "ipv4" else self.ipv6,
            primary_ip4=self.ipv4,
            primary_ip6=self.ipv6,
        )

    def __as_address(self, interface: str) -> str:
        return str(ip_interface(interface).ip)

    def test_use_primary_ipv4(self):
        """Test configuration where Primary IPv4 is preferred over IPv6"""
        nb = self._nb_device(primary="ipv4")

        device = physical_device(nb, preferred_ip="ipv4")
        device.set_ips()

        self.assertEqual(device.ip, self.__as_address(self.ipv4["address"]))

    def test_use_primary_ipv6(self):
        """Test configuration where Primary IPv6 is preferred over IPv4"""
        nb = self._nb_device(primary="ipv6")

        device = physical_device(nb, preferred_ip="ipv6")
        device.set_ips()

        self.assertEqual(device.ip, self.__as_address(self.ipv6["address"]))

    def test_oob_additional_interface(self):
        """Test allowed values for interface requirement, one"""
        nb =  self.netbox.device(
            oob_ip = self.oob, primary_ip=None, primary_ip4=None, primary_ip6=None
        )

        device = physical_device(nb, preferred_ip="auto")
        device.set_ips()

        self.assertEqual(device.oob_ip, self.__as_address(self.oob["address"]))