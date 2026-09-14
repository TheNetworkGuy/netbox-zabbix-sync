"""
Logging module for Netbox-Zabbix-sync
"""

from logging import WARNING, StreamHandler, basicConfig, getLogger
from logging.handlers import RotatingFileHandler
from os import path

logger = getLogger("NetBox-Zabbix-sync")


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
    lgout = StreamHandler()

    # Create log file in current working directory
    working_dir = path.realpath(path.curdir)
    logfile_path = path.join(working_dir, "sync.log")
    lgfile = RotatingFileHandler(logfile_path, mode="a", maxBytes=5 * 1024 * 1024, backupCount=3)

    basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=WARNING,
        handlers=[lgout, lgfile],
    )


def set_log_levels(root_level, own_level):
    """
    Configure log levels for root and Netbox-Zabbix-sync logger
    """
    getLogger().setLevel(root_level)
    logger.setLevel(own_level)
