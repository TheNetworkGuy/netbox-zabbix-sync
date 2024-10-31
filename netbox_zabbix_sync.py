#!/usr/bin/env python3
# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation

"""Netbox to Zabbix sync script."""
import logging
import argparse
from os import environ, path, sys
from pynetbox import api
from zabbix_utils import ZabbixAPI, APIRequestError, ProcessingError
from modules.device import PhysicalDevice
from modules.virtual_machine import VirtualMachine
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
        vm_hostgroup_format,
        nb_device_filter,
        sync_vms
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
    allowed_objects = ["location", "role", "manufacturer", "region",
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
    netbox_vms = netbox.virtualization.virtual_machines.all() if sync_vms else []
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
    for nb_vm in netbox_vms:
        try:
            vm = VirtualMachine(nb_vm, zabbix, netbox_journals, nb_version,
                                create_journal, logger)
            logger.debug(f"Host {vm.name}: started operations on VM.")
            vm.set_vm_template()
            # Check if a valid template has been found for this VM.
            if not vm.zbx_template_names:
                continue
            vm.set_hostgroup(vm_hostgroup_format,netbox_site_groups,netbox_regions)
            # Check if a valid hostgroup has been found for this VM.
            if not vm.hostgroup:
                continue
            # Temporary disable inventory sync for VM's
            # vm.set_inventory(nb_vm)

            # Checks if device is in cleanup state
            if vm.status in zabbix_device_removal:
                if vm.zabbix_id:
                    # Delete device from Zabbix
                    # and remove hostID from Netbox.
                    vm.cleanup()
                    logger.info(f"VM {vm.name}: cleanup complete")
                    continue
                # Device has been added to Netbox
                # but is not in Activate state
                logger.info(f"VM {vm.name}: skipping since this VM is "
                            f"not in the active state.")
                continue
            # Check if the VM is in the disabled state
            if vm.status in zabbix_device_disable:
                vm.zabbix_state = 1
            # Check if VM is already in Zabbix
            if vm.zabbix_id:
                vm.ConsistencyCheck(zabbix_groups, zabbix_templates,
                                        zabbix_proxy_list, full_proxy_sync,
                                        create_hostgroups)
                continue
            # Add hostgroup is config is set
            if create_hostgroups:
                # Create new hostgroup. Potentially multiple groups if nested
                hostgroups = vm.createZabbixHostgroup(zabbix_groups)
                # go through all newly created hostgroups
                for group in hostgroups:
                    # Add new hostgroups to zabbix group list
                    zabbix_groups.append(group)
            # Add VM to Zabbix
            vm.createInZabbix(zabbix_groups, zabbix_templates,
                                    zabbix_proxy_list)
        except SyncError:
            pass

    for nb_device in netbox_devices:
        try:
            # Set device instance set data such as hostgroup and template information.
            device = PhysicalDevice(nb_device, zabbix, netbox_journals, nb_version,
                                    create_journal, logger)
            logger.debug(f"Host {device.name}: started operations on device.")
            device.set_template(templates_config_context, templates_config_context_overrule)
            # Check if a valid template has been found for this VM.
            if not device.zbx_template_names:
                continue
            device.set_hostgroup(hostgroup_format,netbox_site_groups,netbox_regions)
            # Check if a valid hostgroup has been found for this VM.
            if not device.hostgroup:
                continue
            device.set_inventory(nb_device)
            # Checks if device is part of cluster.
            # Requires clustering variable
            if device.isCluster() and clustering:
                # Check if device is primary or secondary
                if device.promoteMasterDevice():
                    e = (f"Device {device.name}: is "
                         f"part of cluster and primary.")
                    logger.info(e)
                else:
                    # Device is secondary in cluster.
                    # Don't continue with this device.
                    e = (f"Device {device.name}: is part of cluster "
                         f"but not primary. Skipping this host...")
                    logger.info(e)
                    continue
            # Checks if device is in cleanup state
            if device.status in zabbix_device_removal:
                if device.zabbix_id:
                    # Delete device from Zabbix
                    # and remove hostID from Netbox.
                    device.cleanup()
                    logger.info(f"Device {device.name}: cleanup complete")
                    continue
                # Device has been added to Netbox
                # but is not in Activate state
                logger.info(f"Device {device.name}: skipping since this device is "
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
            if create_hostgroups:
                # Create new hostgroup. Potentially multiple groups if nested
                hostgroups = device.createZabbixHostgroup(zabbix_groups)
                # go through all newly created hostgroups
                for group in hostgroups:
                    # Add new hostgroups to zabbix group list
                    zabbix_groups.append(group)
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
