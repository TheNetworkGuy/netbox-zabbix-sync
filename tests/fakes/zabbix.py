"""Zabbix API stand-ins.

zabbix_utils builds its API methods dynamically (`zabbix.host.get` is resolved
at call time), so there is no class to spec a mock against. What matters is
that the return values have the shape Zabbix sends, which is plain JSON, so
the defaults below are dicts and lists as the API returns them.
"""

from unittest.mock import MagicMock


def zabbix_api(version=7.0, hostgroup="TestGroup") -> MagicMock:
    """A Zabbix API with one hostgroup and one template, and no hosts yet.

    `version` is passed through as given: core.py compares it as a string and
    host.py as a number, so a test picks the type its code path needs. A test
    that needs an existing host sets `host.get` itself, see `zabbix_host`.
    """
    zabbix = MagicMock(name="ZabbixAPI()")
    zabbix.version = version
    zabbix.hostgroup.get.return_value = [{"groupid": "1", "name": hostgroup}]
    zabbix.hostgroup.create.return_value = {"groupids": ["2"]}
    zabbix.template.get.return_value = [{"templateid": "1", "name": "TestTemplate"}]
    zabbix.proxy.get.return_value = []
    zabbix.proxygroup.get.return_value = []
    zabbix.host.get.return_value = []
    zabbix.host.create.return_value = {"hostids": ["1"]}
    zabbix.hostinterface.create.return_value = {"interfaceids": ["92"]}
    zabbix.host.update.return_value = {"hostids": ["42"]}
    zabbix.host.delete.return_value = [42]
    return zabbix


def zabbix_mock(mock_zabbix_api, version=7.0, hostgroup="TestGroup") -> MagicMock:
    """Point the patched `ZabbixAPI` class at a `zabbix_api()`."""
    zabbix = zabbix_api(version=version, hostgroup=hostgroup)
    mock_zabbix_api.return_value = zabbix
    return zabbix


def zabbix_host(hostname="test-device", status="0", hostid="42") -> list[dict]:
    """A `host.get` response for an existing host, as consistency_check reads it.

    Linked to the template and group `zabbix_api()` serves, with one agent
    interface, so a host matching the NetBox defaults needs no update.
    """
    return [
        {
            "hostid": hostid,
            "host": hostname,
            "name": hostname,
            "parentTemplates": [{"templateid": "1"}],
            "hostgroups": [{"groupid": "1"}],
            "groups": [{"groupid": "1"}],
            "status": status,
            # One interface with only these keys: len == 1 avoids
            # SyncInventoryError, and nothing in it differs from what the sync
            # would set, so no interface update is triggered.
            "interfaces": [{"type": "1", "port": "10050", "interfaceid": "69"}],
            "inventory_mode": "-1",
            "inventory": {},
            "macros": [],
            "tags": [],
            "proxy_hostid": "0",
            "proxyid": "0",
            "proxy_groupid": "0",
        }
    ]
