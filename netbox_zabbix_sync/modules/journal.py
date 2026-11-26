"""Bulk creation of NetBox journal entries."""

from logging import Logger
from typing import Any

from pynetbox import RequestError as NetboxRequestError


class JournalQueue:
    """Stands in for the journal-entries endpoint and creates entries in bulk.

    Hosts call `create()` on it exactly as on the endpoint. The entries are
    collected and sent to NetBox in one POST per batch instead of one per entry.
    NetBox creates a bulk POST all-or-nothing, so a rejected batch is retried
    entry by entry and only the faulty entry is lost.
    """

    def __init__(self, endpoint: Any, logger: Logger, batch_size: int = 100):
        """
        :param endpoint: pynetbox journal-entries endpoint
        :param logger: Logger to report failures to
        :param batch_size: Number of entries collected before they are sent
        """
        self.endpoint = endpoint
        self.logger = logger
        self.batch_size = max(1, int(batch_size))
        self._entries: list[dict] = []

    def create(self, entry: dict) -> None:
        """Queue a journal entry, sending the batch once it is full."""
        self._entries.append(entry)
        if len(self._entries) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Send every queued journal entry to NetBox."""
        entries, self._entries = self._entries, []
        if not entries:
            return
        try:
            self.endpoint.create(entries)
            return
        except NetboxRequestError as e:
            if len(entries) == 1:
                self._failed(entries[0], e)
                return
            self.logger.warning(
                "Creating %d journal entries in bulk failed (%s). "
                "Retrying them one by one.",
                len(entries),
                e,
            )
        for entry in entries:
            try:
                self.endpoint.create(entry)
            except NetboxRequestError as e:
                self._failed(entry, e)

    def _failed(self, entry: dict, error) -> None:
        self.logger.warning(
            "Unable to create journal entry for %s %s: NB returned %s",
            entry.get("assigned_object_type"),
            entry.get("assigned_object_id"),
            error,
        )
