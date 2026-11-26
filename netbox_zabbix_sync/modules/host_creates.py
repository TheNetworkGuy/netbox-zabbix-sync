"""Batched creation of Zabbix hosts.

`Host.prepare_create` builds the host.create parameters of a host; the
`HostCreateQueue` collects those and creates the hosts in batches, with one
`host.create` call carrying an array of hosts. The Zabbix host IDs it gets back
are then written to NetBox with one bulk PATCH per batch instead of one per host.

Zabbix creates a batch all-or-nothing: one rejected host fails the whole call.
A failed batch is therefore retried host by host, so a bad host only costs
itself, the same as when every host was created on its own. The same goes for
the NetBox write-back.

Whether a host already exists in Zabbix is checked against one list of all host
names, fetched once per run, rather than with a host.get per new host.
"""

from logging import Logger
from typing import TYPE_CHECKING, Any

from pynetbox import RequestError as NetboxRequestError
from zabbix_utils import APIRequestError

if TYPE_CHECKING:
    from netbox_zabbix_sync.modules.host import Host


class HostCreateQueue:
    """Collects new hosts and creates them in Zabbix in batches."""

    def __init__(
        self,
        zabbix: Any,
        logger: Logger,
        batch_size: int = 50,
        netbox_batch_size: int = 100,
    ):
        """
        :param zabbix: Zabbix API client
        :param logger: Logger to report failures to
        :param batch_size: Number of hosts created per host.create call.
            1 creates every host on its own.
        :param netbox_batch_size: Number of NetBox objects updated per bulk PATCH
            when the new Zabbix host IDs are written back.
        """
        self.zabbix = zabbix
        self.logger = logger
        self.batch_size = max(1, int(batch_size))
        self.netbox_batch_size = max(1, int(netbox_batch_size))
        self._queue: list[tuple[Host, dict]] = []
        # Technical and visible names of every Zabbix host, loaded on first use.
        self._hostnames: set[str] | None = None
        self._visible_names: set[str] | None = None

    def name_exists(self, host: "Host") -> bool:
        """Whether Zabbix already has a host by this name (see Host.prepare_create)."""
        if self._hostnames is None or self._visible_names is None:
            existing = self.zabbix.host.get(output=["host", "name"])
            self._hostnames = {h["host"] for h in existing}
            self._visible_names = {h["name"] for h in existing}
        if host.use_visible_name:
            return host.visible_name in self._visible_names
        return host.name in self._hostnames

    def add(self, host: "Host", create_data: dict) -> None:
        """Queue a host for creation, creating the batch once it is full."""
        # Taken from here on: a second NetBox object by the same name in this
        # run is reported as already present, as it would have been before.
        if self._hostnames is not None and self._visible_names is not None:
            self._hostnames.add(host.name)
            if host.visible_name:
                self._visible_names.add(host.visible_name)
        self._queue.append((host, create_data))
        if len(self._queue) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Create every queued host and write the new host IDs back to NetBox."""
        queue, self._queue = self._queue, []
        if not queue:
            return
        if len(queue) == 1:
            created = [host for host, data in queue if self._create_single(host, data)]
        else:
            created = self._create_batch(queue)
        self._write_back(created)

    def _create_batch(self, queue: list[tuple["Host", dict]]) -> list["Host"]:
        try:
            result = self.zabbix.host.create([data for _, data in queue])
        except APIRequestError as e:
            self.logger.warning(
                "Batched host.create of %d hosts was rejected by Zabbix (%s). "
                "Retrying these hosts one by one.",
                len(queue),
                e,
            )
            return [host for host, data in queue if self._create_single(host, data)]
        # Zabbix returns the new IDs in the order the hosts were sent.
        for (host, _), hostid in zip(queue, result["hostids"], strict=True):
            host.created(hostid)
        return [host for host, _ in queue]

    def _create_single(self, host: "Host", create_data: dict) -> bool:
        try:
            result = self.zabbix.host.create(**create_data)
        except APIRequestError as e:
            host.log_create_failure(e)
            return False
        host.created(result["hostids"][0])
        return True

    def _write_back(self, hosts: list["Host"]) -> None:
        """Save the new Zabbix host IDs to NetBox, in bulk per endpoint."""
        by_endpoint: dict[str, list[Host]] = {}
        for host in hosts:
            by_endpoint.setdefault(host.nb.endpoint.url, []).append(host)
        for group in by_endpoint.values():
            for start in range(0, len(group), self.netbox_batch_size):
                self._write_back_batch(group[start : start + self.netbox_batch_size])

    def _write_back_batch(self, hosts: list["Host"]) -> None:
        if len(hosts) == 1:
            hosts[0].save_zabbix_id()
            return
        endpoint = hosts[0].nb.endpoint
        try:
            endpoint.update(
                [
                    {
                        "id": host.nb.id,
                        "custom_fields": {
                            host.config["device_cf"]: int(host.zabbix_id)
                        },
                    }
                    for host in hosts
                    if host.zabbix_id is not None
                ]
            )
        except NetboxRequestError as e:
            self.logger.warning(
                "Saving the Zabbix host IDs of %d hosts to NetBox in bulk failed (%s). "
                "Retrying them one by one.",
                len(hosts),
                e,
            )
            for host in hosts:
                host.save_zabbix_id()
