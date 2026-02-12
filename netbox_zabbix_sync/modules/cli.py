import argparse
import logging
from os import environ

from netbox_zabbix_sync.modules.core import run_sync
from netbox_zabbix_sync.modules.exceptions import EnvironmentVarError
from netbox_zabbix_sync.modules.logging import get_logger, set_log_levels

logger = get_logger()


def main(arguments):
    """Run the sync process."""
    # set environment variables
    if arguments.verbose:
        set_log_levels(logging.WARNING, logging.INFO)
    if arguments.debug:
        set_log_levels(logging.WARNING, logging.DEBUG)
    if arguments.debug_all:
        set_log_levels(logging.DEBUG, logging.DEBUG)
    if arguments.quiet:
        set_log_levels(logging.ERROR, logging.ERROR)

    # Gather environment variables for Zabbix and Netbox communication
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

    # Run main sync process
    run_sync(
        nb_host=netbox_host,
        nb_token=netbox_token,
        zbx_host=zabbix_host,
        zbx_user=zabbix_user,
        zbx_pass=zabbix_pass,
        zbx_token=zabbix_token,
    )


def parse_cli():
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
