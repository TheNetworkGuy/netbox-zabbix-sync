#!/usr/bin/python3
"""Netbox to Zabbix sync script."""

from os import environ, path, sys
from packaging import version
import logging
import argparse
from pynetbox import api
from pyzabbix import ZabbixAPI, ZabbixAPIException
try:
    from config import (
        templates_config_context,
        templates_config_context_overrule,
        clustering, create_hostgroups,
        create_journal, full_proxy_sync,
        template_cf, device_cf,
        zabbix_device_removal,
        zabbix_device_disable,
        hostgroup_format,
        nb_device_filter
    )
    
except ModuleNotFoundError:
    print(f"Configuration file config.py not found in main directory."
           "Please create the file or rename the config.py.example file to config.py.")
    sys.exit(0)

# Set logging
log_format = logging.Formatter('%(asctime)s - %(name)s - '
                               '%(levelname)s - %(message)s')
lgout = logging.StreamHandler()
lgout.setFormatter(log_format)
lgout.setLevel(logging.DEBUG)

lgfile = logging.FileHandler(path.join(path.dirname(
                             path.realpath(__file__)), "sync.log"))
lgfile.setFormatter(log_format)
lgfile.setLevel(logging.DEBUG)

logger = logging.getLogger("Netbox-Zabbix-sync")
logger.addHandler(lgout)
logger.addHandler(lgfile)
logger.setLevel(logging.WARNING)


def main(arguments):
    """Run the sync process."""
    # set environment variables
    if(arguments.verbose):
        logger.setLevel(logging.DEBUG)
    env_vars = ["ZABBIX_HOST", "ZABBIX_USER", "ZABBIX_PASS",
                "NETBOX_HOST", "NETBOX_TOKEN"]
    for var in env_vars:
        if var not in environ:
            e = f"Environment variable {var} has not been defined."
            logger.error(e)
            raise EnvironmentVarError(e)
    # Get all virtual environment variables
    zabbix_host = environ.get("ZABBIX_HOST")
    zabbix_user = environ.get("ZABBIX_USER")
    zabbix_pass = environ.get("ZABBIX_PASS")
    netbox_host = environ.get("NETBOX_HOST")
    netbox_token = environ.get("NETBOX_TOKEN")
    # Set Netbox API
    netbox = api(netbox_host, token=netbox_token, threading=True)
    # Check if the provided Hostgroup layout is valid
    hg_objects = hostgroup_format.split("/")
    allowed_objects = ["dev_location", "dev_role", "manufacturer", "region",
                        "site", "site_group", "tenant", "tenant_group"]
    # Create API call to get all custom fields which are on the device objects
    device_cfs = netbox.extras.custom_fields.filter(type="text", content_type_id=23)
    for cf in device_cfs:
        allowed_objects.append(cf.name)
    for object in hg_objects:
        if(object not in allowed_objects):
            e = (f"Hostgroup item {object} is not valid. Make sure you"
                    " use valid items and seperate them with '/'.")
            logger.error(e)
            raise HostgroupError(e)
    # Set Zabbix API
    try:
        zabbix = ZabbixAPI(zabbix_host)
        zabbix.login(zabbix_user, zabbix_pass)
    except ZabbixAPIException as e:
        e = f"Zabbix returned the following error: {str(e)}."
        logger.error(e)
    # Get all Zabbix and Netbox data
    netbox_devices = netbox.dcim.devices.filter(**nb_device_filter)
    netbox_journals = netbox.extras.journal_entries
    zabbix_groups = zabbix.hostgroup.get(output=['groupid', 'name'])
    zabbix_templates = zabbix.template.get(output=['templateid', 'name'])
    if version.parse(zabbix.api_version()) < version.parse("7.0.0"):
        zabbix_proxys = zabbix.proxy.get(output=['proxyid', 'host'])
    else:
        zabbix_proxys = zabbix.proxy.get(output=['proxyid', 'name'])
    # Go through all Netbox devices
    for nb_device in netbox_devices:
        try:
            device = NetworkDevice(nb_device, zabbix, netbox_journals,
                                   create_journal)
            device.set_hostgroup(hostgroup_format)
            device.set_template(templates_config_context, templates_config_context_overrule)
            # Checks if device is part of cluster.
            # Requires clustering variable
            if(device.isCluster() and clustering):
                # Check if device is master or slave
                if(device.promoteMasterDevice()):
                    e = (f"Device {device.name} is "
                         f"part of cluster and primary.")
                    logger.info(e)
                else:
                    # Device is secondary in cluster.
                    # Don't continue with this device.
                    e = (f"Device {device.name} is part of cluster "
                         f"but not primary. Skipping this host...")
                    logger.info(e)
                    continue
            # Checks if device is in cleanup state
            if(device.status in zabbix_device_removal):
                if(device.zabbix_id):
                    # Delete device from Zabbix
                    # and remove hostID from Netbox.
                    device.cleanup()
                    logger.info(f"Cleaned up host {device.name}.")

                else:
                    # Device has been added to Netbox
                    # but is not in Activate state
                    logger.info(f"Skipping host {device.name} since its "
                                f"not in the active state.")
                continue
            elif(device.status in zabbix_device_disable):
                device.zabbix_state = 1
            # Add hostgroup is variable is True
            # and Hostgroup is not present in Zabbix
            if(create_hostgroups):
                for group in zabbix_groups:
                    # If hostgroup is already present in Zabbix
                    if(group["name"] == device.hostgroup):
                        break
                else:
                    # Create new hostgroup
                    hostgroup = device.createZabbixHostgroup()
                    zabbix_groups.append(hostgroup)
            # Device is already present in Zabbix
            if(device.zabbix_id):
                device.ConsistencyCheck(zabbix_groups, zabbix_templates,
                                        zabbix_proxys, full_proxy_sync)
            # Add device to Zabbix
            else:
                device.createInZabbix(zabbix_groups, zabbix_templates,
                                      zabbix_proxys)
        except SyncError:
            pass


class SyncError(Exception):
    pass


class SyncExternalError(SyncError):
    pass


class SyncInventoryError(SyncError):
    pass


class SyncDuplicateError(SyncError):
    pass


class EnvironmentVarError(SyncError):
    pass


class InterfaceConfigError(SyncError):
    pass


class ProxyConfigError(SyncError):
    pass


class HostgroupError(SyncError):
    pass

class TemplateError(SyncError):
    pass

class NetworkDevice():

    """
    Represents Network device.
    INPUT: (Netbox device class, ZabbixAPI class, journal flag, NB journal class)
    """

    def __init__(self, nb, zabbix, nb_journal_class, journal=None):
        self.nb = nb
        self.id = nb.id
        self.name = nb.name
        self.status = nb.status.label
        self.zabbix = zabbix
        self.tenant = nb.tenant
        self.config_context = nb.config_context
        self.zbxproxy = "0"
        self.zabbix_state = 0
        self.journal = journal
        self.nb_journals = nb_journal_class
        self._setBasics()

    def _setBasics(self):
        """
        Sets basic information like IP address.
        """
        # Return error if device does not have primary IP.
        if(self.nb.primary_ip):
            self.cidr = self.nb.primary_ip.address
            self.ip = self.cidr.split("/")[0]
        else:
            e = f"Device {self.name}: no primary IP."
            logger.warning(e)
            raise SyncInventoryError(e)

        # Check if device has custom field for ZBX ID
        if(device_cf in self.nb.custom_fields):
            self.zabbix_id = self.nb.custom_fields[device_cf]
        else:
            e = f"Custom field {device_cf} not found for {self.name}."
            logger.warning(e)
            raise SyncInventoryError(e)

    def set_hostgroup(self, format):
        """Set the hostgroup for this device"""
        # Get all variables from the NB data
        dev_location = str(self.nb.location) if self.nb.location else None
        dev_role = self.nb.device_role.name
        manufacturer = self.nb.device_type.manufacturer.name
        region = str(self.nb.site.region) if self.nb.site.region else None
        site = self.nb.site.name
        site_group = str(self.nb.site.group) if self.nb.site.group else None
        tenant = str(self.tenant) if self.tenant else None
        tenant_group = str(self.tenant.group) if tenant else None
        # Set mapper for string -> variable
        hostgroup_vars = {"dev_location": dev_location, "dev_role": dev_role,
                          "manufacturer": manufacturer, "region": region,
                          "site": site, "site_group": site_group,
                           "tenant": tenant, "tenant_group": tenant_group}
        # Generate list based off string input format
        hg_items = format.split("/")
        hostgroup = ""
        # Go through all hostgroup items
        for item in hg_items:
            # Check if the variable (such as Tenant) is empty.
            if(not hostgroup_vars[item]):
                continue
            # Check if the item is a custom field name
            if(item not in hostgroup_vars):
                cf_value = self.nb.custom_fields[item] if item in self.nb.custom_fields else None
                if(cf_value):
                    # If there is a cf match, add the value of this cf to the hostgroup
                    hostgroup += cf_value + "/"
                # Should there not be a match, this means that
                # the variable is invalid. Skip regardless.
                continue
            # Add value of predefined variable to hostgroup format
            hostgroup += hostgroup_vars[item] + "/"
        # If the final hostgroup variable is empty
        if(not hostgroup):
            e = (f"{self.name} has no reliable hostgroup. This is"
                 "most likely due to the use of custom fields that are empty.")
            logger.error(e)
            raise SyncInventoryError(e)
        # Remove final inserted "/" and set hostgroup to class var
        self.hostgroup = hostgroup.rstrip("/")
    
    def set_template(self, prefer_config_context, overrule_custom):
        self.zbx_template_names = None
        # Gather templates ONLY from the device specific context
        if prefer_config_context:
            try:
                self.zbx_template_names = self.get_templates_context()
            except TemplateError as e:
                logger.warning(e)
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
        # Get Zabbix templates from the device type
        device_type_cfs = self.nb.device_type.custom_fields
        # Check if the ZBX Template CF is present
        if(template_cf in device_type_cfs):
            # Set value to template
            return [device_type_cfs[template_cf]]
        else:
            # Custom field not found, return error
            e = (f"Custom field {template_cf} not "
                f"found for {self.nb.device_type.manufacturer.name}"
                f" - {self.nb.device_type.display}.")
            
            raise TemplateError(e)

    def get_templates_context(self):
        # Get Zabbix templates from the device context
        if("zabbix" not in self.config_context):
            e = ("Key 'zabbix' not found in config "
                    f"context for template host {self.name}")
            raise TemplateError(e)
        if("templates" not in self.config_context["zabbix"]):
            e = ("Key 'zabbix' not found in config "
                    f"context for template host {self.name}")
            raise TemplateError(e)
        return self.config_context["zabbix"]["templates"]

    def isCluster(self):
        """
        Checks if device is part of cluster.
        """
        if(self.nb.virtual_chassis):
            return True
        else:
            return False

    def getClusterMaster(self):
        """
        Returns chassis master ID.
        """
        if(not self.isCluster()):
            e = (f"Unable to proces {self.name} for cluster calculation: "
                 f"not part of a cluster.")
            logger.warning(e)
            raise SyncInventoryError(e)
        elif(not self.nb.virtual_chassis.master):
            e = (f"{self.name} is part of a Netbox virtual chassis which does "
                 "not have a master configured. Skipping for this reason.")
            logger.error(e)
            raise SyncInventoryError(e)
        else:
            return self.nb.virtual_chassis.master.id

    def promoteMasterDevice(self):
        """
        If device is Primary in cluster,
        promote device name to the cluster name.
        Returns True if succesfull, returns False if device is secondary.
        """
        masterid = self.getClusterMaster()
        if(masterid == self.id):
            logger.debug(f"Device {self.name} is primary cluster member. "
                         f"Modifying hostname from {self.name} to " +
                         f"{self.nb.virtual_chassis.name}.")
            self.name = self.nb.virtual_chassis.name

            return True
        else:
            logger.debug(f"Device {self.name} is non-primary cluster member.")
            return False

    def zbxTemplatePrepper(self, templates):
        """
        Returns Zabbix template IDs
        INPUT: list of templates from Zabbix
        OUTPUT: True
        """
        # Check if there are templates defined
        if(not self.zbx_template_names):
            e = (f"No templates found for device {self.name}")
            logger.info(e)
            raise SyncInventoryError()
        # Set variable to empty list
        self.zbx_templates = []
        # Go through all templates definded in Netbox
        for nb_template in self.zbx_template_names:
            template_match = False
            # Go through all templates found in Zabbix
            for zbx_template in templates:
                # If the template names match
                if(zbx_template['name'] == nb_template):
                    # Set match variable to true, add template details
                    # to class variable and return debug log
                    template_match = True
                    self.zbx_templates.append({"templateid": zbx_template['templateid'],
                                               "name": zbx_template['name']}) 
                    e = (f"Found template {zbx_template['name']}"
                        f" for host {self.name}.")
                    logger.debug(e)
            # Return error should the template not be found in Zabbix
            if(not template_match):
                e = (f"Unable to find template {nb_template} "
                    f"for host {self.name} in Zabbix. Skipping host...")
                logger.warning(e)
                raise SyncInventoryError(e)

    def getZabbixGroup(self, groups):
        """
        Returns Zabbix group ID
        INPUT: list of hostgroups
        OUTPUT: True / False
        """
        # Go through all groups
        for group in groups:
            if(group['name'] == self.hostgroup):
                self.group_id = group['groupid']
                e = (f"Found group {group['name']} for host {self.name}.")
                logger.debug(e)
                return True
        else:
            e = (f"Unable to find group '{self.hostgroup}' "
                 f"for host {self.name} in Zabbix.")
            logger.warning(e)
            raise SyncInventoryError(e)

    def cleanup(self):
        """
        Removes device from external resources.
        Resets custom fields in Netbox.
        """
        if(self.zabbix_id):
            try:
                self.zabbix.host.delete(self.zabbix_id)
                self.nb.custom_fields[device_cf] = None
                self.nb.save()
                e = f"Deleted host {self.name} from Zabbix."
                logger.info(e)
                self.create_journal_entry("warning", "Deleted host from Zabbix")
            except ZabbixAPIException as e:
                e = f"Zabbix returned the following error: {str(e)}."
                logger.error(e)
                raise SyncExternalError(e)

    def _zabbixHostnameExists(self):
        """
        Checks if hostname exists in Zabbix.
        """
        host = self.zabbix.host.get(filter={'name': self.name}, output=[])
        if(host):
            return True
        else:
            return False

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
            if(interface.get_context()):
                # If device is SNMP type, add aditional information.
                if(interface.interface["type"] == 2):
                    interface.set_snmp()
            else:
                interface.set_default()
            return [interface.interface]
        except InterfaceConfigError as e:
            e = f"{self.name}: {e}"
            logger.warning(e)
            raise SyncInventoryError(e)

    def setProxy(self, proxy_list):
        # check if Zabbix Proxy has been defined in config context
        if("zabbix" in self.nb.config_context):
            if("proxy" in self.nb.config_context["zabbix"]):
                proxy = self.nb.config_context["zabbix"]["proxy"]
                # Try matching proxy
                for px in proxy_list:
                    if(px["host"] == proxy):
                        self.zbxproxy = px["proxyid"]
                        logger.debug(f"Found proxy {proxy}"
                                     f" for {self.name}.")
                        return True
                else:
                    e = f"{self.name}: Defined proxy {proxy} not found."
                    logger.warning(e)
                    return False

    def createInZabbix(self, groups, templates, proxys,
                       description="Host added by Netbox sync script."):
        """
        Creates Zabbix host object with parameters from Netbox object.
        """
        # Check if hostname is already present in Zabbix
        if(not self._zabbixHostnameExists()):
            # Get group and template ID's for host
            if(not self.getZabbixGroup(groups)):
                raise SyncInventoryError()
            self.zbxTemplatePrepper(templates)
            # Set interface, group and template configuration
            interfaces = self.setInterfaceDetails()
            groups = [{"groupid": self.group_id}]
            # Set Zabbix proxy if defined
            self.setProxy(proxys)
            # Add host to Zabbix
            try:
                host = self.zabbix.host.create(host=self.name,
                                               status=self.zabbix_state,
                                               interfaces=interfaces,
                                               groups=groups,
                                               templates=self.zbx_templates,
                                               proxy_hostid=self.zbxproxy,
                                               description=description)
                self.zabbix_id = host["hostids"][0]
            except ZabbixAPIException as e:
                e = f"Couldn't add {self.name}, Zabbix returned {str(e)}."
                logger.error(e)
                raise SyncExternalError(e)
            # Set Netbox custom field to hostID value.
            self.nb.custom_fields[device_cf] = int(self.zabbix_id)
            self.nb.save()
            msg = f"Created host {self.name} in Zabbix."
            logger.info(msg)
            self.create_journal_entry("success", msg)
        else:
            e = f"Unable to add {self.name} to Zabbix: host already present."
            logger.warning(e)

    def createZabbixHostgroup(self):
        """
        Creates Zabbix host group based on hostgroup format.
        """
        try:
            groupid = self.zabbix.hostgroup.create(name=self.hostgroup)
            e = f"Added hostgroup '{self.hostgroup}'."
            logger.info(e)
            data = {'groupid': groupid["groupids"][0], 'name': self.hostgroup}
            return data
        except ZabbixAPIException as e:
            e = f"Couldn't add hostgroup, Zabbix returned {str(e)}."
            logger.error(e)
            raise SyncExternalError(e)

    def updateZabbixHost(self, **kwargs):
        """
        Updates Zabbix host with given parameters.
        INPUT: Key word arguments for Zabbix host object.
        """
        try:
            self.zabbix.host.update(hostid=self.zabbix_id, **kwargs)
        except ZabbixAPIException as e:
            e = f"Zabbix returned the following error: {str(e)}."
            logger.error(e)
            raise SyncExternalError(e)
        logger.info(f"Updated host {self.name} with data {kwargs}.")
        self.create_journal_entry("info", f"Updated host in Zabbix with latest NB data.")

    def ConsistencyCheck(self, groups, templates, proxys, proxy_power):
        """
        Checks if Zabbix object is still valid with Netbox parameters.
        """
        self.getZabbixGroup(groups)
        self.zbxTemplatePrepper(templates)
        self.setProxy(proxys)
        host = self.zabbix.host.get(filter={'hostid': self.zabbix_id},
                                    selectInterfaces=['type', 'ip',
                                                      'port', 'details',
                                                      'interfaceid'],
                                    selectGroups=["groupid"],
                                    selectParentTemplates=["templateid"])
        if(len(host) > 1):
            e = (f"Got {len(host)} results for Zabbix hosts "
                 f"with ID {self.zabbix_id} - hostname {self.name}.")
            logger.error(e)
            raise SyncInventoryError(e)
        elif(len(host) == 0):
            e = (f"No Zabbix host found for {self.name}. "
                 f"This is likely the result of a deleted Zabbix host "
                 f"without zeroing the ID field in Netbox.")
            logger.error(e)
            raise SyncInventoryError(e)
        else:
            host = host[0]

        if(host["host"] == self.name):
            logger.debug(f"Device {self.name}: hostname in-sync.")
        else:
            logger.warning(f"Device {self.name}: hostname OUT of sync. "
                           f"Received value: {host['host']}")
            self.updateZabbixHost(host=self.name)
        
        # Check if the templates are in-sync
        if(not self.zbx_template_comparer(host["parentTemplates"])):
            logger.warning(f"Device {self.name}: template(s) OUT of sync.")
            # Update Zabbix with NB templates and clear any old / lost templates
            self.updateZabbixHost(templates_clear=host["parentTemplates"], templates=self.zbx_templates)
        else:
            logger.debug(f"Device {self.name}: template(s) in-sync.")

        for group in host["groups"]:
            if(group["groupid"] == self.group_id):
                logger.debug(f"Device {self.name}: hostgroup in-sync.")
                break
        else:
            logger.warning(f"Device {self.name}: hostgroup OUT of sync.")
            self.updateZabbixHost(groups={'groupid': self.group_id})

        if(int(host["status"]) == self.zabbix_state):
            logger.debug(f"Device {self.name}: status in-sync.")
        else:
            logger.warning(f"Device {self.name}: status OUT of sync.")
            self.updateZabbixHost(status=str(self.zabbix_state))

        # Check if a proxy has been defined
        if(self.zbxproxy != "0"):
            # Check if expected proxyID matches with configured proxy
            if(host["proxy_hostid"] == self.zbxproxy):
                logger.debug(f"Device {self.name}: proxy in-sync.")
            else:
                # Proxy diff, update value
                logger.warning(f"Device {self.name}: proxy OUT of sync.")
                self.updateZabbixHost(proxy_hostid=self.zbxproxy)
        else:
            if(not host["proxy_hostid"] == "0"):
                if(proxy_power):
                    # Variable full_proxy_sync has been enabled
                    # delete the proxy link in Zabbix
                    self.updateZabbixHost(proxy_hostid=self.zbxproxy)
                else:
                    # Instead of deleting the proxy config in zabbix and
                    # forcing potential data loss,
                    # an error message is displayed.
                    logger.error(f"Device {self.name} is configured "
                                 f"with proxy in Zabbix but not in Netbox. The"
                                 " -p flag was ommited: no "
                                 "changes have been made.")
        # If only 1 interface has been found
        if(len(host['interfaces']) == 1):
            updates = {}
            # Go through each key / item and check if it matches Zabbix
            for key, item in self.setInterfaceDetails()[0].items():
                # Check if Netbox value is found in Zabbix
                if(key in host["interfaces"][0]):
                    # If SNMP is used, go through nested dict
                    # to compare SNMP parameters
                    if(type(item) == dict and key == "details"):
                        for k, i in item.items():
                            if(k in host["interfaces"][0][key]):
                                # Set update if values don't match
                                if(host["interfaces"][0][key][k] != str(i)):
                                    # If dict has not been created, add it
                                    if(key not in updates):
                                        updates[key] = {}
                                    updates[key][k] = str(i)
                                    # If SNMP version has been changed
                                    # break loop and force full SNMP update
                                    if(k == "version"):
                                        break
                        # Force full SNMP config update
                        # when version has changed.
                        if(key in updates):
                            if("version" in updates[key]):
                                for k, i in item.items():
                                    updates[key][k] = str(i)
                        continue
                    # Set update if values don't match
                    if(host["interfaces"][0][key] != str(item)):
                        updates[key] = item
            if(updates):
                # If interface updates have been found: push to Zabbix
                logger.warning(f"Device {self.name}: Interface OUT of sync.")
                if("type" in updates):
                    # Changing interface type not supported. Raise exception.
                    e = (f"Device {self.name}: changing interface type to "
                         f"{str(updates['type'])} is not supported.")
                    logger.error(e)
                    raise InterfaceConfigError(e)
                # Set interfaceID for Zabbix config
                updates["interfaceid"] = host["interfaces"][0]['interfaceid']
                try:
                    # API call to Zabbix
                    self.zabbix.hostinterface.update(updates)
                    e = f"Solved {self.name} interface conflict."
                    logger.info(e)
                    self.create_journal_entry("info", e)
                except ZabbixAPIException as e:
                    e = f"Zabbix returned the following error: {str(e)}."
                    logger.error(e)
                    raise SyncExternalError(e)
            else:
                # If no updates are found, Zabbix interface is in-sync
                e = f"Device {self.name}: interface in-sync."
                logger.debug(e)
        else:
            e = (f"Device {self.name} has unsupported interface configuration."
                 f" Host has total of {len(host['interfaces'])} interfaces. "
                 "Manual interfention required.")
            logger.error(e)
            SyncInventoryError(e)

    def create_journal_entry(self, severity, message):
        # Send a new Journal entry to Netbox. Usefull for viewing actions
        # in Netbox without having to look in Zabbix or the script log output
        if(self.journal):
            # Check if the severity is valid
            if severity not in ["info", "success", "warning", "danger"]:
                logger.warning(f"Value {severity} not valid for NB journal entries.")
                return False
            journal = {"assigned_object_type": "dcim.device",
                       "assigned_object_id": self.id,
                       "kind": severity,
                       "comments": message
                       }
            try:
                self.nb_journals.create(journal)
                return True
                logger.debug(f"Crated journal entry in NB for host {self.name}")
            except pynetbox.RequestError as e:
                logger.warning("Unable to create journal entry for "
                               f"{self.name}: NB returned {e}")
    
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
                if(nb_tmpl["templateid"] == zbx_tmpl["templateid"]):
                    # Templates match. Remove this template from the Zabbix templates
                    # and add this NB template to the list of successfull templates
                    tmpls_from_zabbix.pop(pos)
                    succesfull_templates.append(nb_tmpl)
                    logger.debug(f"Device {self.name}: template {nb_tmpl['name']} is present in Zabbix.")
                    break
        if(len(succesfull_templates) == len(self.zbx_templates) and
           len(tmpls_from_zabbix) == 0):
            # All of the Netbox templates have been confirmed as successfull
            # and the ZBX template list is empty. This means that
            # all of the templates match.
            return True
        return False




class ZabbixInterface():
    """Class that represents a Zabbix interface."""

    def __init__(self, context, ip):
        self.context = context
        self.ip = ip
        self.skelet = {"main": "1", "useip": "1", "dns": "", "ip": self.ip}
        self.interface = self.skelet

    def get_context(self):
        # check if Netbox custom context has been defined.
        if("zabbix" in self.context):
            zabbix = self.context["zabbix"]
            if("interface_type" in zabbix and "interface_port" in zabbix):
                self.interface["type"] = zabbix["interface_type"]
                self.interface["port"] = zabbix["interface_port"]
                return True
            else:
                return False
        else:
            return False

    def set_snmp(self):
        # Check if interface is type SNMP
        if(self.interface["type"] == 2):
            # Checks if SNMP settings are defined in Netbox
            if("snmp" in self.context["zabbix"]):
                snmp = self.context["zabbix"]["snmp"]
                self.interface["details"] = {}
                # Checks if bulk config has been defined
                if("bulk" in snmp):
                    self.interface["details"]["bulk"] = str(snmp.pop("bulk"))
                else:
                    # Fallback to bulk enabled if not specified
                    self.interface["details"]["bulk"] = "1"
                # SNMP Version config is required in Netbox config context
                if(snmp.get("version")):
                    self.interface["details"]["version"] = str(snmp.pop("version"))
                else:
                    e = "SNMP version option is not defined."
                    raise InterfaceConfigError(e)
                # If version 1 or 2 is used, get community string
                if(self.interface["details"]["version"] in ['1','2']):
                    if("community" in snmp):
                        # Set SNMP community to confix context value
                        community = snmp["community"]
                    else:
                        # Set SNMP community to default
                        community = "{$SNMP_COMMUNITY}"
                    self.interface["details"]["community"] = str(community)
                # If version 3 has been used, get all
                # SNMPv3 Netbox related configs
                elif(self.interface["details"]["version"] == '3'):
                    items = ["securityname", "securitylevel", "authpassphrase",
                             "privpassphrase", "authprotocol", "privprotocol",
                             "contextname"]
                    for key, item in snmp.items():
                        if(key in items):
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
        # Set default config to SNMPv2,port 161 and community macro.
        self.interface = self.skelet
        self.interface["type"] = "2"
        self.interface["port"] = "161"
        self.interface["details"] = {"version": "2",
                                     "community": "{$SNMP_COMMUNITY}",
                                     "bulk": "1"}


if(__name__ == "__main__"):
    parser = argparse.ArgumentParser(
        description='A script to sync Zabbix with Netbox device data.'
    )
    parser.add_argument("-v", "--verbose", help="Turn on debugging.",
                        action="store_true")
    args = parser.parse_args()
    main(args)
