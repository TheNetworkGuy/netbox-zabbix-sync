"""
Logging module for Netbox-Zabbix-sync
"""

from logging import (
    WARNING,
    FileHandler,
    Handler,
    NullHandler,
    StreamHandler,
    basicConfig,
    getLogger,
)
from logging.handlers import RotatingFileHandler
from os import PathLike, makedirs, path, sep

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

    When log_file is None (or True), sync.log in the current working directory is used.
    When the path points to a directory, sync.log is created inside it.
    Returns None when file logging is disabled with False.
    """
    if log_file is False:
        return None
    if log_file is None or log_file is True:
        return path.join(path.realpath(path.curdir), DEFAULT_LOG_FILE)
    if not isinstance(log_file, (str, PathLike)):
        raise TypeError(
            f"Invalid value for log_file: {log_file!r}. "
            "Expected a path, None for the default location or False to disable."
        )
    logfile_path = path.realpath(path.expanduser(str(log_file)))
    if path.isdir(logfile_path) or str(log_file).endswith(("/", sep)):
        logfile_path = path.join(logfile_path, DEFAULT_LOG_FILE)
    return logfile_path


def _get_custom_handlers(log_handlers):
    """
    Validate user provided logging handlers.
    Accepts a single handler or a list / tuple of handlers.
    """
    if log_handlers is None:
        return []
    if isinstance(log_handlers, Handler):
        return [log_handlers]
    if isinstance(log_handlers, (list, tuple)) and all(
        isinstance(handler, Handler) for handler in log_handlers
    ):
        return list(log_handlers)
    raise TypeError(
        f"Invalid value for log_handlers: {log_handlers!r}. "
        "Expected a logging.Handler or a list of logging.Handler objects."
    )


def setup_logger(
    log_file=None,
    log_rotation=True,
    log_console=True,
    log_handlers=None,
):
    """
    Prepare a logger with console, file and custom handlers

    :param log_file: Path to the log file. Defaults to sync.log in the current
                     working directory. Set to False to disable file logging.
    :param log_rotation: Rotate the log file (True) or use a plain file (False).
    :param log_console: Log to the console (stderr).
    :param log_handlers: Additional logging.Handler object(s), for instance
                         to send logs to an external service.
    :return: The absolute path of the log file in use, or None when disabled.
    """
    handlers: list[Handler] = []

    # Console logging
    if log_console:
        handlers.append(StreamHandler())

    # File logging, including creation of any missing parent directories
    logfile_path = get_log_file_path(log_file)
    if logfile_path:
        makedirs(path.dirname(logfile_path), exist_ok=True)
        if log_rotation:
            handlers.append(
                RotatingFileHandler(
                    logfile_path,
                    mode="a",
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT,
                )
            )
        else:
            handlers.append(FileHandler(logfile_path, mode="a"))

    # User provided handlers
    handlers.extend(_get_custom_handlers(log_handlers))

    # Prevent Python from printing warnings to stderr when all logging is disabled
    if not handlers:
        handlers.append(NullHandler())

    basicConfig(
        format=LOG_FORMAT,
        level=WARNING,
        handlers=handlers,
    )
    return logfile_path


def set_log_levels(root_level, own_level):
    """
    Configure log levels for root and Netbox-Zabbix-sync logger
    """
    getLogger().setLevel(root_level)
    logger.setLevel(own_level)
