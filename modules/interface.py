#!/usr/bin/env python3
"""
All of the Zabbix interface related configuration
"""
from modules.exceptions import InterfaceConfigError

class ZabbixInterface():
    """Class that represents a Zabbix interface."""

    def __init__(self, context, ip):
        self.context = context
        self.ip = ip
        self.skelet = {"main": "1", "useip": "1", "dns": "", "ip": self.ip}
        self.interface = self.skelet

    def _set_default_port(self):
        """Sets default TCP / UDP port for different interface types"""
        interface_mapping = {
            1: 10050,
            2: 161,
            3: 623,
            4: 12345
        }
        # Check if interface type is listed in mapper.
        if self.interface['type'] not in interface_mapping:
            return False
        # Set default port to interface
        self.interface["port"] = str(interface_mapping[self.interface['type']])
        return True

    def get_context(self):
        """ check if Netbox custom context has been defined. """
        if "zabbix" in self.context:
            zabbix = self.context["zabbix"]
            if "interface_type" in zabbix:
                self.interface["type"] = zabbix["interface_type"]
                if not "interface_port" in zabbix:
                    self._set_default_port()
                    return True
                self.interface["port"] = zabbix["interface_port"]
                return True
            return False
        return False

    def set_snmp(self):
        """ Check if interface is type SNMP """
        # pylint: disable=too-many-branches
        if self.interface["type"] == 2:
            # Checks if SNMP settings are defined in Netbox
            if "snmp" in self.context["zabbix"]:
                snmp = self.context["zabbix"]["snmp"]
                self.interface["details"] = {}
                # Checks if bulk config has been defined
                if "bulk" in snmp:
                    self.interface["details"]["bulk"] = str(snmp.pop("bulk"))
                else:
                    # Fallback to bulk enabled if not specified
                    self.interface["details"]["bulk"] = "1"
                # SNMP Version config is required in Netbox config context
                if snmp.get("version"):
                    self.interface["details"]["version"] = str(snmp.pop("version"))
                else:
                    e = "SNMP version option is not defined."
                    raise InterfaceConfigError(e)
                # If version 1 or 2 is used, get community string
                if self.interface["details"]["version"] in ['1','2']:
                    if "community" in snmp:
                        # Set SNMP community to confix context value
                        community = snmp["community"]
                    else:
                        # Set SNMP community to default
                        community = "{$SNMP_COMMUNITY}"
                    self.interface["details"]["community"] = str(community)
                # If version 3 has been used, get all
                # SNMPv3 Netbox related configs
                elif self.interface["details"]["version"] == '3':
                    items = ["securityname", "securitylevel", "authpassphrase",
                             "privpassphrase", "authprotocol", "privprotocol",
                             "contextname"]
                    for key, item in snmp.items():
                        if key in items:
                            self.interface["details"][key] = str(item)
                else:
                    e = "Unsupported SNMP version."
                    raise InterfaceConfigError(e)
            else:
                e = "Interface type SNMP but no parameters provided."
                raise InterfaceConfigError(e)
        else:
            e = "Interface type is not SNMP, unable to set SNMP details"
            raise InterfaceConfigError(e)

    def set_default(self):
        """ Set default config to SNMPv2, port 161 and community macro. """
        self.interface = self.skelet
        self.interface["type"] = "2"
        self.interface["port"] = "161"
        self.interface["details"] = {"version": "2",
                                     "community": "{$SNMP_COMMUNITY}",
                                     "bulk": "1"}
