"""Shared test doubles for NetBox, Zabbix and the sync's Host objects.

Build NetBox objects through `FakeNetBox` rather than MagicMock or dicts: see
tests/fakes/netbox.py for why. The `netbox` and `zabbix` fixtures in
tests/conftest.py hand out fresh instances to pytest-style tests.
"""

from tests.fakes.hosts import mock_logger, physical_device, sync_config, virtual_machine
from tests.fakes.netbox import DEFAULT, FakeNetBox
from tests.fakes.sync import connect, netbox_mock
from tests.fakes.zabbix import zabbix_api, zabbix_host, zabbix_mock

__all__ = [
    "DEFAULT",
    "FakeNetBox",
    "connect",
    "mock_logger",
    "netbox_mock",
    "physical_device",
    "sync_config",
    "virtual_machine",
    "zabbix_api",
    "zabbix_host",
    "zabbix_mock",
]
