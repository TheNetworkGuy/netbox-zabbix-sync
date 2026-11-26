"""Tests for the batched Zabbix host creation in HostCreateQueue."""

from unittest.mock import MagicMock

import pytest
from pynetbox import RequestError as NetboxRequestError
from zabbix_utils import APIRequestError

from netbox_zabbix_sync.modules.host_creates import HostCreateQueue

DEVICES = "http://netbox.example/api/dcim/devices/"
VMS = "http://netbox.example/api/virtualization/virtual-machines/"


def endpoint(url):
    ep = MagicMock(name=f"Endpoint({url})")
    ep.url = url
    return ep


class FakeHost:
    """The parts of a Host the queue uses, with the real created() bookkeeping."""

    def __init__(self, nb_id, ep, name=None, visible_name=None):
        self.name = name or f"host-{nb_id}"
        self.visible_name = visible_name
        self.use_visible_name = visible_name is not None
        self.config = {"device_cf": "zabbix_hostid"}
        self.zabbix_id = None
        self.nb = MagicMock(name=f"Record({nb_id})")
        self.nb.id = nb_id
        self.nb.endpoint = ep
        self.log_create_failure = MagicMock()
        self.save_zabbix_id = MagicMock()

    def created(self, hostid):
        self.zabbix_id = hostid


def create_hosts(*args, **kwargs):
    count = len(args[0]) if args else 1
    return {"hostids": [str(100 + n) for n in range(count)]}


@pytest.fixture
def zabbix():
    z = MagicMock(name="ZabbixAPI()")
    z.host.create.side_effect = create_hosts
    z.host.get.return_value = [{"host": "taken", "name": "Taken Visible"}]
    return z


@pytest.fixture
def queue(zabbix):
    return HostCreateQueue(zabbix, MagicMock(name="logger"))


def data(host):
    return {"host": host.name}


def test_single_host_is_created_on_its_own(queue, zabbix):
    host = FakeHost(1, endpoint(DEVICES))
    queue.add(host, data(host))
    queue.flush()

    zabbix.host.create.assert_called_once_with(host="host-1")
    assert host.zabbix_id == "100"
    host.save_zabbix_id.assert_called_once()


def test_hosts_are_created_in_one_call_and_written_back_in_bulk(queue, zabbix):
    ep = endpoint(DEVICES)
    hosts = [FakeHost(n, ep) for n in (1, 2, 3)]
    for host in hosts:
        queue.add(host, data(host))
    queue.flush()

    zabbix.host.create.assert_called_once_with([data(host) for host in hosts])
    # IDs are matched to hosts in the order they were sent.
    assert [host.zabbix_id for host in hosts] == ["100", "101", "102"]
    ep.update.assert_called_once_with(
        [
            {"id": 1, "custom_fields": {"zabbix_hostid": 100}},
            {"id": 2, "custom_fields": {"zabbix_hostid": 101}},
            {"id": 3, "custom_fields": {"zabbix_hostid": 102}},
        ]
    )
    for host in hosts:
        host.save_zabbix_id.assert_not_called()


def test_devices_and_vms_are_written_back_to_their_own_endpoint(queue):
    devices, vms = endpoint(DEVICES), endpoint(VMS)
    hosts = [FakeHost(1, devices), FakeHost(2, vms), FakeHost(3, devices)]
    for host in hosts:
        queue.add(host, data(host))
    queue.flush()

    assert [c["id"] for c in devices.update.call_args.args[0]] == [1, 3]
    # A group of one is saved through the record itself.
    vms.update.assert_not_called()
    hosts[1].save_zabbix_id.assert_called_once()


def test_full_batch_is_created_when_added(zabbix):
    queue = HostCreateQueue(zabbix, MagicMock(), batch_size=2)
    ep = endpoint(DEVICES)
    queue.add(FakeHost(1, ep), {"host": "a"})
    zabbix.host.create.assert_not_called()
    queue.add(FakeHost(2, ep), {"host": "b"})
    zabbix.host.create.assert_called_once_with([{"host": "a"}, {"host": "b"}])


def test_rejected_batch_is_retried_host_by_host(queue, zabbix):
    def create(*args, **kwargs):
        if args or kwargs["host"] == "host-2":
            raise APIRequestError("Host with the same name already exists.")
        return {"hostids": [kwargs["host"].removeprefix("host-") + "00"]}

    zabbix.host.create.side_effect = create
    ep = endpoint(DEVICES)
    hosts = [FakeHost(n, ep) for n in (1, 2, 3)]
    for host in hosts:
        queue.add(host, data(host))
    queue.flush()

    assert [host.zabbix_id for host in hosts] == ["100", None, "300"]
    hosts[1].log_create_failure.assert_called_once()
    # Only the hosts that were created are written back.
    assert [c["id"] for c in ep.update.call_args.args[0]] == [1, 3]


def test_rejected_write_back_is_retried_host_by_host(queue):
    ep = endpoint(DEVICES)
    ep.update.side_effect = NetboxRequestError(MagicMock(status_code=400))
    hosts = [FakeHost(1, ep), FakeHost(2, ep)]
    for host in hosts:
        queue.add(host, data(host))
    queue.flush()

    for host in hosts:
        host.save_zabbix_id.assert_called_once()


def test_existing_names_are_fetched_once(queue, zabbix):
    ep = endpoint(DEVICES)
    assert queue.name_exists(FakeHost(1, ep, name="taken"))
    assert not queue.name_exists(FakeHost(2, ep, name="free"))
    assert queue.name_exists(FakeHost(3, ep, name="x", visible_name="Taken Visible"))
    zabbix.host.get.assert_called_once()


def test_a_queued_name_counts_as_taken(queue):
    ep = endpoint(DEVICES)
    first = FakeHost(1, ep, name="twin")
    assert not queue.name_exists(first)
    queue.add(first, data(first))
    assert queue.name_exists(FakeHost(2, ep, name="twin"))
