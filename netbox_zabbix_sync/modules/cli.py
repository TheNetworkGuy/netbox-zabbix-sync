import argparse
import logging
from os import environ

from netbox_zabbix_sync.modules.core import Sync
from netbox_zabbix_sync.modules.exceptions import EnvironmentVarError
from netbox_zabbix_sync.modules.logging import get_logger, set_log_levels, setup_logger
from netbox_zabbix_sync.modules.settings import load_config

# Boolean settings that can be toggled via --flag / --no-flag
_BOOL_ARGS = [
    ("clustering", "Enable clustering of devices with virtual chassis setup."),
    ("create_hostgroups", "Enable hostgroup generation (requires Zabbix permissions)."),
    ("create_journal", "Create NetBox journal entries on changes."),
    ("sync_vms", "Enable virtual machine sync."),
    (
        "full_proxy_sync",
        "Enable full proxy sync (removes proxies not in config context).",
    ),
    (
        "templates_config_context",
        "Use config context as the template source instead of a custom field.",
    ),
    (
        "templates_config_context_overrule",
        "Give config context templates higher priority than custom field templates.",
    ),
    ("traverse_regions", "Use the full parent-region path in hostgroup names."),
    ("traverse_site_groups", "Use the full parent-site-group path in hostgroup names."),
    (
        "extended_site_properties",
        "Fetch additional site info from NetBox (increases API queries).",
    ),
    ("inventory_sync", "Sync NetBox device properties to Zabbix inventory."),
    ("usermacro_sync", "Sync usermacros from NetBox to Zabbix."),
    ("tag_sync", "Sync host tags to Zabbix."),
    ("tag_lower", "Lowercase tag names and values before syncing."),
]

# String settings that can be set via --option VALUE
_STR_ARGS = [
    ("template_cf", "NetBox custom field name for the Zabbix template.", "FIELD"),
    ("device_cf", "NetBox custom field name for the Zabbix host ID.", "FIELD"),
    (
        "hostgroup_format",
        "Hostgroup path pattern for physical devices (e.g. site/manufacturer/role).",
        "PATTERN",
    ),
    (
        "vm_hostgroup_format",
        "Hostgroup path pattern for virtual machines (e.g. cluster_type/cluster/role).",
        "PATTERN",
    ),
    (
        "inventory_mode",
        "Zabbix inventory mode: disabled, manual, or automatic.",
        "MODE",
    ),
    ("tag_name", "Zabbix tag name used when syncing NetBox tags.", "NAME"),
    (
        "tag_value",
        "NetBox tag property to use as the Zabbix tag value (name, slug, or display).",
        "PROPERTY",
    ),
]


def _apply_cli_overrides(config: dict, arguments: argparse.Namespace) -> dict:
    """Override loaded config with any values explicitly provided on the CLI."""
    for key, _help in _BOOL_ARGS:
        cli_val = getattr(arguments, key, None)
        if cli_val is not None:
            config[key] = cli_val
    for key, _help, _meta in _STR_ARGS:
        cli_val = getattr(arguments, key, None)
        if cli_val is not None:
            config[key] = cli_val
    return config


def main(arguments):
    """Run the sync process."""
    # Set logging
    setup_logger()
    logger = get_logger()
    # Set log levels based on verbosity flags
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

    # Load config (defaults → config.py → env vars), then apply CLI overrides
    config = load_config(config_file=arguments.config)
    config = _apply_cli_overrides(config, arguments)

    # Run main sync process
    syncer = Sync(config=config)
    syncer.connect(
        nb_host=netbox_host,
        nb_token=netbox_token,
        zbx_host=zabbix_host,
        zbx_user=zabbix_user,
        zbx_pass=zabbix_pass,
        zbx_token=zabbix_token,
    )
    syncer.start()
    syncer.logout()


def parse_cli():
    """
    Parse command-line arguments and run the main function.
    """
    parser = argparse.ArgumentParser(
        description="Synchronise NetBox device data to Zabbix."
    )

    # ── Verbosity ──────────────────────────────────────────────────────────────
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
    parser.add_argument(
        "-c",
        "--config",
        help="Path to the config file (default: config.py next to the script or in the current directory).",
        metavar="FILE",
        default=None,
    )
    parser.add_argument(
        "--version", action="version", version="NetBox-Zabbix Sync 3.4.0"
    )

    # ── Boolean config overrides ───────────────────────────────────────────────
    bool_group = parser.add_argument_group(
        "config overrides (boolean)",
        "Override boolean settings from config.py. "
        "Use --flag to enable or --no-flag to disable. "
        "When omitted, the value from config.py (or the built-in default) is used.",
    )
    for key, help_text in _BOOL_ARGS:
        flag = key.replace("_", "-")
        bool_group.add_argument(
            f"--{flag}",
            dest=key,
            help=help_text,
            action=argparse.BooleanOptionalAction,
            default=None,
        )

    # ── String config overrides ────────────────────────────────────────────────
    str_group = parser.add_argument_group(
        "config overrides (string)",
        "Override string settings from config.py. "
        "When omitted, the value from config.py (or the built-in default) is used.",
    )
    for key, help_text, metavar in _STR_ARGS:
        flag = key.replace("_", "-")
        str_group.add_argument(
            f"--{flag}",
            dest=key,
            help=help_text,
            metavar=metavar,
            default=None,
        )

    args = parser.parse_args()
    main(args)
