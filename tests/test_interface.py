"""Tests for the ZabbixInterface class in the interface module."""

import unittest
from typing import cast

from modules.exceptions import InterfaceConfigError
from modules.interface import ZabbixInterface


class TestZabbixInterface(unittest.TestCase):
    """Test class for ZabbixInterface functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_ip = "192.168.1.1"
        self.empty_context = {}
        self.default_interface = ZabbixInterface(self.empty_context, self.test_ip)

        # Create some test contexts for different scenarios
        self.snmpv2_context = {
            "zabbix": {
                "interface_type": 2,
                "interface_port": "161",
                "snmp": {"version": 2, "community": "public", "bulk": 1},
            }
        }

        self.snmpv3_context = {
            "zabbix": {
                "interface_type": 2,
                "snmp": {
                    "version": 3,
                    "securityname": "snmpuser",
                    "securitylevel": "authPriv",
                    "authprotocol": "SHA",
                    "authpassphrase": "authpass123",
                    "privprotocol": "AES",
                    "privpassphrase": "privpass123",
                    "contextname": "context1",
                },
            }
        }

        self.agent_context = {
            "zabbix": {"interface_type": 1, "interface_port": "10050"}
        }

    def test_init(self):
        """Test initialization of ZabbixInterface."""
        interface = ZabbixInterface(self.empty_context, self.test_ip)

        # Check basic properties
        self.assertEqual(interface.ip, self.test_ip)
        self.assertEqual(interface.context, self.empty_context)
        self.assertEqual(interface.interface["ip"], self.test_ip)
        self.assertEqual(interface.interface["main"], "1")
        self.assertEqual(interface.interface["useip"], "1")
        self.assertEqual(interface.interface["dns"], "")

    def test_get_context_empty(self):
        """Test get_context with empty context."""
        interface = ZabbixInterface(self.empty_context, self.test_ip)
        result = interface.get_context()
        self.assertFalse(result)

    def test_get_context_with_interface_type(self):
        """Test get_context with interface_type but no port."""
        context = {"zabbix": {"interface_type": 2}}
        interface = ZabbixInterface(context, self.test_ip)

        # Should set type and default port
        result = interface.get_context()
        self.assertTrue(result)
        self.assertEqual(interface.interface["type"], 2)
        self.assertEqual(interface.interface["port"], "161")  # Default port for SNMP

    def test_get_context_with_interface_type_and_port(self):
        """Test get_context with both interface_type and port."""
        context = {"zabbix": {"interface_type": 1, "interface_port": "12345"}}
        interface = ZabbixInterface(context, self.test_ip)

        # Should set type and specified port
        result = interface.get_context()
        self.assertTrue(result)
        self.assertEqual(interface.interface["type"], 1)
        self.assertEqual(interface.interface["port"], "12345")

    def test_set_default_port(self):
        """Test _set_default_port for different interface types."""
        interface = ZabbixInterface(self.empty_context, self.test_ip)

        # Test for agent type (1)
        interface.interface["type"] = 1
        interface._set_default_port()  #  pylint: disable=protected-access
        self.assertEqual(interface.interface["port"], "10050")

        # Test for SNMP type (2)
        interface.interface["type"] = 2
        interface._set_default_port()  #  pylint: disable=protected-access
        self.assertEqual(interface.interface["port"], "161")

        # Test for IPMI type (3)
        interface.interface["type"] = 3
        interface._set_default_port()  #  pylint: disable=protected-access
        self.assertEqual(interface.interface["port"], "623")

        # Test for JMX type (4)
        interface.interface["type"] = 4
        interface._set_default_port()  #  pylint: disable=protected-access
        self.assertEqual(interface.interface["port"], "12345")

        # Test for unsupported type
        interface.interface["type"] = 99
        result = interface._set_default_port()  #  pylint: disable=protected-access
        self.assertFalse(result)

    def test_set_snmp_v2(self):
        """Test set_snmp with SNMPv2 configuration."""
        interface = ZabbixInterface(self.snmpv2_context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp
        interface.set_snmp()

        # Check SNMP details
        details = cast(dict[str, str], interface.interface["details"])
        self.assertEqual(details["version"], "2")
        self.assertEqual(details["community"], "public")
        self.assertEqual(details["bulk"], "1")

    def test_set_snmp_v3(self):
        """Test set_snmp with SNMPv3 configuration."""
        interface = ZabbixInterface(self.snmpv3_context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp
        interface.set_snmp()

        # Check SNMP details
        details = cast(dict[str, str], interface.interface["details"])
        self.assertEqual(details["version"], "3")
        self.assertEqual(details["securityname"], "snmpuser")
        self.assertEqual(details["securitylevel"], "authPriv")
        self.assertEqual(details["authprotocol"], "SHA")
        self.assertEqual(
            details["authpassphrase"], "authpass123"
        )
        self.assertEqual(details["privprotocol"], "AES")
        self.assertEqual(
            details["privpassphrase"], "privpass123"
        )
        self.assertEqual(details["contextname"], "context1")

    def test_set_snmp_no_snmp_config(self):
        """Test set_snmp with missing SNMP configuration."""
        # Create context with interface type but no SNMP config
        context = {"zabbix": {"interface_type": 2}}
        interface = ZabbixInterface(context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp - should raise exception
        with self.assertRaises(InterfaceConfigError):
            interface.set_snmp()

    def test_set_snmp_unsupported_version(self):
        """Test set_snmp with unsupported SNMP version."""
        # Create context with invalid SNMP version
        context = {
            "zabbix": {
                "interface_type": 2,
                "snmp": {
                    "version": 4  # Invalid version
                },
            }
        }
        interface = ZabbixInterface(context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp - should raise exception
        with self.assertRaises(InterfaceConfigError):
            interface.set_snmp()

    def test_set_snmp_no_version(self):
        """Test set_snmp with missing SNMP version."""
        # Create context without SNMP version
        context = {
            "zabbix": {
                "interface_type": 2,
                "snmp": {
                    "community": "public"  # No version specified
                },
            }
        }
        interface = ZabbixInterface(context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp - should raise exception
        with self.assertRaises(InterfaceConfigError):
            interface.set_snmp()

    def test_set_snmp_non_snmp_interface(self):
        """Test set_snmp with non-SNMP interface type."""
        interface = ZabbixInterface(self.agent_context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp - should raise exception
        with self.assertRaises(InterfaceConfigError):
            interface.set_snmp()

    def test_set_default_snmp(self):
        """Test set_default_snmp method."""
        interface = ZabbixInterface(self.empty_context, self.test_ip)
        interface.set_default_snmp()

        # Check interface properties
        self.assertEqual(interface.interface["type"], "2")
        self.assertEqual(interface.interface["port"], "161")
        details = cast(dict[str, str], interface.interface["details"])
        self.assertEqual(details["version"], "2")
        self.assertEqual(
            details["community"], "{$SNMP_COMMUNITY}"
        )
        self.assertEqual(details["bulk"], "1")

    def test_set_default_agent(self):
        """Test set_default_agent method."""
        interface = ZabbixInterface(self.empty_context, self.test_ip)
        interface.set_default_agent()

        # Check interface properties
        self.assertEqual(interface.interface["type"], "1")
        self.assertEqual(interface.interface["port"], "10050")

    def test_snmpv2_no_community(self):
        """Test SNMPv2 with no community string specified."""
        # Create context with SNMPv2 but no community
        context = {"zabbix": {"interface_type": 2, "snmp": {"version": 2}}}
        interface = ZabbixInterface(context, self.test_ip)
        interface.get_context()  # Set the interface type

        # Call set_snmp
        interface.set_snmp()

        # Should use default community string
        details = cast(dict[str, str], interface.interface["details"])
        self.assertEqual(
            details["community"], "{$SNMP_COMMUNITY}"
        )
