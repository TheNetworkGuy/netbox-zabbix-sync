#!/usr/bin/env python3
# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation, too-many-lines
"""
Device specific handeling for NetBox to Zabbix
"""
from pprint import pprint
from logging import getLogger
from zabbix_utils import APIRequestError
from modules.exceptions import (SyncInventoryError, TemplateError, SyncExternalError,
                                InterfaceConfigError, JournalError)
try:
    from config import (
        inventory_sync,
        inventory_mode,
        device_inventory_map,
        vm_inventory_map
    )
except ModuleNotFoundError:
    print("Configuration file config.py not found in main directory."
           "Please create the file or rename the config.py.example file to config.py.")
    sys.exit(0)

class Inventory():
    # pylint: disable=too-many-instance-attributes, too-many-arguments, too-many-positional-arguments
    """
    Represents Network device.
    INPUT: (NetBox device class, ZabbixAPI class, journal flag, NB journal class)
    """

#    def __init__(self, nb, logger=None):
#        self.nb = nb

    def set_inventory(self, nbobject):
        if hasattr(nbobject, 'device_type'):
            inventory_map = device_inventory_map
        else:
            inventory_map = vm_inventory_map
        """ Set host inventory """
        # Set inventory mode. Default is disabled (see class init function).
        if inventory_mode == "disabled":
            if inventory_sync:
                self.logger.error(f"Host {self.name}: Unable to map NetBox inventory to Zabbix. "
                              "Inventory sync is enabled in config but inventory mode is disabled.")
            return True
        if inventory_mode == "manual":
            self.inventory_mode = 0
        elif inventory_mode == "automatic":
            self.inventory_mode = 1
        else:
            self.logger.error(f"Host {self.name}: Specified value for inventory mode in"
                              f" config is not valid. Got value {inventory_mode}")
            return False
        self.inventory = {}
        if inventory_sync and self.inventory_mode in [0,1]:
            self.logger.debug(f"Host {self.name}: Starting inventory mapper")
            # Let's build an inventory dict for each property in the inventory_map
            for nb_inv_field, zbx_inv_field in inventory_map.items():
                field_list = nb_inv_field.split("/") # convert str to list based on delimiter
                # start at the base of the dict...
                value = nbobject
                # ... and step through the dict till we find the needed value
                for item in field_list:
                    value = value[item] if value else None
                # Check if the result is usable and expected
                # We want to apply any int or float 0 values,
                # even if python thinks those are empty.
                if ((value and isinstance(value, int | float | str )) or
                     (isinstance(value, int | float) and int(value) ==0)):
                    self.inventory[zbx_inv_field] = str(value)
                elif not value:
                    # empty value should just be an empty string for API compatibility
                    self.logger.debug(f"Host {self.name}: NetBox inventory lookup for "
                                      f"'{nb_inv_field}' returned an empty value")
                    self.inventory[zbx_inv_field] = ""
                else:
                    # Value is not a string or numeral, probably not what the user expected.
                    self.logger.error(f"Host {self.name}: Inventory lookup for '{nb_inv_field}'"
                                      " returned an unexpected type: it will be skipped.")
            self.logger.debug(f"Host {self.name}: Inventory mapping complete. "
                            f"Mapped {len(list(filter(None, self.inventory.values())))} field(s)")
#        return True
