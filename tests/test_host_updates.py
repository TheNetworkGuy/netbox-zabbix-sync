"""Tests for the batched Zabbix host updates in HostUpdateQueue."""

from unittest.mock import MagicMock

import pytest
from zabbix_utils import APIRequestError

from netbox_zabbix_sync.modules.host_updates import HostUpdateQueue


class FakeHost:
    """The parts of a Host the queue uses."""

    def __init__(self, hostid, pending=None, name=None):
        self.zabbix_id = hostid
        self.name = name or f"host-{hostid}"
        self.pending_update = dict(pending or {})
        self.create_journal_entry = MagicMock()


@pytest.fixture
def zabbix():
    return MagicMock(name="ZabbixAPI()")


@pytest.fixture
def logger():
    return MagicMock(name="logger")


def queue(zabbix, logger, **kwargs):
    return HostUpdateQueue(zabbix, logger, **kwargs)


def test_single_host_is_updated_on_its_own(zabbix, logger):
    q = queue(zabbix, logger)
    host = FakeHost("42", {"status": "1", "tags": [{"tag": "a", "value": "b"}]})
    q.add(host)
    q.flush()

    zabbix.host.update.assert_called_once_with(
        hostid="42", status="1", tags=[{"tag": "a", "value": "b"}]
    )
    host.create_journal_entry.assert_called_once()
    assert host.pending_update == {}


def test_nothing_is_queued_without_pending_changes(zabbix, logger):
    q = queue(zabbix, logger)
    q.add(FakeHost("42"))
    q.flush()

    zabbix.host.update.assert_not_called()


def test_hosts_share_one_host_update_with_their_own_changes(zabbix, logger):
    q = queue(zabbix, logger)
    q.add(FakeHost("1", {"status": "1"}))
    q.add(FakeHost("2", {"inventory": {"serialno_a": "X"}}))
    q.flush()

    zabbix.host.update.assert_called_once_with(
        [
            {"hostid": "1", "status": "1"},
            {"hostid": "2", "inventory": {"serialno_a": "X"}},
        ]
    )


def test_full_batch_is_sent_when_added(zabbix, logger):
    q = queue(zabbix, logger, batch_size=2)
    q.add(FakeHost("1", {"status": "1"}))
    zabbix.host.update.assert_not_called()
    q.add(FakeHost("2", {"status": "0"}))
    zabbix.host.update.assert_called_once()

    q.add(FakeHost("3", {"status": "0"}))
    q.flush()
    zabbix.host.update.assert_called_with(hostid="3", status="0")


def test_batch_size_one_sends_every_host_on_its_own(zabbix, logger):
    q = queue(zabbix, logger, batch_size=1)
    q.add(FakeHost("1", {"status": "1"}))
    q.add(FakeHost("2", {"status": "1"}))

    assert zabbix.host.update.call_args_list[0].kwargs == {"hostid": "1", "status": "1"}
    assert zabbix.host.update.call_args_list[1].kwargs == {"hostid": "2", "status": "1"}


def test_same_host_twice_is_split_over_two_calls(zabbix, logger):
    """Zabbix rejects a call that names the same host twice."""
    q = queue(zabbix, logger)
    q.add(FakeHost("1", {"status": "1"}))
    q.add(FakeHost("1", {"description": "x"}))
    q.flush()

    assert [c.kwargs for c in zabbix.host.update.call_args_list] == [
        {"hostid": "1", "status": "1"},
        {"hostid": "1", "description": "x"},
    ]


def test_rejected_batch_is_retried_host_by_host(zabbix, logger):
    """One bad host fails the whole batch; the retry only fails that host."""
    bad = FakeHost("2", {"inventory": {"no_such_field": "x"}})
    good = [FakeHost("1", {"status": "1"}), FakeHost("3", {"status": "0"})]

    def update(*args, **kwargs):
        if args or kwargs.get("hostid") == "2":
            raise APIRequestError("Invalid params.")
        return {"hostids": [kwargs["hostid"]]}

    zabbix.host.update.side_effect = update
    q = queue(zabbix, logger)
    for host in (good[0], bad, good[1]):
        q.add(host)
    q.flush()

    # One batched attempt, then one call per host.
    calls = zabbix.host.update.call_args_list
    assert len(calls[0].args[0]) == len(good) + 1
    assert [c.kwargs["hostid"] for c in calls[1:]] == ["1", "2", "3"]
    for host in good:
        host.create_journal_entry.assert_called_once()
    bad.create_journal_entry.assert_not_called()
    logger.warning.assert_called_once()
    logger.error.assert_called_once()
    assert "host-2" in logger.error.call_args.args


def test_flush_on_empty_queue_does_nothing(zabbix, logger):
    queue(zabbix, logger).flush()
    zabbix.host.update.assert_not_called()
