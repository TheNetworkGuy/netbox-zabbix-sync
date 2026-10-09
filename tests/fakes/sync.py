"""Wiring for tests that run `Sync.start()` end to end against the fakes."""

from unittest.mock import MagicMock

from tests.fakes.netbox import FakeNetBox


def netbox_mock(mock_api, netbox: FakeNetBox, devices=(), vms=()) -> MagicMock:
    """Point the patched `nbapi` at an API serving these records.

    The endpoints stay mocks so a test can assert how they were queried (the
    filters passed to `devices.filter`, say), but everything they return comes
    from `netbox`: real Records whose lazy loads and saves hit the fake.
    Everything `Sync.start()` reads before the hosts is an empty result, so a
    test only states the devices and VMs it cares about.
    """
    api = MagicMock(name="nbapi()")
    mock_api.return_value = api
    api.version = netbox.version
    api.dcim.devices.count.return_value = len(devices)
    api.dcim.devices.filter.return_value = list(devices)
    api.virtualization.virtual_machines.filter.return_value = list(vms)
    api.extras.custom_fields.filter.return_value = []
    api.extras.journal_entries = netbox.journal_entries
    api.dcim.site_groups.all.side_effect = lambda: netbox.all("dcim/site-groups")
    api.dcim.regions.all.side_effect = lambda: netbox.all("dcim/regions")
    return api


def connect(syncer):
    """Connect a Sync to the mocked APIs with user/password auth."""
    return syncer.connect(
        "http://netbox.local",
        "nb_token",
        "http://zabbix.local",
        "user",
        "pass",
        None,
    )
