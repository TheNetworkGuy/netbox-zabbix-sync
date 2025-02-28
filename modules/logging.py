"""
Logging module for Netbox-Zabbix-sync
"""

import logging
from os import path

logger = logging.getLogger("NetBox-Zabbix-sync")


def get_logger():
    """
    Return the logger for Netbox Zabbix Sync
    """
    return logger


def setup_logger():
    """
    Prepare a logger with stream and file handlers
    """
    # Set logging
    lgout = logging.StreamHandler()
    lgfile = logging.FileHandler(
        path.join(path.dirname(path.realpath(__file__)), "sync.log")
    )

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
        handlers=[lgout, lgfile],
    )


def set_log_levels(root_level, own_level):
    """
    Configure log levels for root and Netbox-Zabbix-sync logger
    """
    logging.getLogger().setLevel(root_level)
    logger.setLevel(own_level)
