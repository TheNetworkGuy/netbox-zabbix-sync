"""Batched Zabbix host updates.

`Host.consistency_check` does not update Zabbix itself. Every property it finds
out of sync is collected in the host's `pending_update`, and the
`HostUpdateQueue` sends those to Zabbix in batches: one `host.update` call
carrying an array of host objects, each with its own properties.

Zabbix applies such a call all-or-nothing: one rejected host fails the whole
batch. A failed batch is therefore retried host by host, so a bad value only
costs its own host, the same as when every host was updated on its own.

`host.massupdate` was measured as an alternative for hosts that need the same
change. It saves well under a second per 500 hosts on simple fields and nothing
on template changes, where Zabbix spends its time linking items either way, so
it is not worth the extra code path (and it rejects tags on Zabbix 7.0).
"""

from logging import Logger
from typing import TYPE_CHECKING, Any

from zabbix_utils import APIRequestError

from netbox_zabbix_sync.modules.tools import sanatize_log_output

if TYPE_CHECKING:
    from netbox_zabbix_sync.modules.host import Host


class HostUpdateQueue:
    """Collects pending host updates and sends them to Zabbix in batches."""

    def __init__(self, zabbix: Any, logger: Logger, batch_size: int = 100):
        """
        :param zabbix: Zabbix API client
        :param logger: Logger to report updates and failures to
        :param batch_size: Number of hosts queued before the queue is flushed.
            1 sends every host on its own.
        """
        self.zabbix = zabbix
        self.logger = logger
        self.batch_size = max(1, int(batch_size))
        self._queue: dict[str, Host] = {}

    def add(self, host: "Host") -> None:
        """Queue the pending update of a host, flushing once the batch is full."""
        if not host.pending_update or not host.zabbix_id:
            return
        hostid = str(host.zabbix_id)
        # Zabbix rejects a call that names the same host twice.
        if hostid in self._queue:
            self.flush()
        self._queue[hostid] = host
        if len(self._queue) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Send every queued update to Zabbix."""
        hosts = list(self._queue.values())
        self._queue = {}
        if not hosts:
            return
        if len(hosts) == 1:
            self._update_single(hosts[0])
            return
        try:
            self.zabbix.host.update(
                [{"hostid": host.zabbix_id, **host.pending_update} for host in hosts]
            )
        except APIRequestError as e:
            self.logger.warning(
                "Batched host.update of %d hosts was rejected by Zabbix (%s). "
                "Retrying these hosts one by one.",
                len(hosts),
                e,
            )
            for host in hosts:
                self._update_single(host)
            return
        for host in hosts:
            self._applied(host)

    def _update_single(self, host: "Host") -> None:
        try:
            self.zabbix.host.update(hostid=host.zabbix_id, **host.pending_update)
        except APIRequestError as e:
            self.logger.error(
                "Host %s: Unable to update. Zabbix returned the following error: %s.",
                host.name,
                e,
            )
            host.pending_update = {}
            return
        self._applied(host)

    def _applied(self, host: "Host") -> None:
        self.logger.info(
            "Host %s: updated with data %s.",
            host.name,
            sanatize_log_output(host.pending_update),
        )
        host.create_journal_entry("info", "Updated host in Zabbix with latest NB data.")
        host.pending_update = {}
