"""Core component of the sync process"""

import ssl
import sys
from os import environ

from pynetbox import api
from pynetbox.core.query import RequestError as NBRequestError
from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError, ProcessingError, ZabbixAPI

from netbox_zabbix_sync.modules.config import load_config
from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.exceptions import SyncError
from netbox_zabbix_sync.modules.logging import get_logger, setup_logger
from netbox_zabbix_sync.modules.tools import (
    convert_recordset,
    proxy_prepper,
    verify_hg_format,
)
from netbox_zabbix_sync.modules.virtual_machine import VirtualMachine

# Import configuration settings
config = load_config()


setup_logger()
logger = get_logger()


def run_sync(nb_host, nb_token, zbx_host, zbx_user, zbx_pass, zbx_token):
    """
    Run the NetBox to Zabbix sync process.
    """
    # Set NetBox API
    netbox = api(nb_host, token=nb_token, threading=True)
    # Create API call to get all custom fields which are on the device objects
    try:
        # Get NetBox version
        nb_version = netbox.version
        logger.debug("NetBox version is %s.", nb_version)
    except RequestsConnectionError:
        logger.error(
            "Unable to connect to NetBox with URL %s. Please check the URL and status of NetBox.",
            nb_host,
        )
        sys.exit(1)
    except NBRequestError as e:
        logger.error("NetBox error: %s", e)
        sys.exit(1)
    # Check if the provided Hostgroup layout is valid
    device_cfs = []
    vm_cfs = []
    device_cfs = list(
        netbox.extras.custom_fields.filter(
            type=["text", "object", "select"], content_types="dcim.device"
        )
    )
    verify_hg_format(
        config["hostgroup_format"], device_cfs=device_cfs, hg_type="dev", logger=logger
    )
    if config["sync_vms"]:
        vm_cfs = list(
            netbox.extras.custom_fields.filter(
                type=["text", "object", "select"],
                content_types="virtualization.virtualmachine",
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

        if not zbx_token:
            zabbix = ZabbixAPI(
                zbx_host, user=zbx_user, password=zbx_pass, ssl_context=ssl_ctx
            )
        else:
            zabbix = ZabbixAPI(zbx_host, token=zbx_token, ssl_context=ssl_ctx)
        zabbix.check_auth()
    except (APIRequestError, ProcessingError) as zbx_error:
        e = f"Zabbix returned the following error: {zbx_error}."
        logger.error(e)
        sys.exit(1)
    # Set API parameter mapping based on API version
    proxy_name = "host" if not str(zabbix.version).startswith("7") else "name"
    # Get all Zabbix and NetBox data
    netbox_devices = list(netbox.dcim.devices.filter(**config["nb_device_filter"]))
    netbox_vms = []
    if config["sync_vms"]:
        netbox_vms = list(
            netbox.virtualization.virtual_machines.filter(**config["nb_vm_filter"])
        )
    netbox_site_groups = convert_recordset(netbox.dcim.site_groups.all())
    netbox_regions = convert_recordset(netbox.dcim.regions.all())
    netbox_journals = netbox.extras.journal_entries
    zabbix_groups = zabbix.hostgroup.get(output=["groupid", "name"])  # type: ignore[attr-defined]
    zabbix_templates = zabbix.template.get(output=["templateid", "name"])  # type: ignore[attr-defined]
    zabbix_proxies = zabbix.proxy.get(output=["proxyid", proxy_name])  # type: ignore[attr-defined]
    # Set empty list for proxy processing Zabbix <= 6
    zabbix_proxygroups = []
    if str(zabbix.version).startswith("7"):
        zabbix_proxygroups = zabbix.proxygroup.get(output=["proxy_groupid", "name"])  # type: ignore[attr-defined]
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
            if config["extended_site_properties"] and nb_vm.site:
                logger.debug("VM %s: extending site information.", vm.name)
                vm.site = convert_recordset(netbox.dcim.sites.filter(id=nb_vm.site.id))  # type: ignore[attr-defined]
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
                hostgroups = vm.create_zbx_hostgroup(zabbix_groups)
                # go through all newly created hostgroups
                for group in hostgroups:
                    # Add new hostgroups to zabbix group list
                    zabbix_groups.append(group)
            # Check if VM is already in Zabbix
            if vm.zabbix_id:
                vm.consistency_check(
                    zabbix_groups,
                    zabbix_templates,
                    zabbix_proxy_list,
                    config["full_proxy_sync"],
                    config["create_hostgroups"],
                )
                continue
            # Add VM to Zabbix
            vm.create_in_zabbix(zabbix_groups, zabbix_templates, zabbix_proxy_list)
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
            if config["extended_site_properties"] and nb_device.site:
                logger.debug("Device %s: extending site information.", device.name)
                device.site = convert_recordset(  # type: ignore[attr-defined]
                    netbox.dcim.sites.filter(id=nb_device.site.id)
                )
            device.set_inventory(nb_device)
            device.set_usermacros()
            device.set_tags()
            # Checks if device is part of cluster.
            # Requires clustering variable
            if device.is_cluster() and config["clustering"]:
                # Check if device is primary or secondary
                if device.promote_primary_device():
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
                hostgroups = device.create_zbx_hostgroup(zabbix_groups)
                # go through all newly created hostgroups
                for group in hostgroups:
                    # Add new hostgroups to zabbix group list
                    zabbix_groups.append(group)
            # Check if device is already in Zabbix
            if device.zabbix_id:
                device.consistency_check(
                    zabbix_groups,
                    zabbix_templates,
                    zabbix_proxy_list,
                    config["full_proxy_sync"],
                    config["create_hostgroups"],
                )
                continue
            # Add device to Zabbix
            device.create_in_zabbix(zabbix_groups, zabbix_templates, zabbix_proxy_list)
        except SyncError:
            pass
    zabbix.logout()
