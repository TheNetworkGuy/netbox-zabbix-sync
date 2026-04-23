"""
All of the Zabbix interface related configuration
"""

from netbox_zabbix_sync.modules.exceptions import InterfaceConfigError


class ZabbixInterface:
    """Class that represents a Zabbix interface."""

    def __init__(self, context, ip, oob=False):
        self.context = context
        self.is_oob = oob
        self.ip = ip
        self.skelet = {"main": "1", "useip": "1", "dns": "", "ip": self.ip}
        self.interface = self.skelet

    def _set_default_port(self):
        """Sets default TCP / UDP port for different interface types"""
        interface_mapping = {1: 10050, 2: 161, 3: 623, 4: 12345}
        # Check if interface type is listed in mapper.
        if self.interface["type"] not in interface_mapping:
            return False
        # Set default port to interface
        self.interface["port"] = str(interface_mapping[int(self.interface["type"])])
        return True

    def get_context(self):
        """check if NetBox custom context has been defined."""
        int_types = {"agent": 1, "snmp": 2, "ipmi": 3, "jmx": 4}
        int_type = "interface_type"
        int_port = "interface_port"
        max_port = 65536
        max_type = 5
        if self.is_oob:
            int_type = "oob_interface_type"
            int_port = "oob_interface_port"
        if "zabbix" in self.context:
            zabbix = self.context["zabbix"]
            if int_type in zabbix:
                # Use valid integer for type if set
                if str(zabbix[int_type]).isdigit() and (
                    int(zabbix[int_type]) > 0 and int(zabbix[int_type]) < max_type
                ):
                    self.interface["type"] = zabbix[int_type]
                # Otherwise, convert string to type integer
                elif zabbix[int_type].lower() in int_types:
                    self.interface["type"] = int_types[zabbix[int_type].lower()]
                else:
                    e = f"Interface type '{zabbix[int_type]}' is not valid."
                    raise InterfaceConfigError(e)
                # Use default port if not specified in Config Context
                if int_port not in zabbix:
                    self._set_default_port()
                    return True
                # Otherwise, validate Config Context port value
                elif str(zabbix[int_port]).isdigit() and (
                    int(zabbix[int_port]) > 0 and int(zabbix[int_port]) < max_port
                ):
                    self.interface["port"] = zabbix[int_port]
                    return True
                else:
                    e = f"Interface port '{zabbix[int_port]}' is not valid."
                    raise InterfaceConfigError(e)
                return False
            return False
        return False

    def set_snmp(self):
        """Check if interface is type SNMP"""
        snmp_interface_type = 2
        if self.interface["type"] == snmp_interface_type:
            # Checks if SNMP settings are defined in NetBox
            if "snmp" in self.context["zabbix"]:
                snmp = self.context["zabbix"]["snmp"]
                details: dict[str, str] = {}
                self.interface["details"] = details
                # Checks if bulk config has been defined
                if "bulk" in snmp:
                    details["bulk"] = str(snmp.get("bulk"))
                else:
                    # Fallback to bulk enabled if not specified
                    details["bulk"] = "1"
                # SNMP Version config is required in NetBox config context
                if snmp.get("version"):
                    details["version"] = str(snmp.get("version"))
                else:
                    e = "SNMP version option is not defined."
                    raise InterfaceConfigError(e)
                # If version 1 or 2 is used, get community string
                if details["version"] in ["1", "2"]:
                    if "community" in snmp:
                        # Set SNMP community to confix context value
                        community = snmp["community"]
                    else:
                        # Set SNMP community to default
                        community = "{$SNMP_COMMUNITY}"
                    details["community"] = str(community)
                # If version 3 has been used, get all
                # SNMPv3 NetBox related configs
                elif details["version"] == "3":
                    items = [
                        "securityname",
                        "securitylevel",
                        "authpassphrase",
                        "privpassphrase",
                        "authprotocol",
                        "privprotocol",
                        "contextname",
                    ]
                    for key, item in snmp.items():
                        if key in items:
                            details[key] = str(item)
                else:
                    e = "Unsupported SNMP version."
                    raise InterfaceConfigError(e)
            else:
                e = "Interface type SNMP but no parameters provided."
                raise InterfaceConfigError(e)
        else:
            e = "Interface type is not SNMP, unable to set SNMP details"
            raise InterfaceConfigError(e)

    def set_ipmi(self):
        """Check if interface is type IPMI"""
        ipmi_interface_type = 3
        if self.interface["type"] == ipmi_interface_type:
            # Checks if IPMI settings are defined in NetBox
            if "ipmi" in self.context["zabbix"]:
                ipmi = self.context["zabbix"]["ipmi"]
                details: dict[str, str] = {}
                self.interface["details"] = details
                # Check for IPMI username
                if not ipmi.get("username"):
                    e = "No IPMI user provided."
                    raise InterfaceConfigError(e)
                # Check for IPMI password
                if not ipmi.get("password"):
                    e = "No IPMI password provided."
                    raise InterfaceConfigError(e)
            else:
                e = "Interface type IPMI but no parameters provided."
                raise InterfaceConfigError(e)
        else:
            e = "Interface type is not IPMI, unable to set IPMI details"
            raise InterfaceConfigError(e)

    def set_default_snmp(self):
        """Set default config to SNMPv2, port 161 and community macro."""
        self.interface = self.skelet
        self.interface["type"] = "2"
        self.interface["port"] = "161"
        self.interface["details"] = {
            "version": "2",
            "community": "{$SNMP_COMMUNITY}",
            "bulk": "1",
        }

    def set_default_agent(self):
        """Sets interface to Zabbix agent defaults"""
        self.interface["type"] = "1"
        self.interface["port"] = "10050"
