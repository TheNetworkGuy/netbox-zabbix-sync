"""Batched fetching of the Zabbix hosts that the consistency check compares.

`Host.consistency_check` needs each linked host as Zabbix has it: groups,
templates, interfaces, inventory, macros and tags. Fetching those with one
`host.get` per host makes a run with nothing to change cost one Zabbix call
per host. The `ZabbixHostLookup` fetches them in batches instead.

Before the hosts are processed, the sync tells the lookup the Zabbix host IDs
it is going to ask for, in the order it will ask (`plan`). The first `get` of
an ID in a batch fetches that whole batch with one `host.get`. Only the current
batch is kept, so memory use does not grow with the number of hosts.

An ID that was not planned, such as a Zabbix host that a cluster member takes
over during the run, is fetched on its own.
"""

from collections.abc import Iterable
from logging import Logger
from typing import Any

# What consistency_check reads from a Zabbix host, besides the host fields.
HOST_SELECTS: dict[str, Any] = {
    "selectInterfaces": [
        "type",
        "ip",
        "useip",
        "dns",
        "port",
        "details",
        "interfaceid",
    ],
    "selectGroups": ["groupid"],
    "selectHostGroups": ["groupid"],
    "selectParentTemplates": ["templateid"],
    "selectMacros": ["macro", "value", "type", "description"],
    "selectTags": ["tag", "value"],
}


def host_query(inventory_fields: Iterable[str]) -> dict[str, Any]:
    """The host.get parameters for the hosts consistency_check compares.

    :param inventory_fields: Inventory fields to fetch. Only these are compared,
        and fetching all ~70 would make every host look out of sync.
    """
    query = dict(HOST_SELECTS)
    fields = sorted(set(inventory_fields))
    if fields:
        query["selectInventory"] = fields
    return query


class ZabbixHostLookup:
    """Fetches Zabbix hosts in batches, in the order the sync asks for them."""

    def __init__(
        self,
        zabbix: Any,
        logger: Logger,
        inventory_fields: Iterable[str] = (),
        batch_size: int = 100,
    ):
        """
        :param zabbix: Zabbix API client
        :param logger: Logger
        :param inventory_fields: Inventory fields to fetch, see host_query.
        :param batch_size: Number of hosts fetched per host.get call.
        """
        self.zabbix = zabbix
        self.logger = logger
        self.query = host_query(inventory_fields)
        self.batch_size = max(1, int(batch_size))
        self._batches: list[list[str]] = []
        self._batch_of: dict[str, int] = {}
        self._current: int | None = None
        self._hosts: dict[str, dict] = {}

    def plan(self, hostids: Iterable[Any]) -> None:
        """Set the Zabbix host IDs that will be asked for, in that order."""
        ordered = list(dict.fromkeys(str(h) for h in hostids if h))
        self._batches = [
            ordered[i : i + self.batch_size]
            for i in range(0, len(ordered), self.batch_size)
        ]
        self._batch_of = {
            hostid: n for n, batch in enumerate(self._batches) for hostid in batch
        }
        self._current = None
        self._hosts = {}
        self.logger.debug(
            "Fetching %d Zabbix hosts in %d batches.",
            len(ordered),
            len(self._batches),
        )

    def get(self, hostid: Any) -> dict | None:
        """The Zabbix host with this ID, or None when Zabbix does not have it."""
        key = str(hostid)
        batch = self._batch_of.get(key)
        if batch is None:
            return self._fetch([key]).get(key)
        if batch != self._current:
            self._hosts = self._fetch(self._batches[batch])
            self._current = batch
        return self._hosts.get(key)

    def _fetch(self, hostids: list[str]) -> dict[str, dict]:
        hosts = self.zabbix.host.get(filter={"hostid": hostids}, **self.query)
        return {str(host["hostid"]): host for host in hosts}
