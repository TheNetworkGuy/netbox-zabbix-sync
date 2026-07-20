"""Assigning a Zabbix proxy or proxy group, and the four settings behind it.

A proxy is named in NetBox and resolved against Zabbix by name, and the name can
come from three places with a defined precedence:

1. a device custom field, if `proxy_cf` / `proxy_group_cf` names one,
2. that same custom field on the device's *site*, as a per-site default,
3. the device's config context.

On top of that, a proxy group beats a plain proxy on Zabbix 7+, and
`full_proxy_sync` decides whether a proxy set in Zabbix but absent from NetBox
is removed or left alone. That last one is the dangerous setting -- it is the
only one here that deletes configuration -- so both its states are tested.

Every test resolves against a proxy that really exists in Zabbix. Asserting
that the sync sent a name would prove nothing: the whole feature is the lookup.
"""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.functional

# host.monitored_by: 0 = server, 1 = proxy, 2 = proxy group.
MONITORED_BY_SERVER = "0"
MONITORED_BY_PROXY = "1"
MONITORED_BY_PROXY_GROUP = "2"


@pytest.fixture
def proxy_factory(zapi):
    """Create Zabbix proxies and proxy groups, and remove them afterwards.

    Request this fixture *before* `device_factory` in a test signature. Zabbix
    refuses to delete a proxy while a host still points at it, and finalizers
    run in reverse setup order, so first here means the proxies go last.
    """
    proxies = []
    groups = []

    def proxy(name: str | None = None) -> str:
        name = name or f"fn-proxy-{uuid4().hex[:8]}"
        # operating_mode 0 is an active proxy, which needs no address or port:
        # nothing ever connects to it, and the sync only reads its name.
        proxies.append(zapi.proxy.create(name=name, operating_mode=0)["proxyids"][0])
        return name

    def group(name: str | None = None) -> str:
        name = name or f"fn-pgroup-{uuid4().hex[:8]}"
        groups.append(
            zapi.proxygroup.create(name=name, failover_delay="10s", min_online="1")[
                "proxy_groupids"
            ][0]
        )
        return name

    proxy.group = group
    yield proxy

    for proxyid in proxies:
        zapi.proxy.delete(proxyid)
    for groupid in groups:
        zapi.proxygroup.delete(groupid)


@pytest.fixture
def proxy_id(zapi):
    """Resolve a proxy name to its Zabbix id, for comparing against a host."""

    def _get(name: str) -> str:
        found = zapi.proxy.get(filter={"name": name}, output=["proxyid"])
        assert found, f"no Zabbix proxy named {name}"
        return found[0]["proxyid"]

    return _get


def proxy_assignment(host: dict) -> tuple[str, str]:
    """The two fields that together say what is monitoring a host.

    Returned as a pair because neither means anything alone: `proxyid` is "0"
    both when no proxy is set and when a proxy *group* is, so a test asserting
    only on it cannot tell those apart.
    """
    return host["monitored_by"], host.get("proxyid", "0")


def test_proxy_from_config_context(
    proxy_factory, device_factory, run_sync, zabbix_host, zapi
):
    """The baseline: a proxy named in the config context is looked up and set."""
    proxy_name = proxy_factory()
    device = device_factory(config_context={"zabbix": {"proxy": proxy_name}})

    run_sync(device.name)

    monitored_by, proxyid = proxy_assignment(zabbix_host(device.name))
    assert monitored_by == MONITORED_BY_PROXY
    expected = zapi.proxy.get(filter={"name": proxy_name}, output=["proxyid"])
    assert proxyid == expected[0]["proxyid"]


def test_no_proxy_leaves_the_host_on_the_server(device_factory, run_sync, zabbix_host):
    """The off state, and the default: nothing named means nothing assigned."""
    device = device_factory()

    run_sync(device.name)

    assert proxy_assignment(zabbix_host(device.name)) == (MONITORED_BY_SERVER, "0")


def test_proxy_from_a_device_custom_field(
    proxy_factory, custom_field_factory, device_factory, run_sync, zabbix_host
):
    """`proxy_cf` names a custom field, and the field's value names the proxy.

    The setting is off by default (`proxy_cf: False`), so the custom field is
    inert until the config points at it -- which is why this test sets both and
    the test below sets only the field.
    """
    proxy_name = proxy_factory()
    cf_name = custom_field_factory(["dcim.device"])
    device = device_factory()
    device.custom_fields[cf_name] = proxy_name
    device.save()

    run_sync(device.name, proxy_cf=cf_name)

    assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_PROXY


def test_a_custom_field_is_ignored_while_proxy_cf_is_unset(
    proxy_factory, custom_field_factory, device_factory, run_sync, zabbix_host
):
    """The paired off state: identical NetBox data, setting left at its default.

    Without this, the test above would pass for an implementation that read
    every custom field looking for something proxy-shaped.
    """
    proxy_name = proxy_factory()
    cf_name = custom_field_factory(["dcim.device"])
    device = device_factory()
    device.custom_fields[cf_name] = proxy_name
    device.save()

    run_sync(device.name)

    assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_SERVER


def test_proxy_custom_field_falls_back_to_the_site(
    proxy_factory,
    custom_field_factory,
    device_factory,
    run_sync,
    zabbix_host,
    seeded,
    nb,
):
    """A site-level default for every device that does not override it.

    `_set_proxy` checks the device first and the site second (host.py:625-634),
    which is what makes "one proxy per site" expressible without touching every
    device. The device's own field is deliberately left empty here.
    """
    proxy_name = proxy_factory()
    cf_name = custom_field_factory(["dcim.device", "dcim.site"])
    # Refetched rather than reusing the session-scoped Record: that copy keeps
    # whatever custom_fields it was last saved with, and a later save would send
    # a field another test's fixture has since deleted.
    site = nb.dcim.sites.get(seeded["site"].id)
    site.custom_fields[cf_name] = proxy_name
    site.save()
    try:
        device = device_factory()

        run_sync(device.name, proxy_cf=cf_name)

        assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_PROXY
    finally:
        # The site is session-seeded and shared, so the value has to come back
        # off it however this test ends. The custom field itself is torn down
        # by its own fixture, but not before other tests have run.
        site.custom_fields[cf_name] = None
        site.save()


def test_the_device_custom_field_wins_over_the_site(
    proxy_factory,
    custom_field_factory,
    device_factory,
    run_sync,
    zabbix_host,
    seeded,
    nb,
    proxy_id,
):
    """Both levels set, to different proxies, so the precedence is observable."""
    site_proxy = proxy_factory()
    device_proxy = proxy_factory()
    cf_name = custom_field_factory(["dcim.device", "dcim.site"])
    # Refetched rather than reusing the session-scoped Record: that copy keeps
    # whatever custom_fields it was last saved with, and a later save would send
    # a field another test's fixture has since deleted.
    site = nb.dcim.sites.get(seeded["site"].id)
    site.custom_fields[cf_name] = site_proxy
    site.save()
    try:
        device = device_factory()
        device.custom_fields[cf_name] = device_proxy
        device.save()

        run_sync(device.name, proxy_cf=cf_name)

        host = zabbix_host(device.name)
        assert host["monitored_by"] == MONITORED_BY_PROXY
        assert host["proxyid"] != "0"
        assert host["proxyid"] == proxy_id(device_proxy)
    finally:
        site.custom_fields[cf_name] = None
        site.save()


def test_the_custom_field_wins_over_the_config_context(
    proxy_factory, custom_field_factory, device_factory, run_sync, zabbix_host, proxy_id
):
    """Config context is the last resort, not the first (host.py:620-641).

    Guessable either way, so it is pinned: `_set_proxy` only consults the
    config context when the custom field produced nothing.
    """
    cf_proxy = proxy_factory()
    context_proxy = proxy_factory()
    cf_name = custom_field_factory(["dcim.device"])
    device = device_factory(config_context={"zabbix": {"proxy": context_proxy}})
    device.custom_fields[cf_name] = cf_proxy
    device.save()

    run_sync(device.name, proxy_cf=cf_name)

    assert zabbix_host(device.name)["proxyid"] == proxy_id(cf_proxy)


def test_unknown_proxy_name_costs_only_the_proxy_not_the_host(
    device_factory, run_sync, zabbix_host
):
    """A name Zabbix does not have is a warning, and the host syncs anyway.

    This is the right blast radius: a proxy decommissioned in Zabbix but still
    named in NetBox should not stop the host being monitored by the server.
    """
    device = device_factory(config_context={"zabbix": {"proxy": "fn-no-such-proxy"}})

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None, "an unresolvable proxy name dropped the host"
    assert host["monitored_by"] == MONITORED_BY_SERVER


def test_proxy_group_takes_priority_over_a_proxy(
    proxy_factory, device_factory, run_sync, zabbix_host
):
    """Both named on one host: the group wins, because it is the HA option.

    `_set_proxy` puts "proxy_group" at the front of the list it walks on Zabbix
    7+ (host.py:608) and returns on the first match, which is what implements
    the priority.
    """
    proxy_name = proxy_factory()
    group_name = proxy_factory.group()
    device = device_factory(
        config_context={"zabbix": {"proxy": proxy_name, "proxy_group": group_name}}
    )

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host["monitored_by"] == MONITORED_BY_PROXY_GROUP
    assert host["proxyid"] == "0", "a plain proxy was set alongside the group"


def test_proxy_group_from_its_own_custom_field(
    proxy_factory, custom_field_factory, device_factory, run_sync, zabbix_host
):
    """`proxy_group_cf` is a separate setting from `proxy_cf`.

    Set to different field names here so that reading the wrong setting shows
    up as no proxy group at all rather than as an accidental pass.
    """
    group_name = proxy_factory.group()
    group_cf = custom_field_factory(["dcim.device"])
    custom_field_factory(["dcim.device"])
    device = device_factory()
    device.custom_fields[group_cf] = group_name
    device.save()

    run_sync(device.name, proxy_group_cf=group_cf)

    assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_PROXY_GROUP


def test_a_changed_proxy_is_updated_on_the_next_run(
    proxy_factory, device_factory, run_sync, zabbix_host, proxy_id
):
    """Moving a device between proxies in NetBox moves it in Zabbix.

    Independent of `full_proxy_sync`: that setting governs *removal*, not
    reassignment, so this has to work with it off -- which is the default and
    therefore the case most installations are in.
    """
    first = proxy_factory()
    second = proxy_factory()
    device = device_factory(config_context={"zabbix": {"proxy": first}})
    run_sync(device.name)
    assert zabbix_host(device.name)["proxyid"] == proxy_id(first)

    device.local_context_data = {"zabbix": {"proxy": second}}
    device.save()
    run_sync(device.name)

    assert zabbix_host(device.name)["proxyid"] == proxy_id(second)


def test_proxy_is_left_alone_when_full_proxy_sync_is_off(
    proxy_factory, device_factory, run_sync, zabbix_host
):
    """NetBox stops naming a proxy; Zabbix keeps the one it has.

    The default, and the conservative half of the setting: a proxy assigned by
    hand in Zabbix survives a sync that knows nothing about it. Only a warning
    is logged (host.py:1008).
    """
    proxy_name = proxy_factory()
    device = device_factory(config_context={"zabbix": {"proxy": proxy_name}})
    run_sync(device.name)
    assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_PROXY

    device.local_context_data = {}
    device.save()
    run_sync(device.name)

    assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_PROXY, (
        "the proxy was removed even though full_proxy_sync is off"
    )


def test_full_proxy_sync_removes_a_proxy_netbox_no_longer_names(
    proxy_factory, device_factory, run_sync, zabbix_host
):
    """The destructive half, and the reason the setting exists.

    Identical to the test above except for the one boolean, so the difference in
    outcome can only be the setting. This is the only place the sync deletes
    Zabbix configuration it did not just create, which is why both states are
    pinned rather than only this one.
    """
    proxy_name = proxy_factory()
    device = device_factory(config_context={"zabbix": {"proxy": proxy_name}})
    run_sync(device.name, full_proxy_sync=True)
    assert zabbix_host(device.name)["monitored_by"] == MONITORED_BY_PROXY

    device.local_context_data = {}
    device.save()
    run_sync(device.name, full_proxy_sync=True)

    assert proxy_assignment(zabbix_host(device.name)) == (MONITORED_BY_SERVER, "0")
