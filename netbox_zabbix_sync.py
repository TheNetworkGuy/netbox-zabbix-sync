#!/usr/bin/env python3
# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation

"""Netbox to Zabbix sync script."""
import logging
import argparse
from os import environ, path, sys
from pynetbox import api
from zabbix_utils import ZabbixAPI, APIRequestError, ProcessingError
from modules.device import NetworkDevice
from modules.tools import convert_recordset, proxy_prepper
from modules.exceptions import EnvironmentVarError, HostgroupError, SyncError
try:
    from config import (
        templates_config_context,
        templates_config_context_overrule,
        clustering, create_hostgroups,
        create_journal, full_proxy_sync,
        zabbix_device_removal,
        zabbix_device_disable,
        hostgroup_format,
        nb_device_filter
    )
except ModuleNotFoundError:
    print("Configuration file config.py not found in main directory."
           "Please create the file or rename the config.py.example file to config.py.")
    sys.exit(1)

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
    # pylint: disable=too-many-branches, too-many-statements
    # set environment variables
    if arguments.verbose:
        logger.setLevel(logging.DEBUG)
    env_vars = ["ZABBIX_HOST", "NETBOX_HOST", "NETBOX_TOKEN"]
    if "ZABBIX_TOKEN" in environ:
        env_vars.append("ZABBIX_TOKEN")
    else:
        env_vars.append("ZABBIX_USER")
        env_vars.append("ZABBIX_PASS")
    for var in env_vars:
        if var not in environ:
            e = f"Environment variable {var} has not been defined."
            logger.error(e)
            raise EnvironmentVarError(e)
    # Get all virtual environment variables
    if "ZABBIX_TOKEN" in env_vars:
        zabbix_user = None
        zabbix_pass = None
        zabbix_token = environ.get("ZABBIX_TOKEN")
    else:
        zabbix_user = environ.get("ZABBIX_USER")
        zabbix_pass = environ.get("ZABBIX_PASS")
        zabbix_token = None
    zabbix_host = environ.get("ZABBIX_HOST")
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
    for hg_object in hg_objects:
        if hg_object not in allowed_objects:
            e = (f"Hostgroup item {hg_object} is not valid. Make sure you"
                    " use valid items and seperate them with '/'.")
            logger.error(e)
            raise HostgroupError(e)
    # Set Zabbix API
    try:
        if not zabbix_token:
            zabbix = ZabbixAPI(zabbix_host, user=zabbix_user, password=zabbix_pass)
        else:
            zabbix = ZabbixAPI(zabbix_host, token=zabbix_token)
        zabbix.check_auth()
    except (APIRequestError, ProcessingError)  as e:
        e = f"Zabbix returned the following error: {str(e)}"
        logger.error(e)
        sys.exit(1)
    # Set API parameter mapping based on API version
    if not str(zabbix.version).startswith('7'):
        proxy_name = "host"
    else:
        proxy_name = "name"
    # Get all Zabbix and Netbox data
    netbox_devices = netbox.dcim.devices.filter(**nb_device_filter)
    netbox_site_groups = convert_recordset((netbox.dcim.site_groups.all()))
    netbox_regions = convert_recordset(netbox.dcim.regions.all())
    netbox_journals = netbox.extras.journal_entries
    zabbix_groups = zabbix.hostgroup.get(output=['groupid', 'name'])
    zabbix_templates = zabbix.template.get(output=['templateid', 'name'])
    zabbix_proxies = zabbix.proxy.get(output=['proxyid', proxy_name])
    # Set empty list for proxy processing Zabbix <= 6
    zabbix_proxygroups = []
    if str(zabbix.version).startswith('7'):
        zabbix_proxygroups = zabbix.proxygroup.get(output=["proxy_groupid", "name"])
    # Sanitize proxy data
    if proxy_name == "host":
        for proxy in zabbix_proxies:
            proxy['name'] = proxy.pop('host')
    # Prepare list of all proxy and proxy_groups
    zabbix_proxy_list = proxy_prepper(zabbix_proxies, zabbix_proxygroups)

    # Get Netbox API version
    nb_version = netbox.version

    # Go through all Netbox devices
    for nb_device in netbox_devices:
        try:
            # Set device instance set data such as hostgroup and template information.
            device = NetworkDevice(nb_device, zabbix, netbox_journals, nb_version,
                                   create_journal, logger)
            device.set_hostgroup(hostgroup_format,netbox_site_groups,netbox_regions)
            device.set_template(templates_config_context, templates_config_context_overrule)
            device.set_inventory(nb_device)
            # Checks if device is part of cluster.
            # Requires clustering variable
            if device.isCluster() and clustering:
                # Check if device is primary or secondary
                if device.promoteMasterDevice():
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
            if device.status in zabbix_device_removal:
                if device.zabbix_id:
                    # Delete device from Zabbix
                    # and remove hostID from Netbox.
                    device.cleanup()
                    logger.info(f"Cleaned up host {device.name}.")
                    continue
                # Device has been added to Netbox
                # but is not in Activate state
                logger.info(f"Skipping host {device.name} since its "
                            f"not in the active state.")
                continue
            # Check if the device is in the disabled state
            if device.status in zabbix_device_disable:
                device.zabbix_state = 1
            # Check if device is already in Zabbix
            if device.zabbix_id:
                device.ConsistencyCheck(zabbix_groups, zabbix_templates,
                                        zabbix_proxy_list, full_proxy_sync,
                                        create_hostgroups)
                continue
            # Add hostgroup is config is set
            # and Hostgroup is not present in Zabbix
            if create_hostgroups:
                for group in zabbix_groups:
                    # If hostgroup is already present in Zabbix
                    if group["name"] == device.hostgroup:
                        break
                else:
                    # Create new hostgroup
                    hostgroup = device.createZabbixHostgroup()
                    zabbix_groups.append(hostgroup)
            # Add device to Zabbix
            device.createInZabbix(zabbix_groups, zabbix_templates,
                                    zabbix_proxy_list)
        except SyncError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='A script to sync Zabbix with Netbox device data.'
    )
    parser.add_argument("-v", "--verbose", help="Turn on debugging.",
                        action="store_true")
    args = parser.parse_args()
    main(args)
