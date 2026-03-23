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

    # Create log file in current working directory
    working_dir = path.realpath(path.curdir)
    logfile_path = path.join(working_dir, "sync.log")
    lgfile = logging.FileHandler(logfile_path)

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
