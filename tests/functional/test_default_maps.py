"""The maps that ship in DEFAULT_CONFIG, against a real NetBox and Zabbix.

Every other mapping test names its own map, which proves the *mechanism* works
and says nothing about the twenty-odd mappings users actually get out of the
box. Those have two ends, and both can rot without any test noticing:

- **The Zabbix end.** An inventory key must be one of Zabbix's ~70 inventory
  fields and a macro name must satisfy Zabbix's own grammar. A typo is an API
  error at sync time, not a stored value -- so it breaks the user's run, not
  ours, unless a real Zabbix is asked.
- **The NetBox end.** A path like `device_type/manufacturer/name` is a claim
  about the shape NetBox serialises, including how much of a related object it
  nests. NetBox can change that in a minor release, and `field_mapper` walks
  with `Record[key]`, which does not lazy-load what is missing.

So these tests deliberately do *not* pass a map. They let DEFAULT_CONFIG apply
and assert on what Zabbix stored, which is the only thing that can fail when the
shipped defaults stop matching either end.
"""

from uuid import uuid4

import pytest

from netbox_zabbix_sync.modules.settings import DEFAULT_CONFIG
from tests.functional.bootstrap import seed_netbox
from tests.functional.conftest import macro_values, tag_pairs

pytestmark = pytest.mark.functional


def default_map(name: str) -> dict[str, str]:
    """A map out of DEFAULT_CONFIG, narrowed to the dict it is.

    DEFAULT_CONFIG holds every setting, so its value type is the union of all of
    them; the maps have to be narrowed before they can be walked.
    """
    value = DEFAULT_CONFIG[name]
    assert isinstance(value, dict)
    return value


INVENTORY_MANUAL_CONFIG = {"inventory_sync": True, "inventory_mode": "manual"}

# Set on the fully-populated device below, and asserted through the default map.
SERIAL = "SN-DEFAULTS-1"
COMMENTS = "a note that lands in the Zabbix inventory"
OOB_ADDRESS = "10.20.0.1/24"


@pytest.fixture
def placement(nb, seeded):
    """A location, rack and platform for a device to sit in, cleaned up after.

    Only the default-map tests need these: they exist to populate the paths the
    shipped `device_inventory_map` and `device_tag_map` name (`location/name`,
    `rack/name`, `platform/name`), which a bare device leaves empty and which
    would therefore prove nothing about whether the path resolves.
    """
    suffix = uuid4().hex[:8]
    location = nb.dcim.locations.create(
        name=f"loc-{suffix}", slug=f"loc-{suffix}", site=seeded["site"].id
    )
    rack = nb.dcim.racks.create(
        name=f"rack-{suffix}", site=seeded["site"].id, location=location.id
    )
    platform = nb.dcim.platforms.create(name=f"plat-{suffix}", slug=f"plat-{suffix}")

    yield {"location": location, "rack": rack, "platform": platform}

    # NetBox refuses to delete a rack with a device still in it, so these can
    # only go after device_factory's teardown. Finalizers run in reverse setup
    # order, which is why `populated_device` requests this fixture *before*
    # device_factory: that makes device_factory the later setup and so the
    # earlier teardown.
    for obj in (rack, location, platform):
        obj.delete()


@pytest.fixture
def populated_device(placement, device_factory, seeded):
    """A device with every field the default device maps name actually set.

    Argument order matters here; see the note in `placement`'s teardown.
    """
    return device_factory(
        address="10.20.1.1/24",
        oob_address=OOB_ADDRESS,
        location=placement["location"].id,
        rack=placement["rack"].id,
        position=1,
        face="front",
        platform=placement["platform"].id,
        serial=SERIAL,
        asset_tag=f"AT-{placement['rack'].name}",
        comments=COMMENTS,
        latitude="52.370216",
        longitude="4.895168",
    )


def test_default_device_inventory_map_reaches_zabbix(
    populated_device, placement, run_sync, zabbix_host
):
    """The shipped map, unmodified, against a device that populates all of it.

    Asserting every entry rather than a sample: the point is the map as
    shipped, and a spot check would leave the other ten free to rot. A wrong
    Zabbix key fails this test by erroring the sync, a wrong NetBox path by
    landing an empty value.
    """
    run_sync(populated_device.name, **INVENTORY_MANUAL_CONFIG)

    inventory = zabbix_host(populated_device.name)["inventory"]
    assert {
        key: inventory[key] for key in default_map("device_inventory_map").values()
    } == {
        "asset_tag": populated_device.asset_tag,
        # No virtual chassis on this device: an absent *parent* is the one
        # missing-data case field_mapper handles, mapping it to "".
        "chassis": "",
        "deployment_status": "Active",
        "location": placement["location"].name,
        "location_lat": "52.370216",
        "location_lon": "4.895168",
        "notes": COMMENTS,
        "name": populated_device.name,
        "site_rack": placement["rack"].name,
        "serialno_a": SERIAL,
        "type": seed_netbox.DEVICE_TYPE_MODEL,
        "vendor": seed_netbox.MANUFACTURER_NAME,
        "oob_ip": OOB_ADDRESS,
    }


def test_default_device_inventory_map_survives_a_bare_device(
    device_factory, run_sync, zabbix_host
):
    """The common case: a device with none of the optional fields set.

    Every nullable path in the shipped map has to map to "" rather than raise,
    or the run ends on the first sparsely-filled device -- which, given the map
    names a rack, a location, an asset tag and an OOB IP, is most of them.
    """
    device = device_factory()

    run_sync(device.name, **INVENTORY_MANUAL_CONFIG)

    host = zabbix_host(device.name)
    assert host is not None, "a device with only required fields did not sync"
    assert host["inventory"]["name"] == device.name
    assert host["inventory"]["site_rack"] == ""


def test_default_device_usermacro_map_reaches_zabbix(
    populated_device, run_sync, zabbix_host
):
    """Every shipped macro name must satisfy Zabbix's grammar, not just ours.

    `ZabbixUsermacros.validate_macro` is the project's own regex; it is not
    Zabbix's. A name that passes ours and fails Zabbix's is an API error, and
    only a real Zabbix can tell the two apart.
    """
    run_sync(populated_device.name, usermacro_sync=True)

    macros = macro_values(zabbix_host(populated_device.name))
    assert macros == {
        "{$HW_SERIAL}": SERIAL,
        "{$DEV_ROLE}": seed_netbox.ROLE_NAME,
        "{$NB_URL}": populated_device.url,
        "{$NB_ID}": str(populated_device.id),
    }


def test_default_nb_url_macro_is_the_api_url_not_the_web_ui(
    populated_device, run_sync, zabbix_host
):
    """Pinning a surprise in the shipped map rather than endorsing it.

    `{$NB_URL}` maps from `url`, which pynetbox and NetBox both use for the
    *API* endpoint of the object. Someone clicking this macro in Zabbix expecting
    the device's page gets JSON instead. NetBox serves the UI address separately
    as `display_url`, so the fix -- if it is one -- is a map change, not a code
    change. Left as-is because changing it would move every existing user's
    macro; the test is here so the choice is visible.
    """
    run_sync(populated_device.name, usermacro_sync=True)

    nb_url = macro_values(zabbix_host(populated_device.name))["{$NB_URL}"]
    assert nb_url is not None
    assert "/api/dcim/devices/" in nb_url
    assert nb_url == populated_device.url


def test_default_device_tag_map_reaches_zabbix(
    populated_device, placement, run_sync, zabbix_host
):
    """The shipped tag map, with tag_name off so only mapped tags are present.

    `tag_name` defaults to "NetBox" and would add a tag per NetBox tag on top of
    the map's, which is a different feature; it is turned off here so this test
    fails for map reasons only.
    """
    run_sync(populated_device.name, tag_sync=True, tag_name=None)

    assert tag_pairs(zabbix_host(populated_device.name)) == {
        ("site", seed_netbox.SITE_NAME.lower()),
        ("rack", placement["rack"].name.lower()),
        ("target", placement["platform"].name.lower()),
    }


def test_default_vm_inventory_map_reaches_zabbix(vm_factory, run_vm_sync, zabbix_host):
    vm = vm_factory(comments=COMMENTS)

    run_vm_sync(vm.name, **INVENTORY_MANUAL_CONFIG)

    inventory = zabbix_host(vm.name)["inventory"]
    assert {
        key: inventory[key] for key in default_map("vm_inventory_map").values()
    } == {
        "deployment_status": "Active",
        "notes": COMMENTS,
        "name": vm.name,
    }


def test_default_vm_usermacro_map_reaches_zabbix(vm_factory, run_vm_sync, zabbix_host):
    vm = vm_factory(memory=4096)

    run_vm_sync(vm.name, usermacro_sync=True)

    assert macro_values(zabbix_host(vm.name)) == {
        "{$TOTAL_MEMORY}": "4096",
        "{$DEV_ROLE}": seed_netbox.ROLE_NAME,
        "{$NB_URL}": vm.url,
        "{$NB_ID}": str(vm.id),
    }


def test_default_vm_tag_map_reaches_zabbix(vm_factory, run_vm_sync, zabbix_host):
    """`platform/name` is in the shipped map but this VM has no platform.

    Left unset deliberately: a VM without a platform is ordinary, and the tag
    that cannot be built has to be dropped rather than raise or land empty.
    `render_tag` rejects the empty value (tags.py:63), which is why `target` is
    absent here instead of present and blank.
    """
    vm = vm_factory()

    run_vm_sync(vm.name, tag_sync=True, tag_name=None)

    assert tag_pairs(zabbix_host(vm.name)) == {
        ("site", seed_netbox.SITE_NAME.lower()),
        ("cluster", seed_netbox.CLUSTER_NAME.lower()),
    }


def test_hostgroup_reaches_cluster_type_but_a_map_cannot(
    vm_factory, run_vm_sync, zabbix_host
):
    """The same NetBox field, reachable one way and not the other.

    `vm_hostgroup_format` resolves `cluster_type` via `self.nb.cluster.type.name`
    (hostgroups.py:88) -- attribute access, so pynetbox lazily fetches the full
    cluster and the field is there. A map naming the same field as
    `cluster/type/name` goes through `field_mapper`, which walks with
    `Record[key]`; that is `dict(self)[key]`, it does not lazy-load, and NetBox
    does not nest `type` inside the cluster it returns on a VM.

    So the field is simultaneously available and unavailable depending on which
    feature asks. The map does not merely miss it -- the KeyError escapes
    `Sync.start()` and ends the run, which is the bug pinned in
    test_extended_models.py::test_unextended_mapped_field_aborts_the_run. Here
    the hostgroup half is asserted as the contrast; the map half is left to that
    test rather than duplicating an xfail.
    """
    vm = vm_factory()

    run_vm_sync(vm.name)

    groups = [group["name"] for group in zabbix_host(vm.name)["hostgroups"]]
    assert groups == [seed_netbox.EXPECTED_VM_HOSTGROUP]
    assert seed_netbox.CLUSTER_TYPE_NAME in groups[0]
