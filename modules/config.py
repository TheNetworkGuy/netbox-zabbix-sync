"""
Module for parsing configuration from the top level config.yaml file
"""
from pathlib import Path
from importlib import util
from os import environ
from logging import getLogger

logger = getLogger(__name__)

DEFAULT_CONFIG = {
    "templates_config_context": False,
    "templates_config_context_overrule": False,
    "template_cf": "zabbix_template",
    "device_cf": "zabbix_hostid",
    "clustering": False,
    "create_hostgroups": True,
    "create_journal": False,
    "sync_vms": False,
    "zabbix_device_removal": ["Decommissioning", "Inventory"],
    "zabbix_device_disable": ["Offline", "Planned", "Staged", "Failed"]
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
    prefix = "NZS_"
    config_environvar = prefix + config_environvar.upper()
    if config_environvar in environ:
        return environ[config_environvar]
    return None


def load_config_file(config_default, config_file="config.py"):
    """Returns config from config.py file"""
    # Check if config.py exists and load it
    # If it does not exist, return the default config
    config_path = Path(config_file)
    if config_path.exists():
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
    logger.warning(
        "Config file %s not found. Using default config "
        "and environment variables.", config_file)
    return None
