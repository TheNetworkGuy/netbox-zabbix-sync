# pylint: disable=duplicate-code
"""Module that hosts all functions for virtual machine processing"""
from modules.config import load_config
from modules.device import PhysicalDevice
from modules.exceptions import InterfaceConfigError, SyncInventoryError, TemplateError
from modules.interface import ZabbixInterface

# Load config
config = load_config()


class VirtualMachine(PhysicalDevice):
    """Model for virtual machines"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hostgroup = None
        self.zbx_template_names = None
        self.hostgroup_type = "vm"

    def _inventory_map(self):
        """use VM inventory maps"""
        return config["vm_inventory_map"]

    def _usermacro_map(self):
        """use VM usermacro maps"""
        return config["vm_usermacro_map"]

    def _tag_map(self):
        """use VM tag maps"""
        return config["vm_tag_map"]

    def set_vm_template(self):
        """Set Template for VMs. Overwrites default class
        to skip a lookup of custom fields."""
        # Gather templates ONLY from the device specific context
        try:
            self.zbx_template_names = self.get_templates_context()
        except TemplateError as e:
            self.logger.warning(e)
        return True
    
    def set_interface_details(self):
        """
        Overwrites device function to select an agent interface type by default
        Agent type interfaces are more likely to be used with VMs then SNMP
        """
        zabbix_snmp_interface_type = 2
        try:
            # Initiate interface class
            interface = ZabbixInterface(self.nb.config_context, self.ip)
            # Check if NetBox has device context.
            # If not fall back to old config.
            if interface.get_context():
                # If device is SNMP type, add aditional information.
                if interface.interface["type"] == zabbix_snmp_interface_type:
                    interface.set_snmp()
            else:
                interface.set_default_agent()
            return [interface.interface]
        except InterfaceConfigError as e:
            message = f"{self.name}: {e}"
            self.logger.warning(message)
            raise SyncInventoryError(message) from e
