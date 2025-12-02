#!/usr/bin/env python3
# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation

"""NetBox to Zabbix mapper script."""

import argparse
import logging
import ssl
from os import environ, sys

from pynetbox import api
from pynetbox.core.query import RequestError as NBRequestError
from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError, ProcessingError, ZabbixAPI

from pprint import pprint, pformat

from modules.config import load_config
from modules.network_map import ZabbixMap
from modules.exceptions import EnvironmentVarError, SyncError
from modules.logging import get_logger, set_log_levels, setup_logger
from modules.tools import convert_recordset


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
    #netbox_site_groups = convert_recordset((netbox.dcim.site_groups.all()))
    #netbox_regions = convert_recordset(netbox.dcim.regions.all())
    #zabbix_groups = zabbix.hostgroup.get(output=["groupid", "name"])
    netbox_journals = netbox.extras.journal_entries
    netbox_sites = list(netbox.dcim.sites.filter(**config["map_site_filter"]))

    for nb_site in netbox_sites:
        site_devices = []
        network_map = None
        for device in netbox.dcim.devices.filter(site_id=nb_site['id']):
            if (device.custom_fields[config['device_cf']] and
                  device.custom_fields[config['device_cf']] > 0):
                site_devices.append(device)
        if len(site_devices) > 0:
            logger.info("Found %s Zabbix devices in site '%s', starting network mapper.", len(site_devices), nb_site.name)
            network_map = ZabbixMap(
                nb_site,
                site_devices,
                zabbix,
                netbox,
                netbox_journals,
                nb_version,
                config["create_journal"],
                logger,
            )
        
        #for device in site_devices:
        #    pprint(list(netbox.dcim.interfaces.filter(device_id=device.id)))
    zabbix.logout()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to sync NetBox Connections to Zabbix Network Maps."
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
