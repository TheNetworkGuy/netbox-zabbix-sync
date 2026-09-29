"""Every custom field consumer, against every custom field type a real NetBox serves.

The rest of the suite builds its custom fields as `text`, the one type whose
value NetBox serialises as a bare string on every version. NetBox 4.7 changed
the choice types: a `select` value now arrives as `{"value", "label"}` and a
`multiselect` as a list of those, where 4.6 sent the bare value(s). A text-only
suite passes straight through that change without noticing it.

So this file drives each place the sync reads a custom field -- a hostgroup
segment, `proxy_cf`, `template_cf`, the inventory and tag maps, and the
`device_cf` write-back -- with a select (and where it makes sense an object)
field. The assertions are the same whatever NetBox the matrix runs: a choice
resolves to its *value*, which is what every version before 4.7 sent, so an
upgrade does not rename a single hostgroup or change a proxy. Each choice is
given a label that differs from its value so the test can tell which one the
sync used.

The unit-level counterparts, fed payloads captured from both versions, are in
tests/test_custom_fields.py; this file proves those payloads are what NetBox
really sends.
"""

import pytest
from packaging.version import Version

from tests.functional.bootstrap import seed_netbox
from tests.functional.conftest import MONITORED_BY_PROXY, proxy_assignment, tag_pairs
from tests.functional.test_templates import ALTERNATE_TEMPLATE, template_names

pytestmark = pytest.mark.functional

# The first NetBox that wraps choice values in {"value", "label"}.
CHOICE_DICT_VERSION = Version("4.7")

ROLE = seed_netbox.ROLE_NAME
SITE = seed_netbox.SITE_NAME


def uses_choice_dicts(nb) -> bool:
    return Version(nb.version) >= CHOICE_DICT_VERSION


def test_hostid_is_written_back_with_choice_fields_populated(
    nb, device_factory, run_sync, zabbix_host
):
    """The write-back must not trip over the device's own choice fields.

    Writing the host ID back saves the device, and pynetbox sends its whole
    `custom_fields` dict with it. NetBox 4.7 rejects its own choice shape as
    input, so unless pynetbox flattens it the save is a 400 -- raised after the
    Zabbix host exists, and not a SyncError, so it ends the run. Every seeded
    device carries a select default; the multiselect is set too so both
    shapes are in the payload. They must also come through unchanged.
    """
    device = device_factory()
    device.custom_fields[seed_netbox.ENVIRONMENT_CF] = "staging"
    device.custom_fields[seed_netbox.COMPLIANCE_CF] = ["iso27001", "soc2"]
    device.save()
    before = nb.dcim.devices.get(device.id).custom_fields

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None
    after = nb.dcim.devices.get(device.id).custom_fields
    assert after["zabbix_hostid"] == int(host["hostid"])
    for field in (seed_netbox.ENVIRONMENT_CF, seed_netbox.COMPLIANCE_CF):
        assert after[field] == before[field], f"{field} changed by the write-back"


@pytest.fixture
def cf_of_type(nb, seeded, custom_field_factory):
    """Make a custom field of a given type and the input value for "zone-a".

    Returns `(field_name, input_value, expected_name)`. The select's label is
    "Zone A" so a sync that picked the label instead of the value is visible;
    the object field points at the seeded tenant, which the sync resolves by
    name.
    """

    def make(cf_type: str, object_types: list[str]):
        if cf_type == "select":
            name = custom_field_factory(
                object_types, "select", choices=[("zone-a", "Zone A")]
            )
            return name, "zone-a", "zone-a"
        if cf_type == "object":
            name = custom_field_factory(
                object_types, "object", related_object_type="tenancy.tenant"
            )
            return name, seeded["tenant"].id, seed_netbox.TENANT_NAME
        name = custom_field_factory(object_types)
        return name, "zone-a", "zone-a"

    return make


@pytest.mark.parametrize("cf_type", ["text", "select", "object"])
def test_hostgroup_segment_from_each_custom_field_type(
    cf_type, cf_of_type, device_factory, run_sync, zabbix_host
):
    """A custom field segment resolves to the same name for every type.

    These are the three types core.py fetches for `verify_hg_format`, so the
    only ones a hostgroup format can name. On 4.7 a select used to break this
    outright: the lookup came back None and the join raised TypeError, which
    ended the whole run rather than one host.
    """
    cf_name, value, expected = cf_of_type(cf_type, ["dcim.device"])
    device = device_factory()
    device.custom_fields[cf_name] = value
    device.save()

    run_sync(device.name, hostgroup_format=f"site/{cf_name}/role")

    groups = [group["name"] for group in zabbix_host(device.name)["hostgroups"]]
    assert groups == [f"{SITE}/{expected}/{ROLE}"]


@pytest.mark.parametrize("on", ["device", "site"])
def test_proxy_from_a_select_custom_field(
    on,
    proxy_factory,
    nb,
    seeded,
    custom_field_factory,
    device_factory,
    run_sync,
    zabbix_host,
    proxy_id,
):
    """`proxy_cf` pointing at a select field picks the proxy named by its value.

    A select is the natural way to model "which proxy" in NetBox, and on 4.7 it
    silently lost the proxy: the lookup came back None, and the host was left
    on the server with nothing logged above debug. Both the device field and
    the site fallback read the value the same way, so both are covered.
    """
    proxy_name = proxy_factory()
    cf_name = custom_field_factory(
        ["dcim.device", "dcim.site"],
        "select",
        choices=[(proxy_name, f"Proxy {proxy_name}")],
    )
    device = device_factory()

    if on == "device":
        device.custom_fields[cf_name] = proxy_name
        device.save()
        run_sync(device.name, proxy_cf=cf_name)
    else:
        # Refetched: the session-scoped Record keeps whatever custom_fields it
        # last saved, including ones a fixture has since deleted.
        site = nb.dcim.sites.get(seeded["site"].id)
        site.custom_fields[cf_name] = proxy_name
        site.save()
        try:
            run_sync(device.name, proxy_cf=cf_name)
        finally:
            site = nb.dcim.sites.get(seeded["site"].id)
            site.custom_fields[cf_name] = None
            site.save()

    assert proxy_assignment(zabbix_host(device.name)) == (
        MONITORED_BY_PROXY,
        proxy_id(proxy_name),
    )


@pytest.fixture
def device_type_select_cf(nb, seeded, custom_field_factory):
    """A select field on the shared device type, holding a template name.

    Cleared afterwards however the test ends, for the same reason as
    test_templates.device_type_cf: the device type is session-seeded, and the
    next test's device would otherwise inherit a template it never asked for.
    """
    cf_name = custom_field_factory(
        ["dcim.devicetype"],
        "select",
        choices=[(ALTERNATE_TEMPLATE, "Ping only")],
    )
    device_type_id = seeded["device_type"].id
    device_type = nb.dcim.device_types.get(device_type_id)
    device_type.custom_fields[cf_name] = ALTERNATE_TEMPLATE
    device_type.save()

    yield cf_name

    device_type = nb.dcim.device_types.get(device_type_id)
    device_type.custom_fields[cf_name] = None
    device_type.save()


def test_template_from_a_select_custom_field(
    device_type_select_cf, device_factory, run_sync, zabbix_host
):
    """`template_cf` on a select field links the template its value names.

    ALTERNATE_TEMPLATE is not the seeded default, so the host carrying it
    proves the select was read, rather than the sync falling back to the
    `zabbix_template` text field every device type already has.
    """
    device = device_factory()

    run_sync(device.name, template_cf=device_type_select_cf)

    assert template_names(zabbix_host(device.name)) == [ALTERNATE_TEMPLATE]


def test_choice_fields_map_into_inventory_as_values(
    device_factory, run_sync, zabbix_host
):
    """`custom_fields/<select>` and `<multiselect>` reach Zabbix as values.

    Uses the seeded fields, whose labels ("Staging", "ISO 27001") differ from
    their values. On 4.7 the unmapped result was the Python repr of the dict,
    stored verbatim in the inventory.
    """
    device = device_factory()
    device.custom_fields[seed_netbox.ENVIRONMENT_CF] = "staging"
    device.custom_fields[seed_netbox.COMPLIANCE_CF] = ["iso27001", "soc2"]
    device.save()

    run_sync(
        device.name,
        inventory_sync=True,
        inventory_mode="manual",
        device_inventory_map={
            f"custom_fields/{seed_netbox.ENVIRONMENT_CF}": "alias",
            f"custom_fields/{seed_netbox.COMPLIANCE_CF}": "tag",
        },
    )

    inventory = zabbix_host(device.name)["inventory"]
    assert inventory["alias"] == "staging"
    assert inventory["tag"] == "['iso27001', 'soc2']"


def test_select_field_maps_into_a_tag_as_its_value(
    device_factory, run_sync, zabbix_host
):
    """The tag map reads custom fields through the same mapper as inventory.

    A separate caller of `field_mapper` with its own post-processing
    (`render_tag`), so it is checked on its own rather than assumed.
    """
    device = device_factory()
    device.custom_fields[seed_netbox.ENVIRONMENT_CF] = "staging"
    device.save()

    run_sync(
        device.name,
        tag_sync=True,
        tag_name=False,
        device_tag_map={f"custom_fields/{seed_netbox.ENVIRONMENT_CF}": "environment"},
    )

    assert ("environment", "staging") in tag_pairs(zabbix_host(device.name))
