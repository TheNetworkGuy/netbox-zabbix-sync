#!/usr/bin/env python3
# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation

"""NetBox to Zabbix sync script."""

import argparse
import logging
import ssl
from os import environ, sys

from pynetbox import api
from pynetbox.core.query import RequestError as NBRequestError
from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError, ProcessingError, ZabbixAPI
from modules.config import load_config
from modules.device import PhysicalDevice
from modules.exceptions import EnvironmentVarError, SyncError
from modules.logging import get_logger, set_log_levels, setup_logger
from modules.tools import convert_recordset, proxy_prepper, verify_hg_format
from modules.virtual_machine import VirtualMachine

config = load_config()


setup_logger()
logger = get_logger()


def main(arguments):
    """Run the sync process."""
    # pylint: disable=too-many-branches, too-many-statements
    # set environment variables
    if arguments.verbose:
        set_log_levels(logging.WARNING, logging.INFO)
    if arguments.debug:
        set_log_levels(logging.WARNING, logging.DEBUG)
    if arguments.debug_all:
        set_log_levels(logging.DEBUG, logging.DEBUG)
    if arguments.quiet:
        set_log_levels(logging.ERROR, logging.ERROR)

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
    # Set NetBox API
    netbox = api(netbox_host, token=netbox_token, threading=True)
    # Create API call to get all custom fields which are on the device objects
    try:
        # Get NetBox version
        nb_version = netbox.version
        logger.debug("NetBox version is %s.", nb_version)
    except RequestsConnectionError:
        logger.error(
            "Unable to connect to NetBox with URL %s. Please check the URL and status of NetBox.",
            netbox_host,
        )
        sys.exit(1)
    except NBRequestError as e:
        logger.error("NetBox error: %s", e)
        sys.exit(1)
    # Check if the provided Hostgroup layout is valid
    device_cfs = []
    vm_cfs = []
    device_cfs = list(
        netbox.extras.custom_fields.filter(type="text", content_types="dcim.device")
    )
    verify_hg_format(
        config["hostgroup_format"], device_cfs=device_cfs, hg_type="dev", logger=logger
    )
    if config["sync_vms"]:
        vm_cfs = list(
            netbox.extras.custom_fields.filter(
                type="text", content_types="virtualization.virtualmachine"
            )
        )
        verify_hg_format(
            config["vm_hostgroup_format"], vm_cfs=vm_cfs, hg_type="vm", logger=logger
        )
    # Set Zabbix API
    try:
        ssl_ctx = ssl.create_default_context()

        # If a custom CA bundle is set for pynetbox (requests), also use it for the Zabbix API
        if environ.get("REQUESTS_CA_BUNDLE", None):
            ssl_ctx.load_verify_locations(environ["REQUESTS_CA_BUNDLE"])

        if not zabbix_token:
            zabbix = ZabbixAPI(
                zabbix_host, user=zabbix_user, password=zabbix_pass, ssl_context=ssl_ctx
            )
        else:
            zabbix = ZabbixAPI(zabbix_host, token=zabbix_token, ssl_context=ssl_ctx)
        zabbix.check_auth()
    except (APIRequestError, ProcessingError) as e:
        e = f"Zabbix returned the following error: {str(e)}"
        logger.error(e)
        sys.exit(1)
    # Set API parameter mapping based on API version
    if not str(zabbix.version).startswith("7"):
        proxy_name = "host"
    else:
        proxy_name = "name"
    # Get all Zabbix and NetBox data
    netbox_devices = list(netbox.dcim.devices.filter(**config["nb_device_filter"]))
    netbox_vms = []
    if config["sync_vms"]:
        netbox_vms = list(
            netbox.virtualization.virtual_machines.filter(**config["nb_vm_filter"])
        )
    netbox_site_groups = convert_recordset((netbox.dcim.site_groups.all()))
    netbox_regions = convert_recordset(netbox.dcim.regions.all())
    netbox_journals = netbox.extras.journal_entries
    zabbix_groups = zabbix.hostgroup.get(output=["groupid", "name"])
    zabbix_templates = zabbix.template.get(output=["templateid", "name"])
    zabbix_proxies = zabbix.proxy.get(output=["proxyid", proxy_name])
    # Set empty list for proxy processing Zabbix <= 6
    zabbix_proxygroups = []
    if str(zabbix.version).startswith("7"):
        zabbix_proxygroups = zabbix.proxygroup.get(output=["proxy_groupid", "name"])
    # Sanitize proxy data
    if proxy_name == "host":
        for proxy in zabbix_proxies:
            proxy["name"] = proxy.pop("host")
    # Prepare list of all proxy and proxy_groups
    zabbix_proxy_list = proxy_prepper(zabbix_proxies, zabbix_proxygroups)

    # Go through all NetBox devices
    for nb_vm in netbox_vms:
        try:
            vm = VirtualMachine(
                nb_vm,
                zabbix,
                netbox_journals,
                nb_version,
                config["create_journal"],
                logger,
            )
            logger.debug("Host %s: Started operations on VM.", vm.name)
            vm.set_vm_template()
            # Check if a valid template has been found for this VM.
            if not vm.zbx_template_names:
                continue
            vm.set_hostgroup(
                config["vm_hostgroup_format"], netbox_site_groups, netbox_regions
            )
            # Check if a valid hostgroup has been found for this VM.
            if not vm.hostgroups:
                continue
            vm.set_inventory(nb_vm)
            vm.set_usermacros()
            vm.set_tags()
            # Checks if device is in cleanup state
            if vm.status in config["zabbix_device_removal"]:
                if vm.zabbix_id:
                    # Delete device from Zabbix
                    # and remove hostID from NetBox.
                    vm.cleanup()
                    logger.info("VM %s: cleanup complete", vm.name)
                    continue
                # Device has been added to NetBox
                # but is not in Activate state
                logger.info(
                    "VM %s: Skipping since this VM is not in the active state.", vm.name
                )
                continue
            # Check if the VM is in the disabled state
            if vm.status in config["zabbix_device_disable"]:
                vm.zabbix_state = 1
            # Add hostgroup if config is set
            if config["create_hostgroups"]:
                # Create new hostgroup. Potentially multiple groups if nested
                hostgroups = vm.createZabbixHostgroup(zabbix_groups)
                # go through all newly created hostgroups
                for group in hostgroups:
                    # Add new hostgroups to zabbix group list
                    zabbix_groups.append(group)
            # Check if VM is already in Zabbix
            if vm.zabbix_id:
                vm.ConsistencyCheck(
                    zabbix_groups,
                    zabbix_templates,
                    zabbix_proxy_list,
                    config["full_proxy_sync"],
                    config["create_hostgroups"],
                )
                continue
            # Add VM to Zabbix
            vm.createInZabbix(zabbix_groups, zabbix_templates, zabbix_proxy_list)
        except SyncError:
            pass

    for nb_device in netbox_devices:
        try:
            # Set device instance set data such as hostgroup and template information.
            device = PhysicalDevice(
                nb_device,
                zabbix,
                netbox_journals,
                nb_version,
                config["create_journal"],
                logger,
            )
            logger.debug("Host %s: Started operations on device.", device.name)
            device.set_template(
                config["templates_config_context"],
                config["templates_config_context_overrule"],
            )
            # Check if a valid template has been found for this VM.
            if not device.zbx_template_names:
                continue
            device.set_hostgroup(
                config["hostgroup_format"], netbox_site_groups, netbox_regions
            )
            # Check if a valid hostgroup has been found for this VM.
            if not device.hostgroups:
                logger.warning(
                    "Host %s: Host has no valid hostgroups, Skipping this host...",
                    device.name,
                )
                continue
            device.set_inventory(nb_device)
            device.set_usermacros()
            device.set_tags()
            # Checks if device is part of cluster.
            # Requires clustering variable
            if device.isCluster() and config["clustering"]:
                # Check if device is primary or secondary
                if device.promoteMasterDevice():
                    logger.info(
                        "Device %s: is part of cluster and primary.", device.name
                    )
                else:
                    # Device is secondary in cluster.
                    # Don't continue with this device.
                    logger.info(
                        "Device %s: Is part of cluster but not primary. Skipping this host...",
                        device.name,
                    )
                    continue
            # Checks if device is in cleanup state
            if device.status in config["zabbix_device_removal"]:
                if device.zabbix_id:
                    # Delete device from Zabbix
                    # and remove hostID from NetBox.
                    device.cleanup()
                    logger.info("Device %s: cleanup complete", device.name)
                    continue
                # Device has been added to NetBox
                # but is not in Activate state
                logger.info(
                    "Device %s: Skipping since this device is not in the active state.",
                    device.name,
                )
                continue
            # Check if the device is in the disabled state
            if device.status in config["zabbix_device_disable"]:
                device.zabbix_state = 1
            # Add hostgroup is config is set
            if config["create_hostgroups"]:
                # Create new hostgroup. Potentially multiple groups if nested
                hostgroups = device.createZabbixHostgroup(zabbix_groups)
                # go through all newly created hostgroups
                for group in hostgroups:
                    # Add new hostgroups to zabbix group list
                    zabbix_groups.append(group)
            # Check if device is already in Zabbix
            if device.zabbix_id:
                device.ConsistencyCheck(
                    zabbix_groups,
                    zabbix_templates,
                    zabbix_proxy_list,
                    config["full_proxy_sync"],
                    config["create_hostgroups"],
                )
                continue
            # Add device to Zabbix
            device.createInZabbix(zabbix_groups, zabbix_templates, zabbix_proxy_list)
        except SyncError:
            pass
    zabbix.logout()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to sync Zabbix with NetBox device data."
    )
    parser.add_argument(
        "-v", "--verbose", help="Turn on verbose logging.", action="store_true"
    )
    parser.add_argument(
        "-vv", "--debug", help="Turn on debugging.", action="store_true"
    )
    parser.add_argument(
        "-vvv",
        "--debug-all",
        help="Turn on debugging for all modules.",
        action="store_true",
    )
    parser.add_argument("-q", "--quiet", help="Turn off warnings.", action="store_true")
    args = parser.parse_args()
    main(args)
