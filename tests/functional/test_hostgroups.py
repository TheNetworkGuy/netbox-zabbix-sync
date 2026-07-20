"""Hostgroup generation, against real NetBox regions, site groups and CFs.

The hostgroup is the most visible mapping the sync produces -- it is where every
host lands in Zabbix -- and the only functional coverage of it so far is the
default `site/manufacturer/role` in test_device_sync. The parts a mock cannot
vouch for are the ones that read NetBox's own tree shapes:

- **Nested regions and site groups** are walked with `build_path` (tools.py:24),
  which reconstructs the ancestry from a flat recordset using each record's
  `_depth` and `parent` -- fields only a real NetBox populates, and it matches
  ancestors by their *name*.
- **A format segment that is not a built-in option** is looked up as a custom
  field on the host (hostgroups.py:157), or used as a literal if quoted. Whether
  a given custom field is even visible on the object is a NetBox question.

The `traverse_*` flags are switches, so each is paired with its off state: a
flat region name when off, the full path when on.
"""

import contextlib
from uuid import uuid4

import pynetbox
import pytest

from tests.functional.bootstrap import seed_netbox

pytestmark = pytest.mark.functional

ROLE = seed_netbox.ROLE_NAME


@pytest.fixture
def topology(nb):
    """Build region / site-group trees and sites, and tear them down in order.

    Everything created here outlives the per-test device that sits in it, so
    this fixture must be requested *before* device_factory in a test's
    signature: fixtures finalize in reverse setup order, so device_factory then
    finalizes first and removes the device before its site is deleted. Within
    this teardown, reversed creation order deletes children before parents and
    sites before the regions they reference.
    """
    created = []

    class Topology:
        def region(self, name: str | None = None, parent=None):
            suffix = uuid4().hex[:8]
            region = nb.dcim.regions.create(
                name=name or f"reg-{suffix}",
                slug=f"reg-{suffix}",
                parent=parent.id if parent else None,
            )
            created.append(region)
            return region

        def site_group(self, name: str | None = None, parent=None):
            suffix = uuid4().hex[:8]
            group = nb.dcim.site_groups.create(
                name=name or f"sg-{suffix}",
                slug=f"sg-{suffix}",
                parent=parent.id if parent else None,
            )
            created.append(group)
            return group

        def site(self, region=None, group=None):
            suffix = uuid4().hex[:8]
            site = nb.dcim.sites.create(
                name=f"site-{suffix}",
                slug=f"site-{suffix}",
                status="active",
                region=region.id if region else None,
                group=group.id if group else None,
            )
            created.append(site)
            return site

    yield Topology()

    for obj in reversed(created):
        with contextlib.suppress(pynetbox.RequestError):
            obj.delete()


def hostgroup_of(host: dict) -> str:
    """The single hostgroup name the sync built for a host."""
    groups = [group["name"] for group in host["hostgroups"]]
    assert len(groups) == 1, f"expected exactly one hostgroup, got {groups}"
    return groups[0]


def test_region_is_flat_by_default(topology, device_factory, run_sync, zabbix_host):
    """Without traverse_regions, only the site's own region name is used.

    The off state of the switch below. `str(site.region)` is the child region's
    name alone -- NetBox does not fold the ancestry into it -- so the parent
    must be absent here, not merely un-nested.
    """
    parent = topology.region(name=f"EU-{uuid4().hex[:6]}")
    child = topology.region(name=f"NL-{uuid4().hex[:6]}", parent=parent)
    device = device_factory(site=topology.site(region=child).id)

    run_sync(device.name, hostgroup_format="region/role", traverse_regions=False)

    assert hostgroup_of(zabbix_host(device.name)) == f"{child.name}/{ROLE}"


def test_nested_regions_traversed_when_enabled(
    topology, device_factory, run_sync, zabbix_host
):
    """traverse_regions folds the whole ancestry into the hostgroup path."""
    eu = topology.region(name=f"EU-{uuid4().hex[:6]}")
    nl = topology.region(name=f"NL-{uuid4().hex[:6]}", parent=eu)
    ams = topology.region(name=f"AMS-{uuid4().hex[:6]}", parent=nl)
    device = device_factory(site=topology.site(region=ams).id)

    run_sync(device.name, hostgroup_format="region/role", traverse_regions=True)

    assert hostgroup_of(zabbix_host(device.name)) == (
        f"{eu.name}/{nl.name}/{ams.name}/{ROLE}"
    )


def test_nested_site_groups_traversed_when_enabled(
    topology, device_factory, run_sync, zabbix_host
):
    """The same walk over the site-group tree rather than the region tree.

    Worth its own test because it is a second caller of `build_path` reading a
    different NetBox endpoint -- a change to how NetBox serialises site groups
    would break this while leaving the region test green.
    """
    parent = topology.site_group(name=f"PG-{uuid4().hex[:6]}")
    child = topology.site_group(name=f"CG-{uuid4().hex[:6]}", parent=parent)
    device = device_factory(site=topology.site(group=child).id)

    run_sync(device.name, hostgroup_format="site_group/role", traverse_site_groups=True)

    assert hostgroup_of(zabbix_host(device.name)) == (
        f"{parent.name}/{child.name}/{ROLE}"
    )


def test_literal_segment_is_used_verbatim(device_factory, run_sync, zabbix_host):
    """A quoted segment is a literal, not looked up (hostgroups.py:149)."""
    device = device_factory()

    run_sync(device.name, hostgroup_format="'Datacenter'/site/role")

    assert hostgroup_of(zabbix_host(device.name)) == (
        f"Datacenter/{seed_netbox.SITE_NAME}/{ROLE}"
    )


def test_custom_field_segment_resolved_from_the_device(
    device_factory, custom_field_factory, run_sync, zabbix_host
):
    """An unknown segment is resolved as a custom field on the host.

    This is how a site- or tenant-specific grouping that NetBox models as a
    custom field reaches Zabbix. The field has to actually exist and be
    populated on the device, which is why this can only be shown against a real
    NetBox.
    """
    cf = custom_field_factory(object_types=["dcim.device"])
    device = device_factory()
    device.custom_fields[cf] = "Zone-A"
    device.save()

    run_sync(device.name, hostgroup_format=f"site/{cf}/role")

    assert hostgroup_of(zabbix_host(device.name)) == (
        f"{seed_netbox.SITE_NAME}/Zone-A/{ROLE}"
    )


def test_empty_segment_is_skipped(device_factory, run_sync, zabbix_host):
    """A format field with no value drops out rather than leaving a blank.

    The device has no location, so `location` contributes nothing and the
    hostgroup is built from the segments that do. A `//` in a Zabbix hostgroup
    name would be a real, and confusing, group -- so the drop matters.
    """
    device = device_factory()

    run_sync(device.name, hostgroup_format="site/location/role")

    assert hostgroup_of(zabbix_host(device.name)) == (f"{seed_netbox.SITE_NAME}/{ROLE}")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "build_path matches ancestors by name (tools.py:37), but NetBox only "
        "enforces unique region names at the top level -- a name repeated under "
        "two different parents is allowed. build_path then finds two matches, "
        "cannot choose, and returns [] (tools.py:32,35), so the entire region "
        "path is silently dropped and the device lands in the wrong hostgroup "
        "with no error. Remove this marker once build_path disambiguates by "
        "parent/id rather than name."
    ),
)
def test_ambiguous_region_name_still_traverses(
    topology, device_factory, run_sync, zabbix_host
):
    """A region name reused under two parents must not lose the path.

    The device's region is unambiguous *in context* -- it has one parent -- but
    build_path only has the name to go on. The correct hostgroup is the full
    ancestry; today the sync produces just the role.
    """
    eu = topology.region(name=f"EU-{uuid4().hex[:6]}")
    us = topology.region(name=f"US-{uuid4().hex[:6]}")
    shared = f"North-{uuid4().hex[:6]}"
    north_eu = topology.region(name=shared, parent=eu)
    topology.region(name=shared, parent=us)
    device = device_factory(site=topology.site(region=north_eu).id)

    run_sync(device.name, hostgroup_format="region/role", traverse_regions=True)

    assert hostgroup_of(zabbix_host(device.name)) == (f"{eu.name}/{shared}/{ROLE}")
