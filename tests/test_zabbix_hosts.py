"""Tests for the batched fetching of Zabbix hosts in ZabbixHostLookup."""

from unittest.mock import MagicMock

import pytest

from netbox_zabbix_sync.modules.zabbix_hosts import ZabbixHostLookup, host_query


def zabbix_with(*existing):
    """A Zabbix whose host.get answers a hostid filter from these host IDs."""
    zabbix = MagicMock(name="ZabbixAPI()")

    def host_get(filter, **kwargs):
        return [{"hostid": h} for h in filter["hostid"] if h in existing]

    zabbix.host.get.side_effect = host_get
    return zabbix


def fetched(zabbix):
    return [c.kwargs["filter"]["hostid"] for c in zabbix.host.get.call_args_list]


@pytest.fixture
def zabbix():
    return zabbix_with(*(str(n) for n in range(1, 10)))


def test_planned_hosts_are_fetched_in_batches(zabbix):
    lookup = ZabbixHostLookup(zabbix, MagicMock(), batch_size=2)
    lookup.plan([1, 2, 3])

    assert [lookup.get(n)["hostid"] for n in (1, 2, 3)] == ["1", "2", "3"]
    assert fetched(zabbix) == [["1", "2"], ["3"]]


def test_netbox_integer_ids_match_zabbix_string_ids(zabbix):
    """NetBox stores the host ID as an integer, Zabbix returns it as a string."""
    lookup = ZabbixHostLookup(zabbix, MagicMock())
    lookup.plan([5])
    assert lookup.get(5) == {"hostid": "5"}
    assert lookup.get("5") == {"hostid": "5"}
    assert zabbix.host.get.call_count == 1


def test_host_missing_from_zabbix_is_none():
    zabbix = zabbix_with("1")
    lookup = ZabbixHostLookup(zabbix, MagicMock())
    lookup.plan([1, 2])
    assert lookup.get(2) is None
    assert lookup.get(1) == {"hostid": "1"}
    assert zabbix.host.get.call_count == 1


def test_unplanned_host_is_fetched_on_its_own(zabbix):
    """Such as the host a cluster member takes over during the run."""
    lookup = ZabbixHostLookup(zabbix, MagicMock())
    lookup.plan([1, 2])
    assert lookup.get(7) == {"hostid": "7"}
    assert fetched(zabbix) == [["7"]]


def test_only_the_current_batch_is_kept(zabbix):
    lookup = ZabbixHostLookup(zabbix, MagicMock(), batch_size=1)
    lookup.plan([1, 2])
    lookup.get(1)
    lookup.get(2)
    # Going back reloads the earlier batch rather than keeping every batch.
    lookup.get(1)
    assert fetched(zabbix) == [["1"], ["2"], ["1"]]


def test_empty_and_duplicate_ids_are_not_planned(zabbix):
    lookup = ZabbixHostLookup(zabbix, MagicMock())
    lookup.plan([None, 0, 1, 1, "2"])
    lookup.get(1)
    assert fetched(zabbix) == [["1", "2"]]


def test_only_mapped_inventory_fields_are_selected():
    query = host_query(["serialno_a", "name", "name"])
    assert query["selectInventory"] == ["name", "serialno_a"]
    assert "selectInventory" not in host_query([])
