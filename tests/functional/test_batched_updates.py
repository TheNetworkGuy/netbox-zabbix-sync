"""Host updates reach Zabbix in batches, not one call per host or per change.

`consistency_check` only collects what is out of sync and `HostUpdateQueue`
sends it in one `host.update` carrying one object per host.

The other functional tests each sync a single device, so they only ever see a
batch of one -- the tests here put several hosts through the same run, which is
the only way to reach the batched calls.
"""

import pytest

pytestmark = pytest.mark.functional

ZABBIX_STATUS_DISABLED = "1"
ZABBIX_INVENTORY_MANUAL = "0"
ZABBIX_INVENTORY_DISABLED = "-1"

# Zabbix stores location_lat in a 16 character column and rejects anything
# longer, which makes it a field one host can fail on while others pass.
LOCATION_LAT_LIMIT = 16


def names(devices) -> dict:
    return {"name": [device.name for device in devices]}


def test_hosts_sharing_a_change_are_all_updated(
    device_factory, sync_runner, zabbix_host
):
    """The same status change on several hosts lands on every one of them."""
    devices = [device_factory() for _ in range(3)]
    sync_runner(device_filter=names(devices))

    for device in devices:
        device.status = "offline"
        device.save()
    sync_runner(device_filter=names(devices))

    for device in devices:
        assert zabbix_host(device.name)["status"] == ZABBIX_STATUS_DISABLED, (
            f"{device.name} was not disabled by the batched update"
        )


@pytest.mark.parametrize("batch_size", [1, 100])
def test_hosts_with_their_own_changes_each_get_their_own(
    device_factory, sync_runner, zabbix_host, batch_size
):
    """Per-host values sent together are not mixed up between hosts."""
    devices = [device_factory(serial=f"SER-{n}") for n in range(3)]
    sync_runner(device_filter=names(devices))

    sync_runner(
        device_filter=names(devices),
        inventory_sync=True,
        inventory_mode="manual",
        device_inventory_map={"serial": "serialno_a"},
        zabbix_update_batch_size=batch_size,
    )

    for device in devices:
        host = zabbix_host(device.name)
        assert host["inventory_mode"] == ZABBIX_INVENTORY_MANUAL
        assert host["inventory"]["serialno_a"] == device.serial


def test_one_rejected_host_does_not_cost_the_batch(
    device_factory, sync_runner, zabbix_host
):
    """Zabbix applies a batch all-or-nothing; the sync retries host by host.

    The middle device's serial is too long for the inventory field it is mapped
    to, so the batched host.update is rejected as a whole. The other two hosts
    must still be updated, and the rejected host must be left as it was rather
    than half-updated.
    """
    bad_serial = "9" * (LOCATION_LAT_LIMIT + 10)
    devices = [
        device_factory(serial="1.5"),
        device_factory(serial=bad_serial),
        device_factory(serial="2.5"),
    ]
    sync_runner(device_filter=names(devices))

    sync_runner(
        device_filter=names(devices),
        inventory_sync=True,
        inventory_mode="manual",
        device_inventory_map={"serial": "location_lat"},
    )

    good, bad = (devices[0], devices[2]), devices[1]
    for device in good:
        host = zabbix_host(device.name)
        assert host["inventory_mode"] == ZABBIX_INVENTORY_MANUAL, (
            f"{device.name} lost its update because another host was rejected"
        )
        assert host["inventory"]["location_lat"] == device.serial
    assert zabbix_host(bad.name)["inventory_mode"] == ZABBIX_INVENTORY_DISABLED, (
        "the rejected host was partly updated"
    )

