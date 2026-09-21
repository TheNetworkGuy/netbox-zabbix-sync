"""The extended_* settings, which change what NetBox returns rather than what we do.

NetBox serves related objects on a device in a *nested* form carrying only a
handful of fields. `extended_site_properties`, `extended_virtual_chassis` and
`extended_ips` each call `full_details()` on one of them, trading extra API
requests for the rest of the fields -- which then become available to the
inventory, usermacro and tag maps.

This is the one group of features a mocked test cannot express an opinion
about, in either direction:

- A DummyNB has whatever fields the test gave it, so a mocked map over
  `site/latitude` passes whether or not the flag is on. The flag's entire
  effect is on data that arrives from NetBox.
- Which fields are nested and which are not is NetBox's decision, and it
  changes between versions. `primary_ip4.dns_name`, for one, is nested
  already -- so extending IPs is *not* what makes DNS available, despite that
  being the documented reason for the setting.

Two behaviours found while writing these are worth knowing before reading on,
because they are why the tests are not the symmetrical off/on pairs you would
expect. Both come from one pynetbox detail: a nested `Record` lazily fetches
its full details on *attribute* access (`site.latitude`), but `Record[key]`
is `dict(self)[key]` and does no such thing.

1. `field_mapper` walks with `value[item]` (tools.py:130), so a mapped field
   NetBox did not nest raises `KeyError` rather than mapping to "". It escapes
   `start()`, which only catches `SyncError`, and takes the whole run down --
   see `test_unextended_mapped_field_aborts_the_run`.
2. `extended_site_properties` is redundant for devices: `Hostgroup` reads
   `self.nb.site.region` (hostgroups.py:64) for every device with a site,
   which lazily fetches the site anyway -- see
   `test_site_is_fetched_even_without_extended_site_properties`.
"""

from urllib.parse import urlparse

import pytest

from tests.functional.bootstrap.seed_netbox import SITE_LATITUDE, SITE_LONGITUDE
from tests.functional.conftest import macro_values

INVENTORY_MANUAL_CONFIG = {"inventory_sync": True, "inventory_mode": "manual"}

# Devices synced by test_site_costs_one_fetch_per_host_either_way, which
# expects the site to be fetched exactly once for each of them.
SYNCED_HOSTS = 2

# field_mapper indexes rather than getattrs, so it never triggers pynetbox's
# lazy fetch and a field that was not nested is simply missing. The KeyError
# that follows is not caught anywhere: start() handles SyncError only, so one
# such field in the map ends the entire run, not just that host. Mapping an
# absent field is already handled for empty values (tools.py:135) -- an absent
# key looks like the same intent.
UNEXTENDED_FIELD_BUG = (
    "field_mapper raises KeyError on a mapped field that NetBox did not nest, "
    "aborting the whole run instead of mapping it to an empty string "
    "(tools.py:130). Remove this marker once it maps to '' like an empty value."
)


def site_detail_requests(exchanges, site_id: int):
    """The recorded requests that fetched this site's full details."""
    path = f"/api/dcim/sites/{site_id}/"
    return [ex for ex in exchanges if urlparse(ex.url).path == path]


# --- the mapped-but-absent field --------------------------------------------


@pytest.mark.xfail(strict=True, reason=UNEXTENDED_FIELD_BUG)
def test_unextended_mapped_field_aborts_the_run(
    device_factory, virtual_chassis_factory, run_sync, zabbix_host
):
    """A field that needs extending, mapped without it, should be skipped.

    `virtual_chassis/domain` is not on the nested chassis, and the flag that
    would fetch it is off -- the same shape as a user who copied a map out of
    the wiki and forgot the setting, or who upgraded into a NetBox that nests
    one field fewer.

    What should happen is what happens for a field that is present but empty:
    an empty inventory value, host synced. What happens instead is a KeyError
    out of `Sync.start()` that ends the run, so every host queued behind this
    one goes unsynced too.
    """
    device = device_factory(address="10.0.0.130/24")
    virtual_chassis_factory(device, domain="chassis.example.com")

    run_sync(
        device.name,
        **INVENTORY_MANUAL_CONFIG,
        device_inventory_map={"virtual_chassis/domain": "chassis", "name": "name"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["chassis"] == ""


# --- extended_site_properties -----------------------------------------------


def test_site_is_fetched_even_without_extended_site_properties(
    device_factory, seeded, sync_runner, netbox_requests, zabbix_host
):
    """The flag is redundant for devices: the site is fetched either way.

    `Hostgroup` reads `self.nb.site.region` for every device that has a site
    (hostgroups.py:63-71), and that attribute is not nested, so pynetbox
    fetches the full site to answer it. By the time the inventory map runs, the
    geo data is already on the record.

    So the config file's warning that this setting "will increase the number of
    API queries" does not hold for devices, and neither does needing it to map
    `site/latitude`. Pinned rather than left implicit because the day pynetbox
    stops lazy-loading, every user mapping site geo without the flag starts
    hitting the KeyError above -- and this test is where that shows up.
    """
    device = device_factory(address="10.0.0.100/24")

    sync_runner(
        device_filter={"name": device.name},
        **INVENTORY_MANUAL_CONFIG,
        extended_site_properties=False,
        device_inventory_map={"site/latitude": "location_lat", "name": "name"},
    )

    assert site_detail_requests(netbox_requests, seeded["site"].id), (
        "the site was not fetched; the lazy load this test documents is gone "
        "and mapping site/latitude without the flag now raises KeyError"
    )
    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["location_lat"] == SITE_LATITUDE


def test_site_geo_synced_with_extended_site_properties(
    device_factory, run_sync, zabbix_host
):
    """With the flag on, the site's geo data reaches Zabbix inventory.

    This is the documented reason the setting exists: the default inventory map
    ships `latitude`/`longitude` commented out in favour of `site/latitude`.
    """
    device = device_factory(address="10.0.0.101/24")

    run_sync(
        device.name,
        **INVENTORY_MANUAL_CONFIG,
        extended_site_properties=True,
        device_inventory_map={
            "site/latitude": "location_lat",
            "site/longitude": "location_lon",
            "name": "name",
        },
    )

    host = zabbix_host(device.name)
    assert host is not None
    inventory = host["inventory"]
    assert inventory["location_lat"] == SITE_LATITUDE
    assert inventory["location_lon"] == SITE_LONGITUDE


@pytest.mark.parametrize("extended", [False, True], ids=["flag-off", "flag-on"])
def test_site_costs_one_fetch_per_host_either_way(
    device_factory, seeded, sync_runner, netbox_requests, zabbix_host, extended
):
    """The site is fetched once per host whether the flag is set or not.

    The measured cost of `extended_site_properties` on devices, and it is
    nothing: two devices, two site fetches, with the flag off and with it on.
    Each device deserialises its own site Record, so `has_details` is not
    shared and there is no run-level caching to lose -- the flag simply moves
    who triggers the fetch, from `Hostgroup` to `core.start`.

    Which means the config file's warning that this setting "will increase the
    number of API queries" does not describe devices. It is also the N+1 this
    would need if the fetch ever became conditional: one site, one request per
    host.
    """
    first = device_factory(address="10.0.0.102/24")
    second = device_factory(address="10.0.0.103/24")

    sync_runner(
        device_filter={"name": [first.name, second.name]},
        **INVENTORY_MANUAL_CONFIG,
        extended_site_properties=extended,
        device_inventory_map={"site/latitude": "location_lat", "name": "name"},
    )

    assert zabbix_host(first.name) is not None
    assert zabbix_host(second.name) is not None
    assert len(site_detail_requests(netbox_requests, seeded["site"].id)) == SYNCED_HOSTS


# --- extended_virtual_chassis -----------------------------------------------


def test_virtual_chassis_name_needs_no_extension(
    device_factory, virtual_chassis_factory, run_sync, zabbix_host
):
    """The default map's `virtual_chassis/name` works with the flag off.

    Worth pinning: it means the default configuration is not silently relying
    on a setting that defaults to False. `name` is nested; `domain` is not,
    which is the next test.
    """
    device = device_factory(address="10.0.0.110/24")
    vc = virtual_chassis_factory(device, domain="chassis.example.com")

    run_sync(device.name, **INVENTORY_MANUAL_CONFIG)

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["chassis"] == vc.name


def test_chassis_domain_synced_with_extended_virtual_chassis(
    device_factory, virtual_chassis_factory, run_sync, zabbix_host
):
    """extended_virtual_chassis makes the chassis' own fields mappable."""
    device = device_factory(address="10.0.0.112/24")
    virtual_chassis_factory(device, domain="chassis.example.com")

    run_sync(
        device.name,
        **INVENTORY_MANUAL_CONFIG,
        extended_virtual_chassis=True,
        device_inventory_map={"virtual_chassis/domain": "chassis", "name": "name"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["chassis"] == "chassis.example.com"


def test_chassis_members_available_to_jinja_when_extended(
    device_factory, virtual_chassis_factory, sync_runner, zabbix_host
):
    """The extended chassis carries its members, which Jinja can walk.

    `members` is absent from the nested chassis entirely, so this is the
    feature at its most useful: building a macro out of NetBox relations that
    the device itself does not carry. core.py:404-407 extends each member too,
    which is what lets the template reach a member's own fields.
    """
    master = device_factory(
        address="10.0.0.113/24",
        config_context={
            "zabbix": {
                "usermacros": {
                    "{$CHASSIS_MEMBERS}": "{{ data.virtual_chassis.members | length }}"
                }
            }
        },
    )
    member = device_factory(address="10.0.0.114/24")
    virtual_chassis_factory(master, member)

    sync_runner(
        device_filter={"name": master.name},
        extended_virtual_chassis=True,
        render_config_context=True,
        usermacro_sync=True,
        device_usermacro_map={},
    )

    host = zabbix_host(master.name)
    assert host is not None
    assert macro_values(host)["{$CHASSIS_MEMBERS}"] == "2"


def test_clustering_promotes_master_to_chassis_name(
    device_factory, virtual_chassis_factory, sync_runner, zabbix_host
):
    """With clustering on, the chassis syncs as one host named for the chassis.

    A stack of switches is one monitored thing, not N. The master is promoted
    to the chassis name (device.py:57) and the secondary member gets no host of
    its own -- assert both, since a sync that created nothing would satisfy
    only the second.
    """
    master = device_factory(address="10.0.0.115/24")
    member = device_factory(address="10.0.0.116/24")
    vc = virtual_chassis_factory(master, member)

    sync_runner(device_filter={"name": [master.name, member.name]}, clustering=True)

    assert zabbix_host(vc.name) is not None, "master was not promoted to chassis name"
    assert zabbix_host(master.name) is None, "master synced under its own name too"
    assert zabbix_host(member.name) is None, "secondary member got its own host"


# --- extended_ips -----------------------------------------------------------


def test_ip_status_synced_with_extended_ips(device_factory, run_sync, zabbix_host):
    """extended_ips exposes the IP's own fields to the maps.

    `status` is not nested on an IP, so this needs the flag -- unlike
    `dns_name`, which is nested and needs nothing, despite DNS being the
    documented reason for the setting. Without the flag this map does not
    yield "" but raises; that is `test_unextended_mapped_field_aborts_the_run`.
    """
    device = device_factory(address="10.0.0.121/24")

    run_sync(
        device.name,
        **INVENTORY_MANUAL_CONFIG,
        extended_ips=True,
        device_inventory_map={"primary_ip4/status/label": "notes", "name": "name"},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["notes"] == "Active"


def test_oob_ip_address_needs_no_extension(device_factory, run_sync, zabbix_host):
    """The default map's `oob_ip/address` works without extended_ips.

    The nested IP carries its address, so the default inventory map is not
    quietly dependent on a flag that defaults to False.
    """
    device = device_factory(address="10.0.0.122/24", oob_address="192.168.1.122/24")

    run_sync(device.name, **INVENTORY_MANUAL_CONFIG)

    host = zabbix_host(device.name)
    assert host is not None
    assert host["inventory"]["oob_ip"] == "192.168.1.122/24"


def test_dns_name_used_for_interface_when_preferred(
    device_factory, run_sync, zabbix_host
):
    """prefer_dns points the Zabbix interface at the IP's DNS name.

    Included here because it is the behaviour extended_ips is documented to
    enable, and it turns out to need neither the flag nor an extra request:
    dns_name is nested. The interface flips to useip=0 and Zabbix monitors the
    name (interface.py:17).
    """
    device = device_factory(address="10.0.0.123/24", dns_name="host123.example.com")

    run_sync(device.name, prefer_dns=True)

    host = zabbix_host(device.name)
    assert host is not None
    interface = host["interfaces"][0]
    assert interface["useip"] == "0", "interface still uses the IP despite prefer_dns"
    assert interface["dns"] == "host123.example.com"
