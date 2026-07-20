"""Seed a live NetBox with the objects the sync needs to do anything.

Idempotent: every helper is get-or-create, so running against an
already-seeded NetBox is a no-op and a partially-seeded one is repaired.

Only the shared, session-scoped objects live here. Devices are per-test and
come from the ``device_factory`` fixture, so each test can be isolated to its
own device via ``Sync.start(device_filter=...)``.
"""

# The default hostgroup_format is "site/manufacturer/role", so a device needs
# all three of these set or the sync skips it without syncing anything.
SITE_NAME = "AMS-01"
SITE_SLUG = "ams-01"
MANUFACTURER_NAME = "Acme"
MANUFACTURER_SLUG = "acme"
DEVICE_TYPE_MODEL = "Widget-1U"
DEVICE_TYPE_SLUG = "widget-1u"
ROLE_NAME = "Server"
ROLE_SLUG = "server"

# Ships with the Zabbix server image's default dataset. Must be an SNMP
# template: a device with no `zabbix` config context gets an SNMP interface
# (host.py:496), and Zabbix refuses to link an agent template to a host that
# has no agent interface.
ZABBIX_TEMPLATE = "Linux by SNMP"

# The mirror image of the above, and for the mirrored reason: a VM gets an
# AGENT interface by default (virtual_machine.py:set_interface_details, "agent
# type interfaces are more likely to be used with VMs"), so linking the SNMP
# template to a VM is the same error in the other direction.
ZABBIX_VM_TEMPLATE = "Linux by Zabbix agent"

# The default vm_hostgroup_format is "cluster_type/cluster/role", so a VM needs
# a cluster -- which carries the type -- and a role, the way a device needs a
# site, manufacturer and role.
CLUSTER_TYPE_NAME = "Proxmox"
CLUSTER_TYPE_SLUG = "proxmox"
CLUSTER_NAME = "cluster-01"

# The hostgroup the sync should build for a seeded device, given the defaults
# above and hostgroup_format "site/manufacturer/role".
EXPECTED_HOSTGROUP = f"{SITE_NAME}/{MANUFACTURER_NAME}/{ROLE_NAME}"

# The same for a seeded VM, given vm_hostgroup_format "cluster_type/cluster/role".
EXPECTED_VM_HOSTGROUP = f"{CLUSTER_TYPE_NAME}/{CLUSTER_NAME}/{ROLE_NAME}"

# Geo data on the site. NetBox does not include these in the *nested* site it
# returns on a device, so they are only reachable once `extended_site_properties`
# has called full_details() -- which is exactly what the extended_site_properties
# tests use them to prove. Stored as strings because that is how NetBox returns
# these decimal fields, and field_mapper str()s whatever it finds.
SITE_LATITUDE = "52.370216"
SITE_LONGITUDE = "4.895168"


def _get_or_create(endpoint, search: dict, create: dict):
    """Return the existing object matching `search`, else create it."""
    existing = endpoint.get(**search)
    if existing:
        return existing
    return endpoint.create(**create)


def seed_custom_fields(nb) -> None:
    """Create the two custom fields the sync depends on."""
    _get_or_create(
        nb.extras.custom_fields,
        {"name": "zabbix_hostid"},
        {
            "name": "zabbix_hostid",
            "label": "Zabbix host ID",
            "type": "integer",
            # The README claims VMs need a `zabbix_id` field. They do not: the
            # code uses config["device_cf"] for devices and VMs alike.
            "object_types": ["dcim.device", "virtualization.virtualmachine"],
        },
    )
    _get_or_create(
        nb.extras.custom_fields,
        {"name": "zabbix_template"},
        {
            "name": "zabbix_template",
            "label": "Zabbix template",
            "type": "text",
            "object_types": ["dcim.devicetype"],
        },
    )


def seed(nb) -> dict:
    """Seed NetBox and return the shared objects tests build devices from."""
    seed_custom_fields(nb)

    site = _get_or_create(
        nb.dcim.sites,
        {"slug": SITE_SLUG},
        {
            "name": SITE_NAME,
            "slug": SITE_SLUG,
            "status": "active",
            "latitude": SITE_LATITUDE,
            "longitude": SITE_LONGITUDE,
        },
    )
    # An earlier run may have created the site without geo data, so set it
    # unconditionally rather than only on create -- same reasoning as the
    # device type's template custom field below.
    if str(site.latitude) != SITE_LATITUDE or str(site.longitude) != SITE_LONGITUDE:
        site.latitude = SITE_LATITUDE
        site.longitude = SITE_LONGITUDE
        site.save()
    manufacturer = _get_or_create(
        nb.dcim.manufacturers,
        {"slug": MANUFACTURER_SLUG},
        {"name": MANUFACTURER_NAME, "slug": MANUFACTURER_SLUG},
    )
    device_type = _get_or_create(
        nb.dcim.device_types,
        {"slug": DEVICE_TYPE_SLUG},
        {
            "model": DEVICE_TYPE_MODEL,
            "slug": DEVICE_TYPE_SLUG,
            "manufacturer": manufacturer.id,
            "custom_fields": {"zabbix_template": ZABBIX_TEMPLATE},
        },
    )
    # The custom field may not have existed when an earlier run created the
    # device type, so set it unconditionally rather than only on create.
    if device_type.custom_fields.get("zabbix_template") != ZABBIX_TEMPLATE:
        device_type.custom_fields["zabbix_template"] = ZABBIX_TEMPLATE
        device_type.save()

    role = _get_or_create(
        nb.dcim.device_roles,
        {"slug": ROLE_SLUG},
        {"name": ROLE_NAME, "slug": ROLE_SLUG, "color": "00bcd4"},
    )
    # The same role serves devices and VMs: NetBox 4 dropped the separate
    # device_role model, so `role` on both points at dcim.device_roles.
    cluster_type = _get_or_create(
        nb.virtualization.cluster_types,
        {"slug": CLUSTER_TYPE_SLUG},
        {"name": CLUSTER_TYPE_NAME, "slug": CLUSTER_TYPE_SLUG},
    )
    cluster = _get_or_create(
        nb.virtualization.clusters,
        {"name": CLUSTER_NAME},
        {"name": CLUSTER_NAME, "type": cluster_type.id, "status": "active"},
    )

    return {
        "site": site,
        "manufacturer": manufacturer,
        "device_type": device_type,
        "role": role,
        "cluster_type": cluster_type,
        "cluster": cluster,
    }
