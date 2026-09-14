"""The NetBox filters the sync sends, against a real NetBox.

These tests exist because of a bug no mock could have caught. The sync asked
for device custom fields with ``content_types="dcim.device"`` and NetBox
answered with *every* custom field: it silently ignores filter params it does
not recognise, exactly as it ignores ``bogus_filter=xyz``. The param that works
is ``object_type`` (singular). ``verify_hg_format`` was therefore validating
device hostgroup formats against VM custom fields too.

That bug shapes every test here: **a filter in the query string proves nothing
about whether it filtered.** So each test seeds a decoy the filter must exclude
and asserts on what came back. Where a test also inspects recorded requests,
that is only to pin down *how* the result was reached -- server-side rather
than in Python, once rather than once per host.
"""

import pytest

from netbox_zabbix_sync.modules.exceptions import HostgroupError
from tests.functional.bootstrap.seed_netbox import EXPECTED_HOSTGROUP
from tests.functional.conftest import requests_to

CUSTOM_FIELDS_ENDPOINT = "/api/extras/custom-fields/"
DEVICES_ENDPOINT = "/api/dcim/devices/"
VMS_ENDPOINT = "/api/virtualization/virtual-machines/"

# The only custom field types the sync accepts as hostgroup components
# (core.py:285).
FILTERED_CF_TYPES = {"text", "object", "select"}

# A device name that matches nothing, for syncs that are only meant to exercise
# the custom-field validation that runs before any device is fetched.
NO_DEVICE = "no-such-device"


# --- object_type: the custom field filter that regressed --------------------


def test_vm_custom_field_rejected_in_device_hostgroup(
    custom_field_factory, sync_runner
):
    """A VM-only custom field is not a valid *device* hostgroup component.

    The regression test for the `content_types` bug. With the broken filter the
    VM custom field came back in the device list, verify_hg_format accepted it,
    and this sync ran clean -- the failure surfaced later and further away, when
    the device turned out not to have the field.
    """
    vm_cf = custom_field_factory(object_types=["virtualization.virtualmachine"])

    with pytest.raises(HostgroupError, match=vm_cf):
        sync_runner(
            device_filter={"name": NO_DEVICE},
            hostgroup_format=f"site/manufacturer/role/{vm_cf}",
        )


def test_device_custom_field_accepted_in_device_hostgroup(
    custom_field_factory, device_factory, sync_runner, zabbix_host
):
    """A device custom field still builds a hostgroup, end to end.

    The other half of the regression: a filter that excluded everything would
    satisfy the test above while breaking every real user. This is what proves
    `object_type` selects rather than merely rejects.
    """
    device_cf = custom_field_factory(object_types=["dcim.device"])
    device = device_factory(address="10.0.0.30/24")
    device.custom_fields[device_cf] = "Frontend"
    device.save()

    sync_runner(
        device_filter={"name": device.name},
        hostgroup_format=f"site/manufacturer/role/{device_cf}",
    )

    host = zabbix_host(device.name)
    assert host is not None, "device with a valid custom-field hostgroup was not synced"
    groups = [group["name"] for group in host["hostgroups"]]
    assert f"{EXPECTED_HOSTGROUP}/Frontend" in groups


def test_device_custom_field_rejected_in_vm_hostgroup(
    custom_field_factory, sync_runner
):
    """The VM side of the same filter (core.py:299), mirrored.

    No VM needs to exist: verify_hg_format runs on the custom field list before
    any VM is fetched.
    """
    device_cf = custom_field_factory(object_types=["dcim.device"])

    with pytest.raises(HostgroupError, match=device_cf):
        sync_runner(
            device_filter={"name": NO_DEVICE},
            sync_vms=True,
            vm_hostgroup_format=f"cluster_type/cluster/role/{device_cf}",
        )


def test_custom_field_filter_is_sent_and_honoured(
    custom_field_factory, sync_runner, netbox_requests
):
    """`object_type` goes on the wire, and NetBox acts on it.

    The second assertion is the one that would have caught the bug: it reads
    the response the sync actually received and checks the VM custom field is
    genuinely absent, rather than trusting the param to mean something.
    """
    vm_cf = custom_field_factory(object_types=["virtualization.virtualmachine"])
    device_cf = custom_field_factory(object_types=["dcim.device"])

    sync_runner(device_filter={"name": NO_DEVICE})

    cf_requests = requests_to(netbox_requests, CUSTOM_FIELDS_ENDPOINT)
    assert len(cf_requests) == 1, "device custom fields should be fetched exactly once"
    fetch = cf_requests[0]

    assert fetch.params["object_type"] == ["dcim.device"], (
        "must filter on object_type; NetBox silently ignores both content_types "
        "and object_types"
    )
    assert set(fetch.params["type"]) == FILTERED_CF_TYPES

    returned = {cf["name"] for cf in fetch.results}
    assert device_cf in returned
    assert vm_cf not in returned, "object_type filter was ignored by NetBox"


def test_integer_custom_field_rejected_in_hostgroup(custom_field_factory, sync_runner):
    """The `type` filter is honoured too, not just object_type.

    An integer custom field *is* a device custom field, so only the type filter
    keeps it out. If NetBox ignored `type` the way it ignored `content_types`,
    this sync would accept it as a hostgroup component.
    """
    integer_cf = custom_field_factory(object_types=["dcim.device"], cf_type="integer")

    with pytest.raises(HostgroupError, match=integer_cf):
        sync_runner(
            device_filter={"name": NO_DEVICE},
            hostgroup_format=f"site/manufacturer/role/{integer_cf}",
        )


# --- device filters: config, method, and how they combine -------------------


def test_device_filter_scopes_the_sync(device_factory, sync_runner, zabbix_host):
    """`start(device_filter=...)` syncs that device and no other.

    The whole functional suite leans on this for isolation and nothing else
    asserts it: if the filter were ignored, every test would sync every device
    and still pass.
    """
    target = device_factory(address="10.0.0.31/24")
    other = device_factory(address="10.0.0.32/24")

    sync_runner(device_filter={"name": target.name})

    assert zabbix_host(target.name) is not None
    assert zabbix_host(other.name) is None, "device filter did not scope the sync"


def test_device_filter_is_applied_server_side(
    device_factory, sync_runner, netbox_requests
):
    """NetBox does the filtering, rather than the sync fetching all and picking.

    Both devices exist and match the config filter; only one is asked for, so
    the response must contain exactly that one.
    """
    target = device_factory(address="10.0.0.33/24")
    other = device_factory(address="10.0.0.34/24")

    sync_runner(device_filter={"name": target.name})

    fetches = requests_to(netbox_requests, DEVICES_ENDPOINT)
    assert fetches, "no device request was made"

    returned = {device["name"] for ex in fetches for device in ex.results}
    assert returned == {target.name}, (
        f"device fetch was not filtered server-side; {other.name} came back too"
    )


def test_config_and_method_device_filters_are_combined(
    device_factory, sync_runner, netbox_requests
):
    """_combine_filters puts both filters on the wire.

    The config default is `{"name__n": "null"}` (settings.py:34) and the method
    filter is the device name; the request must carry both.
    """
    target = device_factory(address="10.0.0.35/24")

    sync_runner(device_filter={"name": target.name})

    fetches = requests_to(netbox_requests, DEVICES_ENDPOINT)
    assert fetches, "no device request was made"
    params = fetches[0].params
    assert params["name"] == [target.name]
    assert params["name__n"] == ["null"], "config filter was dropped"


def test_method_device_filter_overrides_config_filter(
    device_factory, sync_runner, zabbix_host
):
    """On an overlapping key the method filter wins (core.py:65).

    The config filter names the wrong device, so if it were the one to survive,
    `other` would be synced and `target` would not. Asserted on the Zabbix end
    state rather than the URL, because that is the difference users would feel.
    """
    target = device_factory(address="10.0.0.36/24")
    other = device_factory(address="10.0.0.37/24")

    sync_runner(
        device_filter={"name": target.name},
        nb_device_filter={"name": other.name},
    )

    assert zabbix_host(target.name) is not None, "method filter did not win"
    assert zabbix_host(other.name) is None, "config filter was not overridden"


# --- call efficiency --------------------------------------------------------


def test_custom_fields_fetched_once_for_many_devices(
    device_factory, sync_runner, zabbix_host, netbox_requests
):
    """Custom field definitions are fetched per run, not per device.

    Two devices in one sync, one custom-field request. Guards against the N+1
    that moving the fetch inside the device loop would introduce.
    """
    first = device_factory(address="10.0.0.38/24")
    second = device_factory(address="10.0.0.39/24")

    sync_runner(device_filter={"name": [first.name, second.name]})

    assert zabbix_host(first.name) is not None
    assert zabbix_host(second.name) is not None, "multi-value name filter did not OR"

    assert len(requests_to(netbox_requests, CUSTOM_FIELDS_ENDPOINT)) == 1


def test_vm_endpoints_untouched_when_vm_sync_disabled(
    device_factory, sync_runner, netbox_requests
):
    """sync_vms=False must cost nothing: no VM fetch, no VM custom fields.

    Both sit behind the same flag (core.py:295), so a request to either
    endpoint means the guard has been lost.
    """
    device = device_factory(address="10.0.0.40/24")

    sync_runner(device_filter={"name": device.name}, sync_vms=False)

    assert requests_to(netbox_requests, VMS_ENDPOINT) == []
    assert len(requests_to(netbox_requests, CUSTOM_FIELDS_ENDPOINT)) == 1, (
        "VM custom fields were fetched despite sync_vms=False"
    )


def test_vm_filter_reaches_netbox_when_vm_sync_enabled(sync_runner, netbox_requests):
    """The VM filter is combined and sent the same way the device one is."""
    sync_runner(
        device_filter={"name": NO_DEVICE},
        vm_filter={"name": "no-such-vm"},
        sync_vms=True,
    )

    fetches = requests_to(netbox_requests, VMS_ENDPOINT)
    assert fetches, "sync_vms=True did not fetch VMs"
    params = fetches[0].params
    assert params["name"] == ["no-such-vm"]
    assert params["name__n"] == ["null"], "config VM filter was dropped"

    object_types = [
        ex.params.get("object_type")
        for ex in requests_to(netbox_requests, CUSTOM_FIELDS_ENDPOINT)
    ]
    assert ["dcim.device"] in object_types
    assert ["virtualization.virtualmachine"] in object_types


def test_vm_config_filter_scopes_the_sync(vm_factory, sync_runner, zabbix_host, seeded):
    """`nb_vm_filter` alone, with no method filter, decides which VMs sync.

    The test above proves the filter reaches the wire; this one proves it
    *filtered*, which is the distinction this whole file exists for. A decoy VM
    is seeded that the filter must exclude -- without it, a sync that ignored
    the filter entirely would look identical.
    """
    target = vm_factory()
    decoy = vm_factory()

    sync_runner(
        device_filter={"name": NO_DEVICE},
        sync_vms=True,
        nb_vm_filter={"name": target.name},
    )

    assert zabbix_host(target.name) is not None, "the filtered VM did not sync"
    assert zabbix_host(decoy.name) is None, "nb_vm_filter did not exclude the decoy"


def test_method_vm_filter_overrides_the_config_vm_filter(
    vm_factory, sync_runner, zabbix_host
):
    """On an overlapping key the method filter wins, as it does for devices.

    The VM half of `_combine_filters` is a separate call site from the device
    half, so the precedence has to be checked on both -- the same reason the
    VM maps get their own tests rather than being assumed to match.
    """
    target = vm_factory()
    other = vm_factory()

    sync_runner(
        device_filter={"name": NO_DEVICE},
        vm_filter={"name": target.name},
        sync_vms=True,
        nb_vm_filter={"name": other.name},
    )

    assert zabbix_host(target.name) is not None, "method VM filter did not win"
    assert zabbix_host(other.name) is None, "config VM filter was not overridden"
