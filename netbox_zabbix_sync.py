#!/usr/bin/python3
"""Netbox to Zabbix sync script."""

from os import environ, path
import logging
import argparse
from pynetbox import api
from pyzabbix import ZabbixAPI, ZabbixAPIException

# Set logging
log_format = logging.Formatter('%(asctime)s - %(name)s - '
                               '%(levelname)s - %(message)s')
lgout = logging.StreamHandler()
lgout.setFormatter(log_format)
lgout.setLevel(logging.DEBUG)

lgfile = logging.FileHandler(path.join(path.dirname(path.realpath(__file__)), "sync.log"))
lgfile.setFormatter(log_format)
lgfile.setLevel(logging.DEBUG)

logger = logging.getLogger("Netbox-Zabbix-sync")
logger.addHandler(lgout)
logger.addHandler(lgfile)
logger.setLevel(logging.WARNING)

# Set template and device Netbox "custom field" names
template_cf = "zabbix_template"
device_cf = "zabbix_hostid"


def main(arguments):
    """Module that runs the sync process."""
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

    # Set Zabbix API
    try:
        zabbix = ZabbixAPI(zabbix_host)
        zabbix.login(zabbix_user, zabbix_pass)
    except ZabbixAPIException as e:
        e = f"Zabbix returned the following error: {str(e)}."
        logger.error(e)
    # Set Netbox API
    netbox = api(netbox_host, netbox_token, threading=True)
    # Get all Zabbix and Netbox data
    netbox_devices = netbox.dcim.devices.all()
    zabbix_groups = zabbix.hostgroup.get(output=['name'])
    zabbix_templates = zabbix.template.get(output=['name'])
    # Go through all Netbox devices
    for nb_device in netbox_devices:
        try:
            device = NetworkDevice(nb_device, zabbix)
            # Checks if device is part of cluster.
            # Requires the cluster argument.
            if(device.isCluster() and arguments.cluster):
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
            # With -t flag: add Tenant name to hostgroup name.
            if(arguments.tenant):
                if(device.tenant):
                    device.hg_format.insert(1, device.tenant.name)
                    device.setHostgroup()
                    logger.debug(f"Added Tenant {device.tenant.name} to "
                                 f"hostgroup format of {device.name}.")
                else:
                    logger.debug(f"{device.name} is not linked to a tenant. "
                                 f"Using HG format '{device.hostgroup}'.")
            # Checks if device is in cleanup state
            if(device.status != "Active"):
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
            if(device.zabbix_id):
                # Device is already present in Zabbix
                device.ConsistencyCheck(zabbix_groups, zabbix_templates)
            else:
                # Add hostgroup is flag is true
                # and Hostgroup is not present in Zabbix
                if(arguments.hostgroups):
                    for group in zabbix_groups:
                        if(group["name"] == device.hostgroup):
                            # If hostgroup is already present in Zabbix
                            break
                    else:
                        # Create new hostgroup
                        hostgroup = device.createZabbixHostgroup()
                        zabbix_groups.append(hostgroup)
                # Add device to Zabbix
                device.createInZabbix(zabbix_groups, zabbix_templates)
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


class NetworkDevice():

    """
    Represents Network device.
    INPUT: (Netbox device class, ZabbixAPI class)
    """

    def __init__(self, nb, zabbix):
        self.nb = nb
        self.id = nb.id
        self.name = nb.name
        self.status = nb.status.label
        self.zabbix = zabbix
        self.tenant = nb.tenant
        self.hostgroup = None
        self.hg_format = [self.nb.site.name,
                          self.nb.device_type.manufacturer.name,
                          self.nb.device_role.name]
        self._setBasics()
        self.setHostgroup()

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

        # Check if device_type has custom field
        device_type_cf = self.nb.device_type.custom_fields
        if(template_cf in device_type_cf):
            self.template_name = device_type_cf[template_cf]
        else:
            e = (f"Custom field {template_cf} not "
                 f"found for {self.nb.device_type.name}.")
            logger.warning(e)
            raise SyncInventoryError(e)

        # Check if device has custom field
        if(device_cf in self.nb.custom_fields):
            self.zabbix_id = self.nb.custom_fields[device_cf]
        else:
            e = f"Custom field {template_cf} not found for {self.name}."
            logger.warning(e)
            raise SyncInventoryError(e)

    def setHostgroup(self):
        """Sets hostgroup to a string with hg_format parameters."""
        self.hostgroup = "/".join(self.hg_format)

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

    def getZabbixTemplate(self, templates):
        """
        Returns Zabbix template ID
        INPUT: list of templates
        OUTPUT: True
        """
        if(not self.template_name):
            e = (f"Device template '{self.nb.device_type.display_name}' "
                 "has no Zabbix template defined.")
            logger.info(e)
            raise SyncInventoryError()
        for template in templates:
            if(template['name'] == self.template_name):
                self.template_id = template['templateid']
                e = (f"Found template ID {str(template['templateid'])} "
                     f"for host {self.name}.")
                logger.debug(e)
                return True
        else:
            e = (f"Unable to find template {self.template_name} "
                 f"for host {self.name} in Zabbix.")
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
                if(interface.type == 2):
                    interface.set_snmp()
            else:
                interface.set_default()
            return [interface.interface]
        except InterfaceConfigError as e:
            e = f"{self.name}: {e}"
            logger.warning(e)
            raise SyncInventoryError(e)

    def createInZabbix(self, groups, templates,
                       description="Host added by Netbox sync script."):
        """
        Creates Zabbix host object with parameters from Netbox object.
        """
        # Check if hostname is already present in Zabbix
        if(not self._zabbixHostnameExists()):
            # Get group and template ID's for host
            if(not self.getZabbixGroup(groups)):
                raise SyncInventoryError()
            self.getZabbixTemplate(templates)
            # Set interface, group and template configuration
            interfaces = self.setInterfaceDetails()
            groups = [{"groupid": self.group_id}]
            templates = [{"templateid": self.template_id}]
            # Add host to Zabbix
            try:
                host = self.zabbix.host.create(host=self.name, status=0,
                                               interfaces=interfaces,
                                               groups=groups,
                                               templates=templates,
                                               description=description)
                self.zabbix_id = host["hostids"][0]
            except ZabbixAPIException as e:
                e = f"Couldn't add {self.name}, Zabbix returned {str(e)}."
                logger.error(e)
                raise SyncExternalError(e)
            # Set Netbox custom field to hostID value.
            self.nb.custom_fields[device_cf] = self.zabbix_id
            self.nb.save()
            logger.info(f"Created host {self.name} in Zabbix.")
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

    def ConsistencyCheck(self, groups, templates):
        """
        Checks if Zabbix object is still valid with Netbox parameters.
        """
        self.getZabbixGroup(groups)
        self.getZabbixTemplate(templates)
        host = self.zabbix.host.get(filter={'hostid': self.zabbix_id},
                                    selectInterfaces=['type', 'ip',
                                                      'port', 'details',
                                                      'interfaceid'],
                                    selectGroups=["id"],
                                    selectParentTemplates=["id"])
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

        for template in host["parentTemplates"]:
            if(template["templateid"] == self.template_id):
                logger.debug(f"Device {self.name}: template in-sync.")
                break
        else:
            logger.warning(f"Device {self.name}: template OUT of sync.")
            self.updateZabbixHost(templates=self.template_id)

        for group in host["groups"]:
            if(group["groupid"] == self.group_id):
                logger.debug(f"Device {self.name}: hostgroup in-sync.")
                break
        else:
            logger.warning(f"Device {self.name}: hostgroup OUT of sync.")
            self.updateZabbixHost(groups={'groupid': self.group_id})

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
                logger.debug(f"{self.name}: Updating interface with "
                             f"config {updates}")
                try:
                    # API call to Zabbix
                    self.zabbix.hostinterface.update(updates)
                    e = f"Solved {self.name} interface conflict."
                    logger.info(e)
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


class ZabbixInterface():
    def __init__(self, context, ip):
        self.context = context
        self.type = None
        self.ip = ip
        self.skelet = {"main": "1", "useip": "1", "dns": "", "ip": self.ip}
        self.interface = self.skelet

    def get_context(self):
        # check if Netbox custom context has been defined.
        if("zabbix" in self.context):
            try:
                zabbix = self.context["zabbix"]
                self.interface["type"] = zabbix["interface_type"]
                self.interface["port"] = zabbix["interface_port"]
                self.type = zabbix["interface_type"]
            except KeyError:
                e = ("Interface port or type is not defined under "
                     "config context 'zabbix'.")
                raise InterfaceConfigError(e)
            return True
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
                if(snmp.get("bulk")):
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
                # If version 2 is used, get community string
                if(self.interface["details"]["version"] == '2'):
                    if("community" in snmp):
                        community = snmp["community"]
                        self.interface["details"]["community"] = str(community)
                    else:
                        e = ("No SNMP community string "
                             "defined in custom context.")
                        raise InterfaceConfigError(e)
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
    # Arguments parsing
    parser = argparse.ArgumentParser(
        description='A script to sync Zabbix with Netbox device data.'
    )
    parser.add_argument("-v", "--verbose", help="Turn on debugging.",
                        action="store_true")
    parser.add_argument("-c", "--cluster", action="store_true",
                        help=("Only add the primary node of a cluster "
                              "to Zabbix. Usefull when a shared virtual IP is "
                              "used for the control plane."))
    parser.add_argument("-H", "--hostgroups",
                        help="Create Zabbix hostgroups if not present",
                        action="store_true")
    parser.add_argument("-t", "--tenant", action="store_true",
                        help=("Add Tenant name to the Zabbix "
                              "hostgroup name scheme."))
    args = parser.parse_args()

    main(args)
