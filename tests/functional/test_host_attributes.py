"""Tags, usermacros and inventory, generated from NetBox and read back from Zabbix.

The mocked suite already covers how ZabbixTags, ZabbixUsermacros and
field_mapper build these structures. What it cannot cover is the half that only
a real stack has an opinion about:

- **Zabbix accepts what we build.** A tag, macro or inventory field that Zabbix
  rejects fails the host.create call, and the mocked tests -- which assert on
  the dict handed to a mock -- pass either way. Inventory is the sharp edge: a
  key that is not one of Zabbix's ~70 inventory fields is an API error, so the
  inventory maps are only really validated here.
- **NetBox returns what the map asks for.** A map walks NetBox fields by name
  (`device_type/model`); whether that path exists on the object NetBox actually
  serves is not something a DummyNB can answer, because a DummyNB has whatever
  fields its test gave it.

So the assertions read the host back through the raw ZabbixAPI. Where a feature
is a switch, the test pairs it with its off state -- an assertion that tags
appear when tag_sync is on proves nothing unless something also proves they
stay away when it is off.
"""

from tests.functional.bootstrap.seed_netbox import (
    DEVICE_TYPE_MODEL,
    MANUFACTURER_NAME,
    ROLE_NAME,
    SITE_NAME,
)
from tests.functional.conftest import macro_types, macro_values, tag_pairs

# Zabbix inventory_mode as the API reports it (host.py:311-323).
INVENTORY_DISABLED = "-1"
INVENTORY_MANUAL = "0"
INVENTORY_AUTOMATIC = "1"

# Zabbix usermacro types (usermacros.py:57).
MACRO_TEXT = "0"
MACRO_SECRET = "1"


# --- tags -------------------------------------------------------------------


def test_tags_not_synced_when_disabled(
    device_factory, tag_factory, run_sync, zabbix_host
):
    """tag_sync=False leaves the host untagged, whatever NetBox offers.

    The off state for every tag test below. The device carries a NetBox tag and
    a config context tag, so all three of ZabbixTags' sources have something to
    contribute and the empty result is the flag's doing rather than an empty
    device (host.py:591).
    """
    tag = tag_factory()
    device = device_factory(
        address="10.0.0.50/24",
        tags=[tag.id],
        config_context={"zabbix": {"tags": [{"env": "production"}]}},
    )

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None
    assert host["tags"] == [], "tags were synced despite tag_sync=False"


def test_tags_from_field_map(device_factory, run_sync, zabbix_host):
    """A device_tag_map entry becomes a Zabbix tag, values lowercased.

    `site/name` also exercises field_mapper's nested walk against a real
    NetBox object: the sync gets the site as a *nested* record on the device,
    and `name` is one of the few fields present there without extension.
    """
    device = device_factory(address="10.0.0.51/24")

    run_sync(
        device.name,
        tag_sync=True,
        tag_name=False,
        device_tag_map={"site/name": "site", "role/name": "role"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    # tag_lower defaults to True, so the values arrive lowercased but the tag
    # names -- which come from the map, not from NetBox -- are already lower.
    assert ("site", SITE_NAME.lower()) in tag_pairs(host)
    assert ("role", ROLE_NAME.lower()) in tag_pairs(host)


def test_tag_lower_false_preserves_netbox_case(device_factory, run_sync, zabbix_host):
    """tag_lower=False syncs NetBox's capitalisation through to Zabbix."""
    device = device_factory(address="10.0.0.52/24")

    run_sync(
        device.name,
        tag_sync=True,
        tag_name=False,
        tag_lower=False,
        device_tag_map={"site/name": "Site"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert ("Site", SITE_NAME) in tag_pairs(host)


def test_tags_from_config_context(device_factory, run_sync, zabbix_host):
    """Tags listed under config_context['zabbix']['tags'] reach Zabbix.

    The map and the NetBox-tag source are both off, so anything on the host
    came from the config context.
    """
    device = device_factory(
        address="10.0.0.53/24",
        config_context={
            "zabbix": {"tags": [{"env": "Production"}, {"team": "Networking"}]}
        },
    )

    run_sync(device.name, tag_sync=True, tag_name=False, device_tag_map={})

    host = zabbix_host(device.name)
    assert host is not None
    assert tag_pairs(host) == {("env", "production"), ("team", "networking")}


def test_netbox_tags_synced_under_tag_name(
    device_factory, tag_factory, run_sync, zabbix_host
):
    """NetBox tags have no key/value shape, so they land under `tag_name`.

    Two NetBox tags become two Zabbix tags sharing one name -- which Zabbix
    permits, and which is the whole reason tag_name exists.
    """
    first = tag_factory(name="Rack-A")
    second = tag_factory(name="Rack-B")
    device = device_factory(address="10.0.0.54/24", tags=[first.id, second.id])

    run_sync(
        device.name,
        tag_sync=True,
        tag_name="NetBox",
        tag_value="name",
        device_tag_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert tag_pairs(host) == {("netbox", "rack-a"), ("netbox", "rack-b")}


def test_netbox_tag_value_uses_slug(device_factory, tag_factory, run_sync, zabbix_host):
    """tag_value='slug' picks the slug rather than the name.

    The tag's name and slug differ, so a sync that ignored tag_value would
    still produce a tag -- just the wrong one.
    """
    tag = tag_factory(name="Production Site")
    assert tag.slug != tag.name, "fixture must make name and slug distinguishable"
    device = device_factory(address="10.0.0.55/24", tags=[tag.id])

    run_sync(
        device.name,
        tag_sync=True,
        tag_name="NetBox",
        tag_value="slug",
        device_tag_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert tag_pairs(host) == {("netbox", tag.slug.lower())}


def test_tags_updated_on_rerun(device_factory, run_sync, zabbix_host):
    """A tag removed in NetBox is removed from the Zabbix host on the next run.

    Tags are replaced wholesale rather than merged, so a stale tag would
    otherwise outlive the config context entry that created it. The device is
    edited between syncs, which is what a user changing a config context does.
    """
    device = device_factory(
        address="10.0.0.56/24",
        config_context={"zabbix": {"tags": [{"env": "staging"}]}},
    )

    run_sync(device.name, tag_sync=True, tag_name=False, device_tag_map={})
    assert tag_pairs(zabbix_host(device.name)) == {("env", "staging")}

    device.local_context_data = {"zabbix": {"tags": [{"env": "production"}]}}
    device.save()

    run_sync(device.name, tag_sync=True, tag_name=False, device_tag_map={})

    host = zabbix_host(device.name)
    assert tag_pairs(host) == {("env", "production")}, "stale tag survived the rerun"


# --- usermacros -------------------------------------------------------------


def test_usermacros_not_synced_when_disabled(device_factory, run_sync, zabbix_host):
    """usermacro_sync=False leaves the host without macros (host.py:570)."""
    device = device_factory(
        address="10.0.0.60/24",
        config_context={"zabbix": {"usermacros": {"{$SNMP_COMMUNITY}": "public"}}},
    )

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None
    assert host["macros"] == [], "macros were synced despite usermacro_sync=False"


def test_usermacros_from_field_map(device_factory, run_sync, zabbix_host):
    """A device_usermacro_map entry becomes a Zabbix usermacro.

    `id` is in the map to pin down field_mapper's handling of a non-string
    NetBox value: Zabbix's macro value is a string, so the int must be
    converted rather than sent as-is and rejected.
    """
    device = device_factory(address="10.0.0.61/24", serial="SN-12345")

    run_sync(
        device.name,
        usermacro_sync=True,
        device_usermacro_map={"serial": "{$HW_SERIAL}", "id": "{$NB_ID}"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    macros = macro_values(host)
    assert macros["{$HW_SERIAL}"] == "SN-12345"
    assert macros["{$NB_ID}"] == str(device.id)


def test_usermacros_from_config_context(device_factory, run_sync, zabbix_host):
    """Config context macros sync in both their string and dict forms.

    The dict form is the one that carries a type and description; the plain
    string form is shorthand for a text macro (usermacros.py:88).
    """
    device = device_factory(
        address="10.0.0.62/24",
        config_context={
            "zabbix": {
                "usermacros": {
                    "{$SNMP_COMMUNITY}": "public",
                    "{$IFCONTROL}": {
                        "value": "1",
                        "type": "text",
                        "description": "Interface control",
                    },
                }
            }
        },
    )

    run_sync(device.name, usermacro_sync=True, device_usermacro_map={})

    host = zabbix_host(device.name)
    assert host is not None
    assert macro_values(host) == {"{$SNMP_COMMUNITY}": "public", "{$IFCONTROL}": "1"}
    assert macro_types(host)["{$IFCONTROL}"] == MACRO_TEXT


def test_secret_usermacro_created_as_secret(device_factory, run_sync, zabbix_host):
    """A macro typed `secret` is created with Zabbix's secret type.

    The type is most of what can be asserted: Zabbix withholds a secret macro's
    value, omitting the key from the response rather than blanking it. It is
    also the part that matters -- the same value stored as a text macro would
    be readable by anyone with API access.
    """
    device = device_factory(
        address="10.0.0.63/24",
        config_context={
            "zabbix": {
                "usermacros": {
                    "{$SECRET_PASS}": {"value": "hunter2", "type": "secret"},
                    "{$PLAIN}": {"value": "visible", "type": "text"},
                }
            }
        },
    )

    run_sync(device.name, usermacro_sync=True, device_usermacro_map={})

    host = zabbix_host(device.name)
    assert host is not None
    assert macro_types(host)["{$SECRET_PASS}"] == MACRO_SECRET
    assert macro_types(host)["{$PLAIN}"] == MACRO_TEXT
    assert macro_values(host)["{$SECRET_PASS}"] is None, (
        "Zabbix returned a secret macro's value; it should be withheld"
    )
    assert macro_values(host)["{$PLAIN}"] == "visible"


def test_invalid_macro_name_skipped_without_failing_host(
    device_factory, run_sync, zabbix_host
):
    """One unusable macro does not cost the host its valid ones.

    `NOT_A_MACRO` fails validate_macro. Zabbix would reject the whole
    host.create if it were sent, so this proves the macro is dropped before the
    call rather than merely logged.
    """
    device = device_factory(
        address="10.0.0.64/24",
        config_context={
            "zabbix": {"usermacros": {"NOT_A_MACRO": "nope", "{$VALID}": "yes"}}
        },
    )

    run_sync(device.name, usermacro_sync=True, device_usermacro_map={})

    host = zabbix_host(device.name)
    assert host is not None, "invalid macro took the whole host down"
    assert macro_values(host) == {"{$VALID}": "yes"}


# --- inventory --------------------------------------------------------------


def test_inventory_not_synced_when_mode_disabled(device_factory, run_sync, zabbix_host):
    """inventory_mode='disabled' wins even with inventory_sync=True.

    The combination is a misconfiguration the sync logs and declines to act on
    (host.py:312-319), so the host must come out with inventory off rather than
    populated.
    """
    device = device_factory(address="10.0.0.70/24", serial="SN-DISABLED")

    run_sync(device.name, inventory_sync=True, inventory_mode="disabled")

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory_mode"] == INVENTORY_DISABLED
    assert host["inventory"] in ([], {}), "inventory populated despite disabled mode"


def test_inventory_synced_in_manual_mode(device_factory, run_sync, zabbix_host):
    """The default device_inventory_map populates Zabbix inventory.

    Left as the default map on purpose: every key in it is asserted by Zabbix
    to be a real inventory field, so this is what catches a typo'd or renamed
    field there -- the mocked tests would accept `serialno_typo` happily.
    """
    device = device_factory(
        address="10.0.0.71/24", serial="SN-MANUAL", asset_tag="ASSET-001"
    )

    run_sync(device.name, inventory_sync=True, inventory_mode="manual")

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory_mode"] == INVENTORY_MANUAL

    inventory = host["inventory"]
    assert inventory["serialno_a"] == "SN-MANUAL"
    assert inventory["asset_tag"] == "ASSET-001"
    assert inventory["name"] == device.name
    assert inventory["type"] == DEVICE_TYPE_MODEL
    assert inventory["vendor"] == MANUFACTURER_NAME


def test_inventory_automatic_mode(device_factory, run_sync, zabbix_host):
    """inventory_mode='automatic' maps to Zabbix's automatic mode."""
    device = device_factory(address="10.0.0.72/24", serial="SN-AUTO")

    run_sync(device.name, inventory_sync=True, inventory_mode="automatic")

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory_mode"] == INVENTORY_AUTOMATIC
    assert host["inventory"]["serialno_a"] == "SN-AUTO"


def test_inventory_mode_set_without_sync(device_factory, run_sync, zabbix_host):
    """inventory_mode alone sets the mode and syncs no fields.

    The two settings are independent: a user may want Zabbix's own discovery to
    fill the inventory while NetBox stays out of it.
    """
    device = device_factory(address="10.0.0.73/24", serial="SN-NOSYNC")

    run_sync(device.name, inventory_sync=False, inventory_mode="manual")

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory_mode"] == INVENTORY_MANUAL
    assert "SN-NOSYNC" not in str(host["inventory"]), (
        "NetBox data reached the inventory despite inventory_sync=False"
    )


def test_inventory_maps_custom_field(
    device_factory, custom_field_factory, run_sync, zabbix_host
):
    """A custom field can be mapped into inventory through a nested path.

    `custom_fields/<name>` is the documented way to get user-defined NetBox
    data into Zabbix, and it is the deepest walk field_mapper does against a
    real object.
    """
    cf = custom_field_factory(object_types=["dcim.device"])
    device = device_factory(address="10.0.0.74/24")
    device.custom_fields[cf] = "Rack 42"
    device.save()

    run_sync(
        device.name,
        inventory_sync=True,
        inventory_mode="manual",
        device_inventory_map={f"custom_fields/{cf}": "alias", "name": "name"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["alias"] == "Rack 42"


def test_empty_netbox_field_becomes_empty_inventory_value(
    device_factory, run_sync, zabbix_host
):
    """A NetBox field with no value maps to "", not to None.

    Zabbix rejects a null inventory value, so field_mapper's empty-string
    fallback is what keeps a device with a blank serial syncable at all.
    """
    device = device_factory(address="10.0.0.75/24", serial="")

    run_sync(
        device.name,
        inventory_sync=True,
        inventory_mode="manual",
        device_inventory_map={"serial": "serialno_a", "name": "name"},
    )

    host = zabbix_host(device.name)
    assert host is not None, "a device with an empty serial failed to sync"
    assert host["inventory"]["serialno_a"] == ""
