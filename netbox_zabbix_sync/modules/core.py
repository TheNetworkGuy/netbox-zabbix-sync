"""Core component of the sync process"""

import ssl
from os import environ
from pprint import pformat
from typing import Any

from pynetbox import api as nbapi
from pynetbox.core.query import RequestError as NetBoxRequestError
from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError, ProcessingError, ZabbixAPI

from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.exceptions import JinjaRenderError, SyncError
from netbox_zabbix_sync.modules.logging import get_logger
from netbox_zabbix_sync.modules.settings import DEFAULT_CONFIG
from netbox_zabbix_sync.modules.tools import (
    convert_recordset,
    jinjafy_config_context,
    proxy_prepper,
    verify_hg_format,
)
from netbox_zabbix_sync.modules.virtual_machine import VirtualMachine

logger = get_logger()


class Sync:
    """
    Class that hosts the main sync process.
    This class is used to connect to NetBox and Zabbix and run the sync process.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Docstring for __init__

        :param self: Description
        :param config: Description
        """
        self.netbox = None
        self.zabbix = None
        self.nb_version = None

        default_config = DEFAULT_CONFIG.copy()

        combined_config = {
            **default_config,
            **(config if config else {}),
        }

        self.config: dict[str, Any] = combined_config

    def _combine_filters(self, config_filter, method_filter):
        """
        Combine filters from config and method parameters.
        Method parameters will overwrite config filters if there are overlaps.
        """
        # Check if method filter is provided,
        # if not return config filter directly
        combined_filter = config_filter.copy()
        if method_filter:
            combined_filter.update(method_filter)
        return combined_filter

    def _validate_netbox_token(self, token: str, nb_version: str) -> bool:
        """Validate the format of the NetBox token based on the NetBox version.
        :param token: The NetBox token to validate.
        :param nb_version: The version of NetBox being used.
        :return: True if the token format is valid for the given NetBox version, False otherwise.
        """
        support_token_url = (
            "https://netboxlabs.com/docs/netbox/integrations/rest-api/#v1-and-v2-tokens"  # noqa: S105
        )
        token_prefix = "nbt_"  # noqa: S105
        nb_v2_support_version = "4.5"
        v2_token = bool(token.startswith(token_prefix) and "." in token)
        v2_error_token = bool(token.startswith(token_prefix) and "." not in token)
        # Check if the token is passed without a proper key.token format
        if v2_error_token:
            logger.error(
                "It looks like an invalid v2 token was passed. For more info, see %s",
                support_token_url,
            )
            return False
        # Warning message for Netbox token v1 with Netbox v4.5 and higher
        if not v2_token and nb_version >= nb_v2_support_version:
            logger.warning(
                "Using Netbox v1 token format. "
                "Consider updating to a v2 token. For more info, see %s",
                support_token_url,
            )
        elif v2_token and nb_version < nb_v2_support_version:
            logger.error(
                "Using Netbox v2 token format with Netbox version lower than 4.5. "
                "Revert to v1 token or upgrade Netbox to 4.5 or higher. For more info, see %s",
                support_token_url,
            )
            return False
        elif v2_token and nb_version >= nb_v2_support_version:
            logger.debug("Using NetBox v2 token format.")
        else:
            logger.debug("Using NetBox v1 token format.")
        return True

    def connect(
        self, nb_host, nb_token, zbx_host, zbx_user=None, zbx_pass=None, zbx_token=None
    ):
        """
        Docstring for connect

        :param self: Description
        :param nb_host: Description
        :param nb_token: Description
        :param zbx_host: Description
        :param zbx_user: Description
        :param zbx_pass: Description
        :param zbx_token: Description
        """
        # Initialize Netbox API connection
        netbox = nbapi(nb_host, token=nb_token, threading=True)
        try:
            # Get NetBox version
            nb_version = netbox.version
            # Test API access by attempting to access a basic endpoint
            # This will catch authorization errors early
            netbox.dcim.devices.count()
            logger.debug("NetBox version is %s.", nb_version)
            self.netbox = netbox
            self.nb_version = str(nb_version)
        except RequestsConnectionError:
            logger.error(
                "Unable to connect to NetBox with URL %s. Please check the URL and status of NetBox.",
                nb_host,
            )
            return False
        except NetBoxRequestError as nb_error:
            e = f"NetBox returned the following error: {nb_error}."
            logger.error(e)
            return False
        # Check Netbox API token format based on NetBox version
        if not self._validate_netbox_token(nb_token, self.nb_version):
            return False
        # Set Zabbix API
        if (zbx_pass or zbx_user) and zbx_token:
            e = (
                "Both ZABBIX_PASS, ZABBIX_USER and ZABBIX_TOKEN environment variables are set. "
                "Please choose between token or password based authentication."
            )
            logger.error(e)
            return False
        try:
            ssl_ctx = ssl.create_default_context()

            # If a custom CA bundle is set for pynetbox (requests), also use it for the Zabbix API
            if environ.get("REQUESTS_CA_BUNDLE", None):
                ssl_ctx.load_verify_locations(environ["REQUESTS_CA_BUNDLE"])
            if not zbx_token:
                logger.debug("Using user/password authentication for Zabbix API.")
                self.zabbix = ZabbixAPI(
                    zbx_host, user=zbx_user, password=zbx_pass, ssl_context=ssl_ctx
                )
            else:
                logger.debug("Using token authentication for Zabbix API.")
                self.zabbix = ZabbixAPI(zbx_host, token=zbx_token, ssl_context=ssl_ctx)
            self.zabbix.check_auth()
            logger.debug("Zabbix version is %s.", self.zabbix.version)
        except (APIRequestError, ProcessingError) as zbx_error:
            e = f"Zabbix returned the following error: {zbx_error}."
            logger.error(e)
            return False
        return True

    def logout(self):
        """
        Logout from Zabbix API
        """
        if self.zabbix:
            self.zabbix.logout()
            logger.debug("Logged out from Zabbix API.")
            return True
        return False

    def _render_config_context(self, host, nb_obj):
        """
        Render config context with Jinja2 if enabled.
        Returns False if rendering failed and the host should be skipped.
        """
        if not self.config["render_config_context"]:
            return True
        logger.debug(
            "Host %s: *EXPERIMENTAL* Rendering config context with Jinja2.",
            host.name,
        )
        try:
            rendered_context = jinjafy_config_context(nb_obj)
        except JinjaRenderError as e:
            logger.exception(
                "Host %s: Skipping due to error while rendering config context: %s",
                host.name,
                e,
            )
            logger.debug(
                "Host %s: Source Config Context:\n%s",
                host.name,
                pformat(nb_obj.config_context),
            )
            return False
        if rendered_context and isinstance(rendered_context, dict):
            host.config_context["zabbix"] = rendered_context
        else:
            logger.error(
                "Host %s: Skipping due to unknown issue while rendering config context.",
                host.name,
            )
            return False
        return True

    def _sync_host(self, host, zabbix_groups, zabbix_templates, zabbix_proxy_list):
        """
        Handle the shared sync steps for any Host (device or VM):
        inventory, usermacros, tags, status/cleanup, hostgroup creation,
        and Zabbix create or consistency check.
        """
        host.set_inventory(host.nb)
        host.set_usermacros()
        host.set_tags()

        if host.status in self.config["zabbix_device_removal"]:
            if host.zabbix_id:
                host.cleanup()
                logger.info("Host %s: cleanup complete", host.name)
                return
            logger.info(
                "Host %s: Skipping since this host is not in the active state.",
                host.name,
            )
            return

        if host.status in self.config["zabbix_device_disable"]:
            host.zabbix_state = 1

        if self.config["create_hostgroups"]:
            for group in host.create_zbx_hostgroup(zabbix_groups):
                zabbix_groups.append(group)

        if host.zabbix_id:
            host.consistency_check(
                zabbix_groups,
                zabbix_templates,
                zabbix_proxy_list,
                self.config["full_proxy_sync"],
                self.config["create_hostgroups"],
            )
            return
        host.create_in_zabbix(zabbix_groups, zabbix_templates, zabbix_proxy_list)

    def start(self, device_filter=None, vm_filter=None):
        """
        Run the NetBox to Zabbix sync process.
        """
        if not self.netbox or not self.zabbix:
            logger.error(
                "Not able to start sync: No connection to NetBox or Zabbix API."
            )
            return False

        if not self.nb_version:
            logger.error("NetBox version is not set. Cannot proceed with sync.")
            return False

        device_cfs = []
        vm_cfs = []
        # Create API call to get all custom fields which are on the device objects
        device_cfs = list(
            self.netbox.extras.custom_fields.filter(
                type=["text", "object", "select"], content_types="dcim.device"
            )
        )
        # Check if the provided Hostgroup layout is valid
        verify_hg_format(
            self.config["hostgroup_format"],
            device_cfs=device_cfs,
            hg_type="dev",
            logger=logger,
        )
        if self.config["sync_vms"]:
            vm_cfs = list(
                self.netbox.extras.custom_fields.filter(
                    type=["text", "object", "select"],
                    content_types="virtualization.virtualmachine",
                )
            )
            verify_hg_format(
                self.config["vm_hostgroup_format"],
                vm_cfs=vm_cfs,
                hg_type="vm",
                logger=logger,
            )
        # Set API parameter mapping based on API version
        proxy_name = "host" if str(self.zabbix.version) < "7" else "name"
        # Get all Zabbix and NetBox data
        dev_filter_combined = self._combine_filters(
            self.config["nb_device_filter"], device_filter
        )
        netbox_devices = list(self.netbox.dcim.devices.filter(**dev_filter_combined))
        netbox_vms = []
        if self.config["sync_vms"]:
            vm_filter_combined = self._combine_filters(
                self.config["nb_vm_filter"], vm_filter
            )
            netbox_vms = list(
                self.netbox.virtualization.virtual_machines.filter(**vm_filter_combined)
            )
        netbox_site_groups = convert_recordset(self.netbox.dcim.site_groups.all())
        netbox_regions = convert_recordset(self.netbox.dcim.regions.all())
        netbox_journals = self.netbox.extras.journal_entries
        zabbix_groups = self.zabbix.hostgroup.get(  # type: ignore
            output=["groupid", "name"]
        )
        zabbix_templates = self.zabbix.template.get(  # type: ignore
            output=["templateid", "name"]
        )
        zabbix_proxies = self.zabbix.proxy.get(  # type: ignore
            output=["proxyid", proxy_name]
        )
        # Set empty list for proxy processing Zabbix <= 6
        zabbix_proxygroups = []
        if str(self.zabbix.version) >= "7":
            zabbix_proxygroups = self.zabbix.proxygroup.get(  # type: ignore
                output=["proxy_groupid", "name"]
            )
        # Sanitize proxy data
        if proxy_name == "host":
            for proxy in zabbix_proxies:
                proxy["name"] = proxy.pop("host")
        # Prepare list of all proxy and proxy_groups
        zabbix_proxy_list = proxy_prepper(zabbix_proxies, zabbix_proxygroups)

        for nb_vm in netbox_vms:
            try:
                vm = VirtualMachine(
                    nb_vm,
                    self.zabbix,
                    netbox_journals,
                    self.nb_version,
                    self.config["create_journal"],
                    logger,
                    config=self.config,
                )
                logger.debug("Host %s: Started operations on VM.", vm.name)
                if self.config["extended_site_properties"] and nb_vm.site:
                    logger.debug("Host %s: Extending site information.", vm.name)
                    nb_vm.site.full_details()
                if not self._render_config_context(vm, nb_vm):
                    continue
                logger.debug("Host %s: NetBox data:\n%s", vm.name, pformat(dict(nb_vm)))
                vm.set_vm_template()
                if not vm.zbx_template_names:
                    continue
                vm.set_hostgroup(
                    self.config["vm_hostgroup_format"],
                    netbox_site_groups,
                    netbox_regions,
                )
                if not vm.hostgroups:
                    continue
                self._sync_host(vm, zabbix_groups, zabbix_templates, zabbix_proxy_list)
            except SyncError:
                pass

        for nb_device in netbox_devices:
            try:
                device = PhysicalDevice(
                    nb_device,
                    self.zabbix,
                    netbox_journals,
                    self.nb_version,
                    self.config["create_journal"],
                    logger,
                    config=self.config,
                )
                logger.debug("Host %s: Started operations on device.", device.name)
                if self.config["extended_site_properties"] and nb_device.site:
                    logger.debug("Host %s: Extending site information.", device.name)
                    nb_device.site.full_details()
                if (
                    self.config["extended_virtual_chassis"]
                    and nb_device.virtual_chassis
                ):
                    logger.debug(
                        "Host %s: Extending virtual chassis information.", device.name
                    )
                    nb_device.virtual_chassis.full_details()
                    if "members" in dict(nb_device.virtual_chassis):
                        for member in nb_device.virtual_chassis.members:
                            member.full_details()
                if not self._render_config_context(device, nb_device):
                    continue
                logger.debug(
                    "Host %s: NetBox data:\n%s", device.name, pformat(dict(nb_device))
                )
                device.set_template(
                    self.config["templates_config_context"],
                    self.config["templates_config_context_overrule"],
                )
                if not device.zbx_template_names:
                    continue
                device.set_hostgroup(
                    self.config["hostgroup_format"], netbox_site_groups, netbox_regions
                )
                device.set_ipmi()
                if not device.hostgroups:
                    logger.warning(
                        "Host %s: has no valid hostgroups, Skipping this host...",
                        device.name,
                    )
                    continue
                if device.is_cluster() and self.config["clustering"]:
                    if device.promote_primary_device():
                        logger.info(
                            "Host %s: is part of cluster and primary.", device.name
                        )
                    else:
                        logger.info(
                            "Host %s: Is part of cluster but not primary. Skipping this host...",
                            device.name,
                        )
                        continue
                self._sync_host(
                    device, zabbix_groups, zabbix_templates, zabbix_proxy_list
                )
            except SyncError:
                pass
        return True
