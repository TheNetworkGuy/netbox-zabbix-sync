"""What a NetBox status does to a Zabbix host, and what the run writes back.

Three settings decide a host's fate before any attribute is synced, and all
three are lists or booleans a user is expected to edit:

- `zabbix_device_removal` -- statuses that delete the Zabbix host entirely.
- `zabbix_device_disable` -- statuses that keep the host but disable it.
- `create_hostgroups` -- whether a missing hostgroup is created or is fatal.

The defaults are exercised incidentally elsewhere (an "active" device syncs
enabled). What is not exercised is the *setting*: a status list is only tested
if a status moves between lists and the outcome moves with it. So each test
below drives a status the default lists put somewhere else, which is the only
way to tell "the setting was read" from "the default happened to agree".

`create_journal` is here for the same reason -- it is the one setting whose
effect lands in NetBox rather than Zabbix, so it is asserted through
`nb.extras.journal_entries` rather than through the Zabbix host.
"""

import pytest

pytestmark = pytest.mark.functional

ZABBIX_STATUS_ENABLED = "0"
ZABBIX_STATUS_DISABLED = "1"

# NetBox slug -> the label `Host.status` actually compares against (host.py:84).
# The config lists are written in labels, not slugs, which is a distinction a
# user only discovers by getting it wrong.
DECOMMISSIONING = ("decommissioning", "Decommissioning")
OFFLINE = ("offline", "Offline")
ACTIVE = ("active", "Active")


@pytest.fixture
def zabbix_hostgroups(zapi):
    """Read hostgroups back, and delete any the test caused to be created.

    Hostgroup creation is the one sync side effect that outlives the host: a
    group stays in Zabbix after its last host is deleted, so without this the
    Zabbix side accumulates a group per run and `create_hostgroups=False` tests
    start passing for the wrong reason -- the group a previous run created is
    already there.
    """
    created = []

    def _get(name: str):
        groups = zapi.hostgroup.get(filter={"name": name}, output=["groupid", "name"])
        if groups:
            created.append(groups[0]["groupid"])
        return groups[0] if groups else None

    yield _get

    for groupid in created:
        zapi.hostgroup.delete(groupid)


def test_status_in_removal_list_deletes_an_existing_host(
    nb, device_factory, run_sync, zabbix_host
):
    """The host is created, then the same device syncs again as removable.

    Two syncs rather than one: a device that starts out removable never reaches
    Zabbix at all, which would pass this test without the deletion path ever
    running. The point is that an *existing* host is torn down.
    """
    device = device_factory()
    run_sync(device.name)
    assert zabbix_host(device.name) is not None, "setup: first sync did not create it"

    device.status = OFFLINE[0]
    device.save()
    run_sync(device.name, zabbix_device_removal=[OFFLINE[1]])

    assert zabbix_host(device.name) is None
    # The NetBox side of the teardown: leaving a stale host ID behind would make
    # the next sync fetch a host that no longer exists.
    assert nb.dcim.devices.get(device.id).custom_fields["zabbix_hostid"] is None


def test_status_out_of_the_removal_list_survives(device_factory, run_sync, zabbix_host):
    """The same status as above, with the setting left at its default.

    "Offline" is in the *disable* default, not the removal default, so this is
    the paired off-state: the host must live, and be disabled rather than gone.
    Without this the test above would pass for a device that never synced.
    """
    device = device_factory(status=OFFLINE[0])

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None, "an offline device was removed under the default lists"
    assert host["status"] == ZABBIX_STATUS_DISABLED


def test_removal_status_on_a_host_zabbix_never_had_is_a_no_op(
    device_factory, run_sync, zabbix_host
):
    """A device that is removable on its first sync is skipped, not an error.

    `_sync_host` returns early with no Zabbix ID rather than calling cleanup
    (core.py:237-247). The run has to continue: an inventory full of
    decommissioned devices is ordinary, and each one raising would end it.
    """
    removable = device_factory(status=DECOMMISSIONING[0])

    run_sync(removable.name)

    assert zabbix_host(removable.name) is None


def test_status_in_a_custom_disable_list_disables_the_host(
    device_factory, run_sync, zabbix_host
):
    """ "Active" is in no default list; putting it in the disable list must bite.

    Chosen deliberately over a status that is already disabled by default --
    that one cannot distinguish the setting being read from the default
    applying.
    """
    device = device_factory(status=ACTIVE[0])

    run_sync(device.name, zabbix_device_disable=[ACTIVE[1]])

    assert zabbix_host(device.name)["status"] == ZABBIX_STATUS_DISABLED


def test_emptying_the_disable_list_keeps_an_offline_host_enabled(
    device_factory, run_sync, zabbix_host
):
    """The off state of the disable feature: an empty list disables nothing."""
    device = device_factory(status=OFFLINE[0])

    run_sync(device.name, zabbix_device_disable=[])

    assert zabbix_host(device.name)["status"] == ZABBIX_STATUS_ENABLED


def test_a_disabled_host_is_re_enabled_when_its_status_changes(
    device_factory, run_sync, zabbix_host
):
    """Status is reconciled on every run, not only set at creation.

    `consistency_check` compares the stored status against the computed one
    (host.py:915). A host disabled by a previous run has to come back when the
    NetBox status moves out of the disable list, or a device stays dark in
    Zabbix after being brought back into service.
    """
    device = device_factory(status=OFFLINE[0])
    run_sync(device.name)
    assert zabbix_host(device.name)["status"] == ZABBIX_STATUS_DISABLED

    device.status = ACTIVE[0]
    device.save()
    run_sync(device.name)

    assert zabbix_host(device.name)["status"] == ZABBIX_STATUS_ENABLED


# A hostgroup format has to name items from a fixed allow-list (tools.py:210),
# so "the device's own name" is not available to make a group unique per test.
# A custom field is: its name passes the allow-list and its *value* is per
# device, which is what these two tests need -- a group name Zabbix has
# certainly never seen, so its presence or absence afterwards means something.
GROUP_CF_TYPES = ["dcim.device"]


def test_missing_hostgroup_is_created_when_enabled(
    zabbix_hostgroups, custom_field_factory, device_factory, run_sync, zabbix_host
):
    """`create_hostgroups` on: a group Zabbix has never seen is created.

    `zabbix_hostgroups` is requested first on purpose: Zabbix refuses to delete
    a group that still holds a host, so the group has to go after
    device_factory's hosts. Finalizers run in reverse setup order, so first
    here means last at teardown.
    """
    cf_name = custom_field_factory(GROUP_CF_TYPES)
    device = device_factory()
    group_name = f"grp-{device.name}"
    device.custom_fields[cf_name] = group_name
    device.save()

    run_sync(device.name, hostgroup_format=cf_name)

    assert zabbix_hostgroups(group_name) is not None, "the group was not created"
    groups = [group["name"] for group in zabbix_host(device.name)["hostgroups"]]
    assert groups == [group_name]


def test_missing_hostgroup_costs_the_host_when_creation_is_disabled(
    # Requested first so it tears down last; see the note in the test above.
    zabbix_hostgroups,
    custom_field_factory,
    device_factory,
    tag_factory,
    sync_runner,
    zabbix_host,
    zapi,
):
    """`create_hostgroups` off: the host is skipped rather than created groupless.

    This is the setting's whole point -- a Zabbix account without hostgroup
    write permission would otherwise fail the API call, so the sync must stop
    short of trying. What has to be shown is that it costs *only* that host, so
    a second device, whose group does exist, is synced in the same run.

    Both devices carry a shared NetBox tag and the run is filtered on it, which
    is what puts them in one run rather than two.
    """
    cf_name = custom_field_factory(GROUP_CF_TYPES)
    tag = tag_factory()
    orphan = device_factory(tags=[tag.id])
    ordinary = device_factory(tags=[tag.id])
    for device, group in ((orphan, "grp-absent"), (ordinary, "grp-present")):
        device.custom_fields[cf_name] = f"{group}-{device.name}"
        device.save()

    # Pre-created by hand, so the sync finds this one rather than making it.
    existing_group = f"grp-present-{ordinary.name}"
    zapi.hostgroup.create(name=existing_group)

    sync_runner(
        device_filter={"tag": tag.slug},
        create_hostgroups=False,
        hostgroup_format=cf_name,
    )

    assert zabbix_host(orphan.name) is None, "host created despite its group missing"
    assert zabbix_hostgroups(f"grp-absent-{orphan.name}") is None, (
        "group created despite create_hostgroups being off"
    )

    host = zabbix_host(ordinary.name)
    assert host is not None, "the run stopped instead of skipping just the one host"
    assert [group["name"] for group in host["hostgroups"]] == [existing_group]
    # Registers the hand-made group with the fixture so it is torn down too.
    assert zabbix_hostgroups(existing_group) is not None


def test_journal_entry_written_on_creation(nb, device_factory, run_sync):
    """`create_journal` on: the sync leaves a trail in NetBox.

    Asserted through `nb.extras.journal_entries` rather than a log line: the
    setting's purpose is a record a NetBox user can see without reading the
    sync's output.
    """
    device = device_factory()

    run_sync(device.name, create_journal=True)

    entries = list(
        nb.extras.journal_entries.filter(
            assigned_object_type="dcim.device", assigned_object_id=device.id
        )
    )
    assert entries, "create_journal was on but NetBox has no entry for the device"
    assert any("Zabbix" in entry.comments for entry in entries)


def test_no_journal_entries_when_disabled(nb, device_factory, run_sync):
    """The off state, and the default -- so the test above proves the setting."""
    device = device_factory()

    run_sync(device.name)

    assert not list(
        nb.extras.journal_entries.filter(
            assigned_object_type="dcim.device", assigned_object_id=device.id
        )
    )


def test_journal_entry_written_on_removal(nb, device_factory, run_sync, zabbix_host):
    """Deletion is journalled too, with `warning` severity (host.py:423).

    The severity matters: a deleted host is the one sync outcome a NetBox user
    would want to notice in the journal without reading it.
    """
    device = device_factory()
    run_sync(device.name, create_journal=True)

    device.status = DECOMMISSIONING[0]
    device.save()
    run_sync(device.name, create_journal=True)

    assert zabbix_host(device.name) is None
    entries = list(
        nb.extras.journal_entries.filter(
            assigned_object_type="dcim.device", assigned_object_id=device.id
        )
    )
    assert "warning" in [entry.kind.value for entry in entries]
