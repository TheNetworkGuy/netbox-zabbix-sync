"""
Module for parsing configuration from the top level config.py file
"""

from pathlib import Path
from importlib import util
from os import environ, path
from logging import getLogger

logger = getLogger(__name__)

# PLEASE NOTE: This is a sample config file. Please do NOT make any edits in this file!
# You should create your own config.py and it will overwrite the default config.

DEFAULT_CONFIG = {
    "templates_config_context": False,
    "templates_config_context_overrule": False,
    "template_cf": "zabbix_template",
    "device_cf": "zabbix_hostid",
    "proxy_cf": False,
    "proxy_group_cf": False,
    "clustering": False,
    "create_hostgroups": True,
    "create_journal": False,
    "sync_vms": False,
    "vm_hostgroup_format": "cluster_type/cluster/role",
    "full_proxy_sync": False,
    "zabbix_device_removal": ["Decommissioning", "Inventory"],
    "zabbix_device_disable": ["Offline", "Planned", "Staged", "Failed"],
    "hostgroup_format": "site/manufacturer/role",
    "traverse_regions": False,
    "traverse_site_groups": False,
    "nb_device_filter": {"name__n": "null"},
    "nb_vm_filter": {"name__n": "null"},
    "inventory_mode": "disabled",
    "inventory_sync": False,
    "extended_site_properties": False,
    "device_inventory_map": {
        "asset_tag": "asset_tag",
        "virtual_chassis/name": "chassis",
        "status/label": "deployment_status",
        "location/name": "location",
        "latitude": "location_lat",
        "longitude": "location_lon",
        "comments": "notes",
        "name": "name",
        "rack/name": "site_rack",
        "serial": "serialno_a",
        "device_type/model": "type",
        "device_type/manufacturer/name": "vendor",
        "oob_ip/address": "oob_ip",
    },
    "vm_inventory_map": {
        "status/label": "deployment_status",
        "comments": "notes",
        "name": "name",
    },
    "usermacro_sync": False,
    "device_usermacro_map": {
        "serial": "{$HW_SERIAL}",
        "role/name": "{$DEV_ROLE}",
        "url": "{$NB_URL}",
        "id": "{$NB_ID}",
    },
    "vm_usermacro_map": {
        "memory": "{$TOTAL_MEMORY}",
        "role/name": "{$DEV_ROLE}",
        "url": "{$NB_URL}",
        "id": "{$NB_ID}",
    },
    "tag_sync": False,
    "tag_lower": True,
    "tag_name": "NetBox",
    "tag_value": "name",
    "device_tag_map": {
        "site/name": "site",
        "rack/name": "rack",
        "platform/name": "target",
    },
    "vm_tag_map": {
        "site/name": "site",
        "cluster/name": "cluster",
        "platform/name": "target",
    },
    "map_site_filter": {},
    "map_orphans": False,
    "map_cf": "zabbix_mapid",
    "map_layout": "auto",
    "map_width": 700,
    "map_height": 700,
    "map_border": 100,
    "map_header_size": None,
    "map_header_color": "696969",
    "map_name_prefix": "",
    "map_name_suffix": "",
    "map_default_bg": None,
    "map_dynamic_bg": False,
    "map_default_icon": "Network_(48)",
    "map_iconmap": None,
    "map_link_uni": "195431",
    "map_link_multi": "2F9F5E",
    "map_link_nc": "97AAB3",
    "map_link_info": "7499FF",
    "map_link_warn": "FFC859",
    "map_link_avg": "FFA059",
    "map_link_high": "E97659",
    "map_link_dis": "E45959",
    "map_link_triggers": False,
    "map_trigger_prio": "warning"
}


def load_config():
    """Returns combined config from all sources"""
    # Overwrite default config with config.py
    conf = load_config_file(config_default=DEFAULT_CONFIG)
    # Overwrite default config and config.py with environment variables
    for key in conf:
        value_setting = load_env_variable(key)
        if value_setting is not None:
            conf[key] = value_setting
    return conf


def load_env_variable(config_environvar):
    """Returns config from environment variable"""
    prefix = "NBZX_"
    config_environvar = prefix + config_environvar.upper()
    if config_environvar in environ:
        return environ[config_environvar]
    return None


def load_config_file(config_default, config_file="config.py"):
    """Returns config from config.py file"""
    # Find the script path and config file next to it.
    script_dir = path.dirname(path.dirname(path.abspath(__file__)))
    config_path = Path(path.join(script_dir, config_file))

    # If the script directory is not found, try the current working directory
    if not config_path.exists():
        config_path = Path(config_file)

    # If both checks fail then fallback to the default config
    if not config_path.exists():
        return config_default

    dconf = config_default.copy()
    # Dynamically import the config module
    spec = util.spec_from_file_location("config", config_path)
    config_module = util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    # Update DEFAULT_CONFIG with variables from the config module
    for key in dconf:
        if hasattr(config_module, key):
            dconf[key] = getattr(config_module, key)
    return dconf
