"""Module that hosts all functions for virtual machine processing"""

from os import sys
from modules.device import PhysicalDevice
from modules.hostgroups import Hostgroup
from modules.exceptions import TemplateError
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
    def set_hostgroup(self, hg_format, nb_site_groups, nb_regions):
        """Set the hostgroup for this device"""
        # Create new Hostgroup instance
        hg = Hostgroup("vm", self.nb, self.nb_api_version,
                       traverse_site_groups, traverse_regions,
                       nb_site_groups, nb_regions)
        # Generate hostgroup based on hostgroup format
        self.hostgroup = hg.generate(hg_format)

    def set_vm_template(self):
        """ Set Template for VMs. Overwrites default class
        to skip a lookup of custom fields."""
        self.zbx_template_names = None
        # Gather templates ONLY from the device specific context
        try:
            self.zbx_template_names = self.get_templates_context()
        except TemplateError as e:
            self.logger.warning(e)
        return True

    def set_template(self, prefer_config_context=None, overrule_custom=None):
        """
        This wrapper takes the original function and
        overwrites it with the set_vm_template function.
        This function (set_template) is used by several
        other functions such as the consistency check in the parent class.
        """
        self.set_vm_template()
