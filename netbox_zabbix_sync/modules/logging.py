"""
Logging module for Netbox-Zabbix-sync
"""

from logging import WARNING, StreamHandler, basicConfig, getLogger
from logging.handlers import RotatingFileHandler
from os import makedirs, path, sep

LOGGER_NAME = "NetBox-Zabbix-sync"
DEFAULT_LOG_FILE = "sync.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

logger = getLogger(LOGGER_NAME)


def get_logger():
    """
    Return the logger for Netbox Zabbix Sync
    """
    return logger


def get_log_file_path(log_file=None):
    """
    Resolve the absolute path of the log file.

    When no path is given, sync.log in the current working directory is used.
    When the path points to a directory, sync.log is created inside it.
    """
    if not log_file:
        return path.join(path.realpath(path.curdir), DEFAULT_LOG_FILE)
    logfile_path = path.realpath(path.expanduser(str(log_file)))
    if path.isdir(logfile_path) or str(log_file).endswith(("/", sep)):
        logfile_path = path.join(logfile_path, DEFAULT_LOG_FILE)
    return logfile_path


def setup_logger(log_file=None):
    """
    Prepare a logger with stream and rotating file handlers

    :param log_file: Optional path to the log file. Defaults to sync.log
                     in the current working directory.
    :return: The absolute path of the log file in use.
    """
    # Set logging
    lgout = StreamHandler()

    # Create log file, including any missing parent directories
    logfile_path = get_log_file_path(log_file)
    makedirs(path.dirname(logfile_path), exist_ok=True)
    lgfile = RotatingFileHandler(
        logfile_path, mode="a", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )

    basicConfig(
        format=LOG_FORMAT,
        level=WARNING,
        handlers=[lgout, lgfile],
    )
    return logfile_path


def set_log_levels(root_level, own_level):
    """
    Configure log levels for root and Netbox-Zabbix-sync logger
    """
    getLogger().setLevel(root_level)
    logger.setLevel(own_level)
