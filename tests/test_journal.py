"""Tests for the bulk creation of NetBox journal entries in JournalQueue."""

from unittest.mock import MagicMock

import pytest
from pynetbox import RequestError as NetboxRequestError

from netbox_zabbix_sync.modules.journal import JournalQueue


def entry(n):
    return {"assigned_object_type": "dcim.device", "assigned_object_id": n}


@pytest.fixture
def endpoint():
    return MagicMock(name="journal_entries")


def test_entries_are_sent_in_one_request(endpoint):
    journals = JournalQueue(endpoint, MagicMock())
    journals.create(entry(1))
    journals.create(entry(2))
    endpoint.create.assert_not_called()

    journals.flush()
    endpoint.create.assert_called_once_with([entry(1), entry(2)])


def test_full_batch_is_sent_when_added(endpoint):
    journals = JournalQueue(endpoint, MagicMock(), batch_size=2)
    journals.create(entry(1))
    journals.create(entry(2))
    endpoint.create.assert_called_once_with([entry(1), entry(2)])


def test_flush_on_empty_queue_does_nothing(endpoint):
    JournalQueue(endpoint, MagicMock()).flush()
    endpoint.create.assert_not_called()


def test_rejected_batch_is_retried_entry_by_entry(endpoint):
    error = NetboxRequestError(MagicMock(status_code=400))

    def create(data):
        if isinstance(data, list) or data["assigned_object_id"] == 2:  # noqa: PLR2004
            raise error

    endpoint.create.side_effect = create
    logger = MagicMock()
    journals = JournalQueue(endpoint, logger)
    for n in (1, 2, 3):
        journals.create(entry(n))
    journals.flush()

    sent = [c.args[0] for c in endpoint.create.call_args_list[1:]]
    assert sent == [entry(1), entry(2), entry(3)]
    # One warning for the batch, one for the entry that failed again.
    assert logger.warning.call_count == 2  # noqa: PLR2004
