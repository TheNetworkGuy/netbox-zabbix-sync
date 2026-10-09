"""Builders for the sync's own Host objects around a NetBox record."""

from copy import deepcopy
from logging import Logger
from unittest.mock import MagicMock, create_autospec

from pynetbox.core.endpoint import Endpoint
from pynetbox.core.response import Record

from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.settings import DEFAULT_CONFIG
from netbox_zabbix_sync.modules.virtual_machine import VirtualMachine
from tests.fakes.netbox import DEFAULT_VERSION
from tests.fakes.zabbix import zabbix_api


def sync_config(**overrides) -> dict:
    """The config a Host gets from Sync: DEFAULT_CONFIG with `overrides` on top."""
    return {**deepcopy(DEFAULT_CONFIG), **overrides}


def mock_logger() -> MagicMock:
    """A logger mock that only accepts real `Logger` methods."""
    return create_autospec(Logger, instance=True)


def _journal_for(nb: Record):
    """The journal endpoint of the FakeNetBox `nb` came from."""
    netbox = getattr(nb.api.http_session, "netbox", None)
    if netbox is not None:
        return netbox.journal_entries
    return create_autospec(Endpoint, instance=True)


def _host(cls, nb, zabbix, journal, nb_version, journal_enabled, logger, config):
    return cls(
        nb,
        zabbix if zabbix is not None else zabbix_api(),
        journal if journal is not None else _journal_for(nb),
        nb_version,
        journal=journal_enabled,
        logger=logger if logger is not None else mock_logger(),
        config=sync_config(**config),
    )


def physical_device(
    nb: Record,
    *,
    zabbix=None,
    journal=None,
    nb_version: str = DEFAULT_VERSION,
    journal_enabled: bool = False,
    logger=None,
    **config,
) -> PhysicalDevice:
    """A PhysicalDevice for `nb`. Keyword arguments override sync settings."""
    return _host(
        PhysicalDevice, nb, zabbix, journal, nb_version, journal_enabled, logger, config
    )


def virtual_machine(
    nb: Record,
    *,
    zabbix=None,
    journal=None,
    nb_version: str = DEFAULT_VERSION,
    journal_enabled: bool = False,
    logger=None,
    **config,
) -> VirtualMachine:
    """A VirtualMachine for `nb`. Keyword arguments override sync settings."""
    return _host(
        VirtualMachine, nb, zabbix, journal, nb_version, journal_enabled, logger, config
    )
