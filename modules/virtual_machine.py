#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""Module that hosts all functions for virtual machine processing"""

from os import sys
from modules.device import PhysicalDevice
from modules.hostgroups import Hostgroup
from modules.interface import ZabbixInterface
from modules.exceptions import TemplateError, InterfaceConfigError, SyncInventoryError
try:
    from config import (
        traverse_site_groups,
        traverse_regions
    )
except ModuleNotFoundError:
    print("Configuration file config.py not found in main directory."
           "Please create the file or rename the config.py.example file to config.py.")
    sys.exit(0)

class VirtualMachine(PhysicalDevice):
    """Model for virtual machines"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hostgroup = None
        self.zbx_template_names = None

    def set_hostgroup(self, hg_format, nb_site_groups, nb_regions):
        """Set the hostgroup for this device"""
        # Create new Hostgroup instance
        hg = Hostgroup("vm", self.nb, self.nb_api_version, logger=self.logger)
        hg.set_nesting(traverse_site_groups, traverse_regions, nb_site_groups, nb_regions)
        # Generate hostgroup based on hostgroup format
        self.hostgroup = hg.generate(hg_format)

    def set_vm_template(self):
        """ Set Template for VMs. Overwrites default class
        to skip a lookup of custom fields."""
        # Gather templates ONLY from the device specific context
        try:
            self.zbx_template_names = self.get_templates_context()
        except TemplateError as e:
            self.logger.warning(e)
        return True

    def setInterfaceDetails(self): # pylint: disable=invalid-name
        """
        Overwrites device function to select an agent interface type by default
        Agent type interfaces are more likely to be used with VMs then SNMP
        """
        try:
            # Initiate interface class
            interface = ZabbixInterface(self.nb.config_context, self.ip)
            # Check if NetBox has device context.
            # If not fall back to old config.
            if interface.get_context():
                # If device is SNMP type, add aditional information.
                if interface.interface["type"] == 2:
                    interface.set_snmp()
            else:
                interface.set_default_agent()
            return [interface.interface]
        except InterfaceConfigError as e:
            message = f"{self.name}: {e}"
            self.logger.warning(message)
            raise SyncInventoryError(message) from e
