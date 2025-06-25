# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation, too-many-lines, too-many-public-methods, duplicate-code
"""
Device specific handeling for NetBox to Zabbix
"""

from copy import deepcopy
from logging import getLogger
from re import search
from operator import itemgetter

from zabbix_utils import APIRequestError
from pynetbox import RequestError as NetboxRequestError

from modules.exceptions import (
    InterfaceConfigError,
    SyncExternalError,
    SyncInventoryError,
    TemplateError,
)
from modules.hostgroups import Hostgroup
from modules.interface import ZabbixInterface
from modules.tags import ZabbixTags
from modules.tools import field_mapper, remove_duplicates, sanatize_log_output
from modules.usermacros import ZabbixUsermacros
from modules.config import load_config

config = load_config()


class PhysicalDevice:
    # pylint: disable=too-many-instance-attributes, too-many-arguments, too-many-positional-arguments
    """
    Represents Network device.
    INPUT: (NetBox device class, ZabbixAPI class, journal flag, NB journal class)
    """

    def __init__(
        self, nb, zabbix, nb_journal_class, nb_version, journal=None, logger=None
    ):
        self.nb = nb
        self.id = nb.id
        self.name = nb.name
        self.visible_name = None
        self.status = nb.status.label
        self.zabbix = zabbix
        self.zabbix_id = None
        self.group_ids = []
        self.nb_api_version = nb_version
        self.zbx_template_names = []
        self.zbx_templates = []
        self.hostgroups = []
        self.hostgroup_type = "dev"
        self.tenant = nb.tenant
        self.config_context = nb.config_context
        self.zbxproxy = None
        self.zabbix_state = 0
        self.journal = journal
        self.nb_journals = nb_journal_class
        self.inventory_mode = -1
        self.inventory = {}
        self.usermacros = []
        self.tags = {}
        self.logger = logger if logger else getLogger(__name__)
        self._setBasics()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    def _inventory_map(self):
        """Use device inventory maps"""
        return config["device_inventory_map"]

    def _usermacro_map(self):
        """Use device inventory maps"""
        return config["device_usermacro_map"]

    def _tag_map(self):
        """Use device host tag maps"""
        return config["device_tag_map"]

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
            self.logger.warning(e)
            raise SyncInventoryError(e)

        # Check if device has custom field for ZBX ID
        if config["device_cf"] in self.nb.custom_fields:
            self.zabbix_id = self.nb.custom_fields[config["device_cf"]]
        else:
            e = f"Host {self.name}: Custom field {config['device_cf']} not present"
            self.logger.error(e)
            raise SyncInventoryError(e)

        # Validate hostname format.
        odd_character_list = ["ä", "ö", "ü", "Ä", "Ö", "Ü", "ß"]
        self.use_visible_name = False
        if any(letter in self.name for letter in odd_character_list) or bool(
            search("[\u0400-\u04ff]", self.name)
        ):
            self.name = f"NETBOX_ID{self.id}"
            self.visible_name = self.nb.name
            self.use_visible_name = True
            self.logger.info(
                "Host %s contains special characters. Using %s as name for the NetBox object and using %s as visible name in Zabbix.",
                self.visible_name,
                self.name,
                self.visible_name,
            )
        else:
            pass

    def set_hostgroup(self, hg_format, nb_site_groups, nb_regions):
        """Set the hostgroup for this device"""
        # Create new Hostgroup instance
        hg = Hostgroup(
            self.hostgroup_type,
            self.nb,
            self.nb_api_version,
            logger=self.logger,
            nested_sitegroup_flag=config["traverse_site_groups"],
            nested_region_flag=config["traverse_regions"],
            nb_groups=nb_site_groups,
            nb_regions=nb_regions,
        )
        # Generate hostgroup based on hostgroup format
        if isinstance(hg_format, list):
            self.hostgroups = [hg.generate(f) for f in hg_format]
        else:
            self.hostgroups.append(hg.generate(hg_format))
        # Remove duplicates and None values
        self.hostgroups = list(filter(None, list(set(self.hostgroups))))
        if self.hostgroups:
            self.logger.debug(
                "Host %s: Should be member of groups: %s", self.name, self.hostgroups
            )
            return True
        return False

    def set_template(self, prefer_config_context, overrule_custom):
        """Set Template"""
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
        """Get template from custom field"""
        # Get Zabbix templates from the device type
        device_type_cfs = self.nb.device_type.custom_fields
        # Check if the ZBX Template CF is present
        if config["template_cf"] in device_type_cfs:
            # Set value to template
            return [device_type_cfs[config["template_cf"]]]
        # Custom field not found, return error
        e = (
            f"Custom field {config['template_cf']} not "
            f"found for {self.nb.device_type.manufacturer.name}"
            f" - {self.nb.device_type.display}."
        )
        self.logger.warning(e)
        raise TemplateError(e)

    def get_templates_context(self):
        """Get Zabbix templates from the device context"""
        if "zabbix" not in self.config_context:
            e = (
                f"Host {self.name}: Key 'zabbix' not found in config "
                "context for template lookup"
            )
            raise TemplateError(e)
        if "templates" not in self.config_context["zabbix"]:
            e = (
                f"Host {self.name}: Key 'templates' not found in config "
                "context 'zabbix' for template lookup"
            )
            raise TemplateError(e)
        # Check if format is list or string.
        if isinstance(self.config_context["zabbix"]["templates"], str):
            return [self.config_context["zabbix"]["templates"]]
        return self.config_context["zabbix"]["templates"]

    def set_inventory(self, nbdevice):
        """Set host inventory"""
        # Set inventory mode. Default is disabled (see class init function).
        if config["inventory_mode"] == "disabled":
            if config["inventory_sync"]:
                self.logger.error(
                    "Host %s: Unable to map NetBox inventory to Zabbix. Inventory sync is enabled in  config but inventory mode is disabled",
                    self.name,
                )
            return True
        if config["inventory_mode"] == "manual":
            self.inventory_mode = 0
        elif config["inventory_mode"] == "automatic":
            self.inventory_mode = 1
        else:
            self.logger.error(
                "Host %s: Specified value for inventory mode in config is not valid. Got value %s",
                self.name,
                config["inventory_mode"],
            )
            return False
        self.inventory = {}
        if config["inventory_sync"] and self.inventory_mode in [0, 1]:
            self.logger.debug("Host %s: Starting inventory mapper.", self.name)
            self.inventory = field_mapper(
                self.name, self._inventory_map(), nbdevice, self.logger
            )
            self.logger.debug(
                "Host %s: Resolved inventory: %s", self.name, self.inventory
            )
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
            e = (
                f"Unable to proces {self.name} for cluster calculation: "
                f"not part of a cluster."
            )
            self.logger.info(e)
            raise SyncInventoryError(e)
        if not self.nb.virtual_chassis.master:
            e = (
                f"{self.name} is part of a NetBox virtual chassis which does "
                "not have a master configured. Skipping for this reason."
            )
            self.logger.warning(e)
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
            self.logger.info(
                "Host %s is primary cluster member. Modifying hostname from %s to %s.",
                self.name,
                self.name,
                self.nb.virtual_chassis.name,
            )
            self.name = self.nb.virtual_chassis.name
            return True
        self.logger.info("Host %s is non-primary cluster member.", self.name)
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
            self.logger.warning(e)
            raise SyncInventoryError()
        # Set variable to empty list
        self.zbx_templates = []
        # Go through all templates definded in NetBox
        for nb_template in self.zbx_template_names:
            template_match = False
            # Go through all templates found in Zabbix
            for zbx_template in templates:
                # If the template names match
                if zbx_template["name"] == nb_template:
                    # Set match variable to true, add template details
                    # to class variable and return debug log
                    template_match = True
                    self.zbx_templates.append(
                        {
                            "templateid": zbx_template["templateid"],
                            "name": zbx_template["name"],
                        }
                    )
                    e = (
                        f"Host {self.name}: Found template '{zbx_template['name']}' "
                        f"(ID:{zbx_template['templateid']})"
                    )
                    self.logger.debug(e)
            # Return error should the template not be found in Zabbix
            if not template_match:
                e = (
                    f"Unable to find template {nb_template} "
                    f"for host {self.name} in Zabbix. Skipping host..."
                )
                self.logger.warning(e)
                raise SyncInventoryError(e)

    def setZabbixGroupID(self, groups):
        """
        Sets Zabbix group ID as instance variable
        INPUT: list of hostgroups
        OUTPUT: True / False
        """
        # Go through all groups
        for hg in self.hostgroups:
            for group in groups:
                if group["name"] == hg:
                    self.group_ids.append({"groupid": group["groupid"]})
                    e = (
                        f"Host {self.name}: Matched group "
                        f'"{group["name"]}" (ID:{group["groupid"]})'
                    )
                    self.logger.debug(e)
        if len(self.group_ids) == len(self.hostgroups):
            return True
        return False

    def cleanup(self):
        """
        Removes device from external resources.
        Resets custom fields in NetBox.
        """
        if self.zabbix_id:
            try:
                # Check if the Zabbix host exists in Zabbix
                zbx_host = bool(
                    self.zabbix.host.get(filter={"hostid": self.zabbix_id}, output=[])
                )
                e = (
                    f"Host {self.name}: was already deleted from Zabbix."
                    " Removed link in NetBox."
                )
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
        """Sets the hostID custom field in NetBox to zero,
        effectively destroying the link"""
        self.nb.custom_fields[config["device_cf"]] = None
        self.nb.save()

    def _zabbixHostnameExists(self):
        """
        Checks if hostname exists in Zabbix.
        """
        # Validate the hostname or visible name field
        if not self.use_visible_name:
            zbx_filter = {"host": self.name}
        else:
            zbx_filter = {"name": self.visible_name}
        host = self.zabbix.host.get(filter=zbx_filter, output=[])
        return bool(host)

    def setInterfaceDetails(self):
        """
        Checks interface parameters from NetBox and
        creates a model for the interface to be used in Zabbix.
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
                interface.set_default_snmp()
            return [interface.interface]
        except InterfaceConfigError as e:
            message = f"{self.name}: {e}"
            self.logger.warning(message)
            raise SyncInventoryError(message) from e

    def set_usermacros(self):
        """
        Generates Usermacros
        """
        macros = ZabbixUsermacros(
            self.nb,
            self._usermacro_map(),
            config["usermacro_sync"],
            logger=self.logger,
            host=self.name,
        )
        if macros.sync is False:
            self.usermacros = []
            return True

        self.usermacros = macros.generate()
        return True

    def set_tags(self):
        """
        Generates Host Tags
        """
        tags = ZabbixTags(
            self.nb,
            self._tag_map(),
            tag_sync=config["tag_sync"],
            tag_lower=config["tag_lower"],
            tag_name=config["tag_name"],
            tag_value=config["tag_value"],
            logger=self.logger,
            host=self.name,
        )
        if config["tag_sync"] is False:
            self.tags = []
            return False
        self.tags = tags.generate()
        return True

    def setProxy(self, proxy_list):
        """
        Sets proxy or proxy group if this
        value has been defined in config context

        input: List of all proxies and proxy groups in standardized format
        """
        # check if the key Zabbix is defined in the config context
        if "zabbix" not in self.nb.config_context:
            return False
        if (
            "proxy" in self.nb.config_context["zabbix"]
            and not self.nb.config_context["zabbix"]["proxy"]
        ):
            return False
        # Proxy group takes priority over a proxy due
        # to it being HA and therefore being more reliable
        # Includes proxy group fix since Zabbix <= 6 should ignore this
        proxy_types = ["proxy"]
        if str(self.zabbix.version).startswith("7"):
            # Only insert groups in front of list for Zabbix7
            proxy_types.insert(0, "proxy_group")
        for proxy_type in proxy_types:
            # Check if the key exists in NetBox CC
            if proxy_type in self.nb.config_context["zabbix"]:
                proxy_name = self.nb.config_context["zabbix"][proxy_type]
                # go through all proxies
                for proxy in proxy_list:
                    # If the proxy does not match the type, ignore and continue
                    if not proxy["type"] == proxy_type:
                        continue
                    # If the proxy name matches
                    if proxy["name"] == proxy_name:
                        self.logger.debug(
                            "Host %s: using {proxy['type']} '%s'", self.name, proxy_name
                        )
                        self.zbxproxy = proxy
                        return True
                self.logger.warning(
                    "Host %s: unable to find proxy %s", self.name, proxy_name
                )
        return False

    def createInZabbix(
        self,
        groups,
        templates,
        proxies,
        description="Host added by NetBox sync script.",
    ):
        """
        Creates Zabbix host object with parameters from NetBox object.
        """
        # Check if hostname is already present in Zabbix
        if not self._zabbixHostnameExists():
            # Set group and template ID's for host
            if not self.setZabbixGroupID(groups):
                e = (
                    f"Unable to find group '{self.hostgroup}' "
                    f"for host {self.name} in Zabbix."
                )
                self.logger.warning(e)
                raise SyncInventoryError(e)
            self.zbxTemplatePrepper(templates)
            templateids = []
            for template in self.zbx_templates:
                templateids.append({"templateid": template["templateid"]})
            # Set interface, group and template configuration
            interfaces = self.setInterfaceDetails()
            # Set Zabbix proxy if defined
            self.setProxy(proxies)
            # Set basic data for host creation
            create_data = {
                "host": self.name,
                "name": self.visible_name,
                "status": self.zabbix_state,
                "interfaces": interfaces,
                "groups": self.group_ids,
                "templates": templateids,
                "description": description,
                "inventory_mode": self.inventory_mode,
                "inventory": self.inventory,
                "macros": self.usermacros,
                "tags": self.tags,
            }
            # If a Zabbix proxy or Zabbix Proxy group has been defined
            if self.zbxproxy:
                # If a lower version than 7 is used, we can assume that
                # the proxy is a normal proxy and not a proxy group
                if not str(self.zabbix.version).startswith("7"):
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
                msg = f"Host {self.name}: Couldn't create. Zabbix returned {str(e)}."
                self.logger.error(msg)
                raise SyncExternalError(msg) from e
            # Set NetBox custom field to hostID value.
            self.nb.custom_fields[config["device_cf"]] = int(self.zabbix_id)
            self.nb.save()
            msg = f"Host {self.name}: Created host in Zabbix. (ID:{self.zabbix_id})"
            self.logger.info(msg)
            self.create_journal_entry("success", msg)
        else:
            self.logger.error(
                "Host %s: Unable to add to Zabbix. Host already present.", self.name
            )

    def createZabbixHostgroup(self, hostgroups):
        """
        Creates Zabbix host group based on hostgroup format.
        Creates multiple when using a nested format.
        """
        final_data = []
        # Check if the hostgroup is in a nested format and check each parent
        for hostgroup in self.hostgroups:
            for pos in range(len(hostgroup.split("/"))):
                zabbix_hg = hostgroup.rsplit("/", pos)[0]
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
                    final_data.append(
                        {"groupid": groupid["groupids"][0], "name": zabbix_hg}
                    )
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
            e = (
                f"Host {self.name}: Unable to update. "
                f"Zabbix returned the following error: {str(e)}."
            )
            self.logger.error(e)
            raise SyncExternalError(e) from None
        self.logger.info(
            "Host %s: updated with data %s.", self.name, sanatize_log_output(kwargs)
        )
        self.create_journal_entry("info", "Updated host in Zabbix with latest NB data.")

    def ConsistencyCheck(
        self, groups, templates, proxies, proxy_power, create_hostgroups
    ):
        # pylint: disable=too-many-branches, too-many-statements
        """
        Checks if Zabbix object is still valid with NetBox parameters.
        """
        # If group is found or if the hostgroup is nested
        if not self.setZabbixGroupID(groups):  # or len(self.hostgroups.split("/")) > 1:
            if create_hostgroups:
                # Script is allowed to create a new hostgroup
                new_groups = self.createZabbixHostgroup(groups)
                for group in new_groups:
                    # Add all new groups to the list of groups
                    groups.append(group)
            # check if the initial group was not already found (and this is a nested folder check)
            if not self.group_ids:
                # Function returns true / false but also sets GroupID
                if not self.setZabbixGroupID(groups) and not create_hostgroups:
                    e = (
                        f"Host {self.name}: different hostgroup is required but "
                        "unable to create hostgroup without generation permission."
                    )
                    self.logger.warning(e)
                    raise SyncInventoryError(e)

        # Prepare templates and proxy config
        self.zbxTemplatePrepper(templates)
        self.setProxy(proxies)
        # Get host object from Zabbix
        host = self.zabbix.host.get(
            filter={"hostid": self.zabbix_id},
            selectInterfaces=["type", "ip", "port", "details", "interfaceid"],
            selectGroups=["groupid"],
            selectHostGroups=["groupid"],
            selectParentTemplates=["templateid"],
            selectInventory=list(self._inventory_map().values()),
            selectMacros=["macro", "value", "type", "description"],
            selectTags=["tag", "value"],
        )
        if len(host) > 1:
            e = (
                f"Got {len(host)} results for Zabbix hosts "
                f"with ID {self.zabbix_id} - hostname {self.name}."
            )
            self.logger.error(e)
            raise SyncInventoryError(e)
        if len(host) == 0:
            e = (
                f"Host {self.name}: No Zabbix host found. "
                f"This is likely the result of a deleted Zabbix host "
                f"without zeroing the ID field in NetBox."
            )
            self.logger.error(e)
            raise SyncInventoryError(e)
        host = host[0]
        if host["host"] == self.name:
            self.logger.debug("Host %s: Hostname in-sync.", self.name)
        else:
            self.logger.info(
                "Host %s: Hostname OUT of sync. Received value: %s",
                self.name,
                host["host"],
            )
            self.updateZabbixHost(host=self.name)

        # Execute check depending on wether the name is special or not
        if self.use_visible_name:
            if host["name"] == self.visible_name:
                self.logger.debug("Host %s: Visible name in-sync.", self.name)
            else:
                self.logger.info(
                    "Host %s: Visible name OUT of sync. Received value: %s",
                    self.name,
                    host["name"],
                )
                self.updateZabbixHost(name=self.visible_name)

        # Check if the templates are in-sync
        if not self.zbx_template_comparer(host["parentTemplates"]):
            self.logger.info("Host %s: Template(s) OUT of sync.", self.name)
            # Prepare Templates for API parsing
            templateids = []
            for template in self.zbx_templates:
                templateids.append({"templateid": template["templateid"]})
            # Update Zabbix with NB templates and clear any old / lost templates
            self.updateZabbixHost(
                templates_clear=host["parentTemplates"], templates=templateids
            )
        else:
            self.logger.debug("Host %s: Template(s) in-sync.", self.name)

        # Check if Zabbix version is 6 or higher. Issue #93
        group_dictname = "hostgroups"
        if str(self.zabbix.version).startswith(("6", "5")):
            group_dictname = "groups"
        # Check if hostgroups match
        if sorted(host[group_dictname], key=itemgetter("groupid")) == sorted(
            self.group_ids, key=itemgetter("groupid")
        ):
            self.logger.debug("Host %s: Hostgroups in-sync.", self.name)
        else:
            self.logger.info("Host %s: Hostgroups OUT of sync.", self.name)
            self.updateZabbixHost(groups=self.group_ids)

        if int(host["status"]) == self.zabbix_state:
            self.logger.debug("Host %s: Status in-sync.", self.name)
        else:
            self.logger.info("Host %s: Status OUT of sync.", self.name)
            self.updateZabbixHost(status=str(self.zabbix_state))

        # Check if a proxy has been defined
        if self.zbxproxy:
            # Check if proxy or proxy group is defined
            if (
                self.zbxproxy["idtype"] in host
                and host[self.zbxproxy["idtype"]] == self.zbxproxy["id"]
            ):
                self.logger.debug("Host %s: Proxy in-sync.", self.name)
            # Backwards compatibility for Zabbix <= 6
            elif "proxy_hostid" in host and host["proxy_hostid"] == self.zbxproxy["id"]:
                self.logger.debug("Host %s: Proxy in-sync.", self.name)
            # Proxy does not match, update Zabbix
            else:
                self.logger.info("Host %s: Proxy OUT of sync.", self.name)
                # Zabbix <= 6 patch
                if not str(self.zabbix.version).startswith("7"):
                    self.updateZabbixHost(proxy_hostid=self.zbxproxy["id"])
                # Zabbix 7+
                else:
                    # Prepare data structure for updating either proxy or group
                    update_data = {
                        self.zbxproxy["idtype"]: self.zbxproxy["id"],
                        "monitored_by": self.zbxproxy["monitored_by"],
                    }
                    self.updateZabbixHost(**update_data)
        else:
            # No proxy is defined in NetBox
            proxy_set = False
            # Check if a proxy is defined. Uses the proxy_hostid key for backwards compatibility
            for key in ("proxy_hostid", "proxyid", "proxy_groupid"):
                if key in host:
                    if bool(int(host[key])):
                        proxy_set = True
            if proxy_power and proxy_set:
                # Zabbix <= 6 fix
                self.logger.warning(
                    "Host %s: No proxy is configured in NetBox but is configured in Zabbix. Removing proxy config in Zabbix",
                    self.name,
                )
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
                self.logger.warning(
                    "Host %s: Is configured with proxy in Zabbix but not in NetBox. The -p flag was ommited: no changes have been made.",
                    self.name,
                )
            if not proxy_set:
                self.logger.debug("Host %s: Proxy in-sync.", self.name)
        # Check host inventory mode
        if str(host["inventory_mode"]) == str(self.inventory_mode):
            self.logger.debug("Host %s: inventory_mode in-sync.", self.name)
        else:
            self.logger.info("Host %s: inventory_mode OUT of sync.", self.name)
            self.updateZabbixHost(inventory_mode=str(self.inventory_mode))
        if config["inventory_sync"] and self.inventory_mode in [0, 1]:
            # Check host inventory mapping
            if host["inventory"] == self.inventory:
                self.logger.debug("Host %s: Inventory in-sync.", self.name)
            else:
                self.logger.info("Host %s: Inventory OUT of sync.", self.name)
                self.updateZabbixHost(inventory=self.inventory)

        # Check host usermacros
        if config["usermacro_sync"]:
            # Make a full copy synce we dont want to lose the original value
            # of secret type macros from Netbox
            netbox_macros = deepcopy(self.usermacros)
            # Set the sync bit
            full_sync_bit = bool(str(config["usermacro_sync"]).lower() == "full")
            for macro in netbox_macros:
                # If the Macro is a secret and full sync is NOT activated
                if macro["type"] == str(1) and not full_sync_bit:
                    # Remove the value as the Zabbix api does not return the value key
                    # This is required when you want to do a diff between both lists
                    macro.pop("value")

            # Sort all lists
            def filter_with_macros(macro):
                return macro["macro"]

            host["macros"].sort(key=filter_with_macros)
            netbox_macros.sort(key=filter_with_macros)
            # Check if both lists are the same
            if host["macros"] == netbox_macros:
                self.logger.debug("Host %s: Usermacros in-sync.", self.name)
            else:
                self.logger.info("Host %s: Usermacros OUT of sync.", self.name)
                # Update Zabbix with NetBox usermacros
                self.updateZabbixHost(macros=self.usermacros)

        # Check host tags
        if config["tag_sync"]:
            if remove_duplicates(host["tags"], sortkey="tag") == self.tags:
                self.logger.debug("Host %s: Tags in-sync.", self.name)
            else:
                self.logger.info("Host %s: Tags OUT of sync.", self.name)
                self.updateZabbixHost(tags=self.tags)

        # If only 1 interface has been found
        # pylint: disable=too-many-nested-blocks
        if len(host["interfaces"]) == 1:
            updates = {}
            # Go through each key / item and check if it matches Zabbix
            for key, item in self.setInterfaceDetails()[0].items():
                # Check if NetBox value is found in Zabbix
                if key in host["interfaces"][0]:
                    # If SNMP is used, go through nested dict
                    # to compare SNMP parameters
                    if isinstance(item, dict) and key == "details":
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
                self.logger.info("Host %s: Interface OUT of sync.", self.name)
                if "type" in updates:
                    # Changing interface type not supported. Raise exception.
                    e = (
                        f"Host {self.name}: Changing interface type to "
                        f"{str(updates['type'])} is not supported."
                    )
                    self.logger.error(e)
                    raise InterfaceConfigError(e)
                # Set interfaceID for Zabbix config
                updates["interfaceid"] = host["interfaces"][0]["interfaceid"]
                try:
                    # API call to Zabbix
                    self.zabbix.hostinterface.update(updates)
                    err_msg = (
                        f"Host {self.name}: Updated interface "
                        f"with data {sanatize_log_output(updates)}."
                    )
                    self.logger.info(err_msg)
                    self.create_journal_entry("info", err_msg)
                except APIRequestError as e:
                    msg = f"Zabbix returned the following error: {str(e)}."
                    self.logger.error(msg)
                    raise SyncExternalError(msg) from e
            else:
                # If no updates are found, Zabbix interface is in-sync
                self.logger.debug("Host %s: Interface in-sync.", self.name)
        else:
            err_msg = (
                f"Host {self.name}: Has unsupported interface configuration."
                f" Host has total of {len(host['interfaces'])} interfaces. "
                "Manual intervention required."
            )
            self.logger.error(err_msg)
            raise SyncInventoryError(err_msg)

    def create_journal_entry(self, severity, message):
        """
        Send a new Journal entry to NetBox. Usefull for viewing actions
        in NetBox without having to look in Zabbix or the script log output
        """
        if self.journal:
            # Check if the severity is valid
            if severity not in ["info", "success", "warning", "danger"]:
                self.logger.warning(
                    "Value %s not valid for NB journal entries.", severity
                )
                return False
            journal = {
                "assigned_object_type": "dcim.device",
                "assigned_object_id": self.id,
                "kind": severity,
                "comments": message,
            }
            try:
                self.nb_journals.create(journal)
                self.logger.debug("Host %s: Created journal entry in NetBox", self.name)
                return True
            except NetboxRequestError as e:
                self.logger.warning(
                    "Unable to create journal entry for %s: NB returned {e}", self.name
                )
            return False
        return False

    def zbx_template_comparer(self, tmpls_from_zabbix):
        """
        Compares the NetBox and Zabbix templates with each other.
        Should there be a mismatch then the function will return false

        INPUT: list of NB and ZBX templates
        OUTPUT: Boolean True/False
        """
        succesfull_templates = []
        # Go through each NetBox template
        for nb_tmpl in self.zbx_templates:
            # Go through each Zabbix template
            for pos, zbx_tmpl in enumerate(tmpls_from_zabbix):
                # Check if template IDs match
                if nb_tmpl["templateid"] == zbx_tmpl["templateid"]:
                    # Templates match. Remove this template from the Zabbix templates
                    # and add this NB template to the list of successfull templates
                    tmpls_from_zabbix.pop(pos)
                    succesfull_templates.append(nb_tmpl)
                    self.logger.debug(
                        "Host %s: Template '%s' is present in Zabbix.",
                        self.name,
                        nb_tmpl["name"],
                    )
                    break
        if (
            len(succesfull_templates) == len(self.zbx_templates)
            and len(tmpls_from_zabbix) == 0
        ):
            # All of the NetBox templates have been confirmed as successfull
            # and the ZBX template list is empty. This means that
            # all of the templates match.
            return True
        return False
