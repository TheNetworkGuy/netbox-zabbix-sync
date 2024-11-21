#!/usr/bin/env python3
# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation, too-many-lines
"""
Device specific handeling for Netbox to Zabbix
"""
from os import sys
from re import search
from logging import getLogger
from zabbix_utils import APIRequestError
from modules.exceptions import (SyncInventoryError, TemplateError, SyncExternalError,
                                InterfaceConfigError, JournalError)
from modules.interface import ZabbixInterface
from modules.hostgroups import Hostgroup
try:
    from config import (
        template_cf, device_cf,
        traverse_site_groups,
        traverse_regions,
        inventory_sync,
        inventory_mode,
        inventory_map
    )
except ModuleNotFoundError:
    print("Configuration file config.py not found in main directory."
           "Please create the file or rename the config.py.example file to config.py.")
    sys.exit(0)

class PhysicalDevice():
    # pylint: disable=too-many-instance-attributes, too-many-arguments, too-many-positional-arguments
    """
    Represents Network device.
    INPUT: (Netbox device class, ZabbixAPI class, journal flag, NB journal class)
    """

    def __init__(self, nb, zabbix, nb_journal_class, nb_version, journal=None, logger=None):
        self.nb = nb
        self.id = nb.id
        self.name = nb.name
        self.visible_name = None
        self.status = nb.status.label
        self.zabbix = zabbix
        self.zabbix_id = None
        self.group_id = None
        self.nb_api_version = nb_version
        self.zbx_template_names = []
        self.zbx_templates = []
        self.hostgroup = None
        self.tenant = nb.tenant
        self.config_context = nb.config_context
        self.zbxproxy = None
        self.zabbix_state = 0
        self.journal = journal
        self.nb_journals = nb_journal_class
        self.inventory_mode = -1
        self.inventory = {}
        self.logger = logger if logger else getLogger(__name__)
        self._setBasics()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    def _setBasics(self):
        """
        Sets basic information like IP address.
        """
        # Return error if device does not have primary IP.
        if self.nb.primary_ip:
            self.cidr = self.nb.primary_ip.address
            self.ip = self.cidr.split("/")[0]
        else:
            e = f"Host {self.name}: no primary IP."
            self.logger.info(e)
            raise SyncInventoryError(e)

        # Check if device has custom field for ZBX ID
        if device_cf in self.nb.custom_fields:
            self.zabbix_id = self.nb.custom_fields[device_cf]
        else:
            e = f"Host {self.name}: Custom field {device_cf} not present"
            self.logger.warning(e)
            raise SyncInventoryError(e)

        # Validate hostname format.
        odd_character_list = ["ä", "ö", "ü", "Ä", "Ö", "Ü", "ß"]
        self.use_visible_name = False
        if (any(letter in self.name for letter in odd_character_list) or
            bool(search('[\u0400-\u04FF]', self.name))):
            self.name = f"NETBOX_ID{self.id}"
            self.visible_name = self.nb.name
            self.use_visible_name = True
            self.logger.info(f"Host {self.visible_name} contains special characters. "
                             f"Using {self.name} as name for the Netbox object "
                             f"and using {self.visible_name} as visible name in Zabbix.")
        else:
            pass

    def set_hostgroup(self, hg_format, nb_site_groups, nb_regions):
        """Set the hostgroup for this device"""
        # Create new Hostgroup instance
        hg = Hostgroup("dev", self.nb, self.nb_api_version)
        # Set Hostgroup nesting options
        hg.set_nesting(traverse_site_groups, traverse_regions, nb_site_groups, nb_regions)
        # Generate hostgroup based on hostgroup format
        self.hostgroup = hg.generate(hg_format)

    def set_template(self, prefer_config_context, overrule_custom):
        """ Set Template """
        self.zbx_template_names = None
        # Gather templates ONLY from the device specific context
        if prefer_config_context:
            try:
                self.zbx_template_names = self.get_templates_context()
            except TemplateError as e:
                self.logger.warning(e)
            return True
        # Gather templates from the custom field but overrule
        # them should there be any device specific templates
        if overrule_custom:
            try:
                self.zbx_template_names = self.get_templates_context()
            except TemplateError:
                pass
            if not self.zbx_template_names:
                self.zbx_template_names = self.get_templates_cf()
            return True
        # Gather templates ONLY from the custom field
        self.zbx_template_names = self.get_templates_cf()
        return True

    def get_templates_cf(self):
        """ Get template from custom field """
        # Get Zabbix templates from the device type
        device_type_cfs = self.nb.device_type.custom_fields
        # Check if the ZBX Template CF is present
        if template_cf in device_type_cfs:
            # Set value to template
            return [device_type_cfs[template_cf]]
        # Custom field not found, return error
        e = (f"Custom field {template_cf} not "
            f"found for {self.nb.device_type.manufacturer.name}"
            f" - {self.nb.device_type.display}.")
        raise TemplateError(e)

    def get_templates_context(self):
        """ Get Zabbix templates from the device context """
        if "zabbix" not in self.config_context:
            e = (f"Host {self.name}: Key 'zabbix' not found in config "
                 "context for template lookup")
            raise TemplateError(e)
        if "templates" not in self.config_context["zabbix"]:
            e = (f"Host {self.name}: Key 'templates' not found in config "
                 "context 'zabbix' for template lookup")
            raise TemplateError(e)
        # Check if format is list or string.
        if isinstance(self.config_context["zabbix"]["templates"], str):
            return [self.config_context["zabbix"]["templates"]]
        return self.config_context["zabbix"]["templates"]

    def set_inventory(self, nbdevice):
        """ Set host inventory """
        # Set inventory mode. Default is disabled (see class init function).
        if inventory_mode == "disabled":
            if inventory_sync:
                self.logger.error(f"Host {self.name}: Unable to map Netbox inventory to Zabbix. "
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
                value = nbdevice
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
                    self.logger.debug(f"Host {self.name}: Netbox inventory lookup for "
                                      f"'{nb_inv_field}' returned an empty value")
                    self.inventory[zbx_inv_field] = ""
                else:
                    # Value is not a string or numeral, probably not what the user expected.
                    self.logger.error(f"Host {self.name}: Inventory lookup for '{nb_inv_field}'"
                                      " returned an unexpected type: it will be skipped.")
            self.logger.debug(f"Host {self.name}: Inventory mapping complete. "
                            f"Mapped {len(list(filter(None, self.inventory.values())))} field(s)")
        return True

    def isCluster(self):
        """
        Checks if device is part of cluster.
        """
        return bool(self.nb.virtual_chassis)

    def getClusterMaster(self):
        """
        Returns chassis master ID.
        """
        if not self.isCluster():
            e = (f"Unable to proces {self.name} for cluster calculation: "
                 f"not part of a cluster.")
            self.logger.warning(e)
            raise SyncInventoryError(e)
        if not self.nb.virtual_chassis.master:
            e = (f"{self.name} is part of a Netbox virtual chassis which does "
                 "not have a master configured. Skipping for this reason.")
            self.logger.error(e)
            raise SyncInventoryError(e)
        return self.nb.virtual_chassis.master.id

    def promoteMasterDevice(self):
        """
        If device is Primary in cluster,
        promote device name to the cluster name.
        Returns True if succesfull, returns False if device is secondary.
        """
        masterid = self.getClusterMaster()
        if masterid == self.id:
            self.logger.debug(f"Host {self.name} is primary cluster member. "
                              f"Modifying hostname from {self.name} to " +
                              f"{self.nb.virtual_chassis.name}.")
            self.name = self.nb.virtual_chassis.name
            return True
        self.logger.debug(f"Host {self.name} is non-primary cluster member.")
        return False

    def zbxTemplatePrepper(self, templates):
        """
        Returns Zabbix template IDs
        INPUT: list of templates from Zabbix
        OUTPUT: True
        """
        # Check if there are templates defined
        if not self.zbx_template_names:
            e = f"Host {self.name}: No templates found"
            self.logger.info(e)
            raise SyncInventoryError()
        # Set variable to empty list
        self.zbx_templates = []
        # Go through all templates definded in Netbox
        for nb_template in self.zbx_template_names:
            template_match = False
            # Go through all templates found in Zabbix
            for zbx_template in templates:
                # If the template names match
                if zbx_template['name'] == nb_template:
                    # Set match variable to true, add template details
                    # to class variable and return debug log
                    template_match = True
                    self.zbx_templates.append({"templateid": zbx_template['templateid'],
                                               "name": zbx_template['name']})
                    e = f"Host {self.name}: found template {zbx_template['name']}"
                    self.logger.debug(e)
            # Return error should the template not be found in Zabbix
            if not template_match:
                e = (f"Unable to find template {nb_template} "
                    f"for host {self.name} in Zabbix. Skipping host...")
                self.logger.warning(e)
                raise SyncInventoryError(e)

    def setZabbixGroupID(self, groups):
        """
        Sets Zabbix group ID as instance variable
        INPUT: list of hostgroups
        OUTPUT: True / False
        """
        # Go through all groups
        for group in groups:
            if group['name'] == self.hostgroup:
                self.group_id = group['groupid']
                e = f"Host {self.name}: matched group {group['name']}"
                self.logger.debug(e)
                return True
        return False

    def cleanup(self):
        """
        Removes device from external resources.
        Resets custom fields in Netbox.
        """
        if self.zabbix_id:
            try:
                # Check if the Zabbix host exists in Zabbix
                zbx_host = bool(self.zabbix.host.get(filter={'hostid': self.zabbix_id},
                                                     output=[]))
                e = (f"Host {self.name}: was already deleted from Zabbix."
                        " Removed link in Netbox.")
                if zbx_host:
                    # Delete host should it exists
                    self.zabbix.host.delete(self.zabbix_id)
                    e = f"Host {self.name}: Deleted host from Zabbix."
                self._zeroize_cf()
                self.logger.info(e)
                self.create_journal_entry("warning", "Deleted host from Zabbix")
            except APIRequestError as e:
                message = f"Zabbix returned the following error: {str(e)}."
                self.logger.error(message)
                raise SyncExternalError(message) from e

    def _zeroize_cf(self):
        """Sets the hostID custom field in Netbox to zero,
        effectively destroying the link"""
        self.nb.custom_fields[device_cf] = None
        self.nb.save()

    def _zabbixHostnameExists(self):
        """
        Checks if hostname exists in Zabbix.
        """
        # Validate the hostname or visible name field
        if not self.use_visible_name:
            zbx_filter = {'host': self.name}
        else:
            zbx_filter = {'name': self.visible_name}
        host = self.zabbix.host.get(filter=zbx_filter, output=[])
        return bool(host)

    def setInterfaceDetails(self):
        """
        Checks interface parameters from Netbox and
        creates a model for the interface to be used in Zabbix.
        """
        try:
            # Initiate interface class
            interface = ZabbixInterface(self.nb.config_context, self.ip)
            # Check if Netbox has device context.
            # If not fall back to old config.
            if interface.get_context():
                # If device is SNMP type, add aditional information.
                if interface.interface["type"] == 2:
                    interface.set_snmp()
            else:
                interface.set_default_snmp()
            return [interface.interface]
        except InterfaceConfigError as e:
            message = f"{self.name}: {e}"
            self.logger.warning(message)
            raise SyncInventoryError(message) from e

    def setProxy(self, proxy_list):
        """
        Sets proxy or proxy group if this
        value has been defined in config context

        input: List of all proxies and proxy groups in standardized format
        """
        # check if the key Zabbix is defined in the config context
        if not "zabbix" in self.nb.config_context:
            return False
        if ("proxy" in self.nb.config_context["zabbix"] and
               not self.nb.config_context["zabbix"]["proxy"]):
            return False
        # Proxy group takes priority over a proxy due
        # to it being HA and therefore being more reliable
        # Includes proxy group fix since Zabbix <= 6 should ignore this
        proxy_types = ["proxy"]
        if str(self.zabbix.version).startswith('7'):
            # Only insert groups in front of list for Zabbix7
            proxy_types.insert(0, "proxy_group")
        for proxy_type in proxy_types:
            # Check if the key exists in Netbox CC
            if proxy_type in self.nb.config_context["zabbix"]:
                proxy_name = self.nb.config_context["zabbix"][proxy_type]
                # go through all proxies
                for proxy in proxy_list:
                    # If the proxy does not match the type, ignore and continue
                    if not proxy["type"] == proxy_type:
                        continue
                    # If the proxy name matches
                    if proxy["name"] == proxy_name:
                        self.logger.debug(f"Host {self.name}: using {proxy['type']}"
                                          f" {proxy_name}")
                        self.zbxproxy = proxy
                        return True
                self.logger.warning(f"Host {self.name}: unable to find proxy {proxy_name}")
        return False

    def createInZabbix(self, groups, templates, proxies,
                       description="Host added by Netbox sync script."):
        """
        Creates Zabbix host object with parameters from Netbox object.
        """
        # Check if hostname is already present in Zabbix
        if not self._zabbixHostnameExists():
            # Set group and template ID's for host
            if not self.setZabbixGroupID(groups):
                e = (f"Unable to find group '{self.hostgroup}' "
                     f"for host {self.name} in Zabbix.")
                self.logger.warning(e)
                raise SyncInventoryError(e)
            self.zbxTemplatePrepper(templates)
            templateids = []
            for template in self.zbx_templates:
                templateids.append({'templateid': template['templateid']})
            # Set interface, group and template configuration
            interfaces = self.setInterfaceDetails()
            groups = [{"groupid": self.group_id}]
            # Set Zabbix proxy if defined
            self.setProxy(proxies)
            # Set basic data for host creation
            create_data = {"host": self.name,
                            "name": self.visible_name,
                            "status": self.zabbix_state,
                            "interfaces": interfaces,
                            "groups": groups,
                            "templates": templateids,
                            "description": description,
                            "inventory_mode": self.inventory_mode,
                            "inventory": self.inventory
                            }
            # If a Zabbix proxy or Zabbix Proxy group has been defined
            if self.zbxproxy:
                # If a lower version than 7 is used, we can assume that
                # the proxy is a normal proxy and not a proxy group
                if not str(self.zabbix.version).startswith('7'):
                    create_data["proxy_hostid"] = self.zbxproxy["id"]
                else:
                    # Configure either a proxy or proxy group
                    create_data[self.zbxproxy["idtype"]] = self.zbxproxy["id"]
                    create_data["monitored_by"] = self.zbxproxy["monitored_by"]
            # Add host to Zabbix
            try:
                host = self.zabbix.host.create(**create_data)
                self.zabbix_id = host["hostids"][0]
            except APIRequestError as e:
                e = f"Host {self.name}: Couldn't create. Zabbix returned {str(e)}."
                self.logger.error(e)
                raise SyncExternalError(e) from None
            # Set Netbox custom field to hostID value.
            self.nb.custom_fields[device_cf] = int(self.zabbix_id)
            self.nb.save()
            msg = f"Host {self.name}: Created host in Zabbix."
            self.logger.info(msg)
            self.create_journal_entry("success", msg)
        else:
            e = f"Host {self.name}: Unable to add to Zabbix. Host already present."
            self.logger.warning(e)

    def createZabbixHostgroup(self, hostgroups):
        """
        Creates Zabbix host group based on hostgroup format.
        Creates multiple when using a nested format.
        """
        final_data = []
        # Check if the hostgroup is in a nested format and check each parent
        for pos in range(len(self.hostgroup.split('/'))):
            zabbix_hg = self.hostgroup.rsplit('/', pos)[0]
            if self.lookupZabbixHostgroup(hostgroups, zabbix_hg):
                # Hostgroup already exists
                continue
            # Create new group
            try:
                # API call to Zabbix
                groupid = self.zabbix.hostgroup.create(name=zabbix_hg)
                e = f"Hostgroup '{zabbix_hg}': created in Zabbix."
                self.logger.info(e)
                # Add group to final data
                final_data.append({'groupid': groupid["groupids"][0], 'name': zabbix_hg})
            except APIRequestError as e:
                msg = f"Hostgroup '{zabbix_hg}': unable to create. Zabbix returned {str(e)}."
                self.logger.error(msg)
                raise SyncExternalError(msg) from e
        return final_data

    def lookupZabbixHostgroup(self, group_list, lookup_group):
        """
        Function to check if a hostgroup
        exists in a list of Zabbix hostgroups
        INPUT: Group list and group lookup
        OUTPUT: Boolean
        """
        for group in group_list:
            if group["name"] == lookup_group:
                return True
        return False

    def updateZabbixHost(self, **kwargs):
        """
        Updates Zabbix host with given parameters.
        INPUT: Key word arguments for Zabbix host object.
        """
        try:
            self.zabbix.host.update(hostid=self.zabbix_id, **kwargs)
        except APIRequestError as e:
            e = (f"Host {self.name}: Unable to update. "
                 f"Zabbix returned the following error: {str(e)}.")
            self.logger.error(e)
            raise SyncExternalError(e) from None
        self.logger.info(f"Updated host {self.name} with data {kwargs}.")
        self.create_journal_entry("info", "Updated host in Zabbix with latest NB data.")

    def ConsistencyCheck(self, groups, templates, proxies, proxy_power, create_hostgroups):
        # pylint: disable=too-many-branches, too-many-statements
        """
        Checks if Zabbix object is still valid with Netbox parameters.
        """
        # If group is found or if the hostgroup is nested
        if not self.setZabbixGroupID(groups) or len(self.hostgroup.split('/')) > 1:
            if create_hostgroups:
                # Script is allowed to create a new hostgroup
                new_groups = self.createZabbixHostgroup(groups)
                for group in new_groups:
                    # Add all new groups to the list of groups
                    groups.append(group)
            # check if the initial group was not already found (and this is a nested folder check)
            if not self.group_id:
                # Function returns true / false but also sets GroupID
                if not self.setZabbixGroupID(groups) and not create_hostgroups:
                    e = (f"Host {self.name}: different hostgroup is required but "
                        "unable to create hostgroup without generation permission.")
                    self.logger.warning(e)
                    raise SyncInventoryError(e)
        # Prepare templates and proxy config
        self.zbxTemplatePrepper(templates)
        self.setProxy(proxies)
        # Get host object from Zabbix
        host = self.zabbix.host.get(filter={'hostid': self.zabbix_id},
                                    selectInterfaces=['type', 'ip',
                                                      'port', 'details',
                                                      'interfaceid'],
                                    selectGroups=["groupid"],
                                    selectParentTemplates=["templateid"],
                                    selectInventory=list(inventory_map.values()))
        if len(host) > 1:
            e = (f"Got {len(host)} results for Zabbix hosts "
                 f"with ID {self.zabbix_id} - hostname {self.name}.")
            self.logger.error(e)
            raise SyncInventoryError(e)
        if len(host) == 0:
            e = (f"Host {self.name}: No Zabbix host found. "
                 f"This is likely the result of a deleted Zabbix host "
                 f"without zeroing the ID field in Netbox.")
            self.logger.error(e)
            raise SyncInventoryError(e)
        host = host[0]
        if host["host"] == self.name:
            self.logger.debug(f"Host {self.name}: hostname in-sync.")
        else:
            self.logger.warning(f"Host {self.name}: hostname OUT of sync. "
                                f"Received value: {host['host']}")
            self.updateZabbixHost(host=self.name)
        # Execute check depending on wether the name is special or not
        if self.use_visible_name:
            if host["name"] == self.visible_name:
                self.logger.debug(f"Host {self.name}: visible name in-sync.")
            else:
                self.logger.warning(f"Host {self.name}: visible name OUT of sync."
                                    f" Received value: {host['name']}")
                self.updateZabbixHost(name=self.visible_name)

        # Check if the templates are in-sync
        if not self.zbx_template_comparer(host["parentTemplates"]):
            self.logger.warning(f"Host {self.name}: template(s) OUT of sync.")
            # Prepare Templates for API parsing
            templateids = []
            for template in self.zbx_templates:
                templateids.append({'templateid': template['templateid']})
            # Update Zabbix with NB templates and clear any old / lost templates
            self.updateZabbixHost(templates_clear=host["parentTemplates"],
                                      templates=templateids)
        else:
            self.logger.debug(f"Host {self.name}: template(s) in-sync.")

        for group in host["groups"]:
            if group["groupid"] == self.group_id:
                self.logger.debug(f"Host {self.name}: hostgroup in-sync.")
                break
        else:
            self.logger.warning(f"Host {self.name}: hostgroup OUT of sync.")
            self.updateZabbixHost(groups={'groupid': self.group_id})

        if int(host["status"]) == self.zabbix_state:
            self.logger.debug(f"Host {self.name}: status in-sync.")
        else:
            self.logger.warning(f"Host {self.name}: status OUT of sync.")
            self.updateZabbixHost(status=str(self.zabbix_state))
        # Check if a proxy has been defined
        if self.zbxproxy:
            # Check if proxy or proxy group is defined
            if (self.zbxproxy["idtype"] in host and
                host[self.zbxproxy["idtype"]] == self.zbxproxy["id"]):
                self.logger.debug(f"Host {self.name}: proxy in-sync.")
            # Backwards compatibility for Zabbix <= 6
            elif "proxy_hostid" in host and host["proxy_hostid"] == self.zbxproxy["id"]:
                self.logger.debug(f"Host {self.name}: proxy in-sync.")
            # Proxy does not match, update Zabbix
            else:
                self.logger.warning(f"Host {self.name}: proxy OUT of sync.")
                # Zabbix <= 6 patch
                if not str(self.zabbix.version).startswith('7'):
                    self.updateZabbixHost(proxy_hostid=self.zbxproxy['id'])
                # Zabbix 7+
                else:
                    # Prepare data structure for updating either proxy or group
                    update_data = {self.zbxproxy["idtype"]: self.zbxproxy["id"],
                                   "monitored_by": self.zbxproxy['monitored_by']}
                    self.updateZabbixHost(**update_data)
        else:
            # No proxy is defined in Netbox
            proxy_set = False
            # Check if a proxy is defined. Uses the proxy_hostid key for backwards compatibility
            for key in ("proxy_hostid", "proxyid", "proxy_groupid"):
                if key in host:
                    if bool(int(host[key])):
                        proxy_set = True
            if proxy_power and proxy_set:
                # Zabbix <= 6 fix
                self.logger.warning(f"Host {self.name}: no proxy is configured in Netbox "
                                    "but is configured in Zabbix. Removing proxy config in Zabbix")
                if "proxy_hostid" in host and bool(host["proxy_hostid"]):
                    self.updateZabbixHost(proxy_hostid=0)
                # Zabbix 7 proxy
                elif "proxyid" in host and bool(host["proxyid"]):
                    self.updateZabbixHost(proxyid=0, monitored_by=0)
                # Zabbix 7 proxy group
                elif "proxy_groupid" in host and bool(host["proxy_groupid"]):
                    self.updateZabbixHost(proxy_groupid=0, monitored_by=0)
            # Checks if a proxy has been defined in Zabbix and if proxy_power config has been set
            if proxy_set and not proxy_power:
                # Display error message
                self.logger.error(f"Host {self.name} is configured "
                                    f"with proxy in Zabbix but not in Netbox. The"
                                    " -p flag was ommited: no "
                                    "changes have been made.")
            if not proxy_set:
                self.logger.debug(f"Host {self.name}: proxy in-sync.")
        # Check host inventory mode
        if str(host['inventory_mode']) == str(self.inventory_mode):
            self.logger.debug(f"Host {self.name}: inventory_mode in-sync.")
        else:
            self.logger.warning(f"Host {self.name}: inventory_mode OUT of sync.")
            self.updateZabbixHost(inventory_mode=str(self.inventory_mode))
        if inventory_sync and self.inventory_mode in [0,1]:
            # Check host inventory mapping
            if host['inventory'] == self.inventory:
                self.logger.debug(f"Host {self.name}: inventory in-sync.")
            else:
                self.logger.warning(f"Host {self.name}: inventory OUT of sync.")
                self.updateZabbixHost(inventory=self.inventory)

        # If only 1 interface has been found
        # pylint: disable=too-many-nested-blocks
        if len(host['interfaces']) == 1:
            updates = {}
            # Go through each key / item and check if it matches Zabbix
            for key, item in self.setInterfaceDetails()[0].items():
                # Check if Netbox value is found in Zabbix
                if key in host["interfaces"][0]:
                    # If SNMP is used, go through nested dict
                    # to compare SNMP parameters
                    if isinstance(item,dict) and key == "details":
                        for k, i in item.items():
                            if k in host["interfaces"][0][key]:
                                # Set update if values don't match
                                if host["interfaces"][0][key][k] != str(i):
                                    # If dict has not been created, add it
                                    if key not in updates:
                                        updates[key] = {}
                                    updates[key][k] = str(i)
                                    # If SNMP version has been changed
                                    # break loop and force full SNMP update
                                    if k == "version":
                                        break
                        # Force full SNMP config update
                        # when version has changed.
                        if key in updates:
                            if "version" in updates[key]:
                                for k, i in item.items():
                                    updates[key][k] = str(i)
                        continue
                    # Set update if values don't match
                    if host["interfaces"][0][key] != str(item):
                        updates[key] = item
            if updates:
                # If interface updates have been found: push to Zabbix
                self.logger.warning(f"Host {self.name}: Interface OUT of sync.")
                if "type" in updates:
                    # Changing interface type not supported. Raise exception.
                    e = (f"Host {self.name}: changing interface type to "
                         f"{str(updates['type'])} is not supported.")
                    self.logger.error(e)
                    raise InterfaceConfigError(e)
                # Set interfaceID for Zabbix config
                updates["interfaceid"] = host["interfaces"][0]['interfaceid']
                try:
                    # API call to Zabbix
                    self.zabbix.hostinterface.update(updates)
                    e = f"Host {self.name}: solved interface conflict."
                    self.logger.info(e)
                    self.create_journal_entry("info", e)
                except APIRequestError as e:
                    msg = f"Zabbix returned the following error: {str(e)}."
                    self.logger.error(msg)
                    raise SyncExternalError(msg) from e
            else:
                # If no updates are found, Zabbix interface is in-sync
                e = f"Host {self.name}: interface in-sync."
                self.logger.debug(e)
        else:
            e = (f"Host {self.name} has unsupported interface configuration."
                 f" Host has total of {len(host['interfaces'])} interfaces. "
                 "Manual interfention required.")
            self.logger.error(e)
            raise SyncInventoryError(e)

    def create_journal_entry(self, severity, message):
        """
        Send a new Journal entry to Netbox. Usefull for viewing actions
        in Netbox without having to look in Zabbix or the script log output
        """
        if self.journal:
            # Check if the severity is valid
            if severity not in ["info", "success", "warning", "danger"]:
                self.logger.warning(f"Value {severity} not valid for NB journal entries.")
                return False
            journal = {"assigned_object_type": "dcim.device",
                       "assigned_object_id": self.id,
                       "kind": severity,
                       "comments": message
                       }
            try:
                self.nb_journals.create(journal)
                self.logger.debug(f"Host {self.name}: Created journal entry in Netbox")
                return True
            except JournalError(e) as e:
                self.logger.warning("Unable to create journal entry for "
                                    f"{self.name}: NB returned {e}")
            return False
        return False

    def zbx_template_comparer(self, tmpls_from_zabbix):
        """
        Compares the Netbox and Zabbix templates with each other.
        Should there be a mismatch then the function will return false

        INPUT: list of NB and ZBX templates
        OUTPUT: Boolean True/False
        """
        succesfull_templates = []
        # Go through each Netbox template
        for nb_tmpl in self.zbx_templates:
            # Go through each Zabbix template
            for pos, zbx_tmpl in enumerate(tmpls_from_zabbix):
                # Check if template IDs match
                if nb_tmpl["templateid"] == zbx_tmpl["templateid"]:
                    # Templates match. Remove this template from the Zabbix templates
                    # and add this NB template to the list of successfull templates
                    tmpls_from_zabbix.pop(pos)
                    succesfull_templates.append(nb_tmpl)
                    self.logger.debug(f"Host {self.name}: template "
                                      f"{nb_tmpl['name']} is present in Zabbix.")
                    break
        if len(succesfull_templates) == len(self.zbx_templates) and len(tmpls_from_zabbix) == 0:
            # All of the Netbox templates have been confirmed as successfull
            # and the ZBX template list is empty. This means that
            # all of the templates match.
            return True
        return False
