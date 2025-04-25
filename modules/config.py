"""
Module for parsing configuration from the top level config.yaml file
"""
from pathlib import Path
import yaml

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


def load_config(config_path="config.yaml"):
    """Loads config from YAML file and combines it with default config"""
    # Get data from default config.
    config = DEFAULT_CONFIG.copy()
    # Set config path
    config_file = Path(config_path)
    # Check if file exists
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
                config.update(user_config)
        except OSError:
            # Probably some I/O error with user permissions etc.
            # Ignore for now and return default config
            pass
    return config
