"""Seed a live NetBox with the objects the sync needs to do anything.

Idempotent: every helper is get-or-create, so running against an
already-seeded NetBox is a no-op and a partially-seeded one is repaired.

Two kinds of object live here. The first is what the sync cannot run without:
the site, manufacturer, device type, role and cluster the default hostgroup
formats name, plus the two custom fields the sync reads and writes. The second
is the furniture every NetBox in production has and a bare test fixture does
not -- a region tree above the site, a site group, a tenant, a platform, and
the handful of custom fields an instance grows once people actually use it.

That second layer is not decoration. Before it syncs anything the sync fetches
*every* text, object and select custom field defined on a device (core.py:283)
and validates the hostgroup format against the names it finds, so an instance
whose only custom fields are the two the sync owns is a case no real user is
in. Seeding a select, a multiselect and an object custom field also puts all
three shapes NetBox serialises a custom field value as -- a bare string, a list
of strings, and a nested object -- on every device the suite syncs.

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
DEVICE_TYPE_PART_NUMBER = "ACME-W1U"
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

# The rest of the site, as somebody running a datacenter would fill it in. None
# of it is required by the sync; it is here so the shipped inventory map has
# something in the same shape as production to walk over.
SITE_DESCRIPTION = "Primary datacenter, Amsterdam"
SITE_FACILITY = "Equinix AM5"
SITE_PHYSICAL_ADDRESS = "Kuiperbergweg 13, 1101 AE Amsterdam, NL"
SITE_TIME_ZONE = "Europe/Amsterdam"

# --- The tree above the site -------------------------------------------------
#
# `region`, `site_group`, `tenant` and `tenant_group` are all valid hostgroup
# format options (tools.py:210), and `traverse_regions` / `traverse_site_groups`
# walk the ancestry of the first two. A site hanging off nothing leaves all of
# that unreachable from the seeded device, so the site is placed in a nested
# region, a site group and a tenant here -- the way a real one would be.
PARENT_REGION_NAME = "Europe"
PARENT_REGION_SLUG = "europe"
REGION_NAME = "Netherlands"
REGION_SLUG = "netherlands"
SITE_GROUP_NAME = "Datacenters"
SITE_GROUP_SLUG = "datacenters"
TENANT_GROUP_NAME = "Business Units"
TENANT_GROUP_SLUG = "business-units"
TENANT_NAME = "Internal IT"
TENANT_SLUG = "internal-it"

# Not put on any device: `platform/name` is in the shipped device_tag_map, and
# the default-map tests assert on a platform they create themselves. This one
# exists because an instance with device types and no platforms is not a shape
# NetBox is ever found in.
PLATFORM_NAME = "Ubuntu 24.04 LTS"
PLATFORM_SLUG = "ubuntu-24-04-lts"

# --- Custom fields -----------------------------------------------------------
#
# Two of these the sync owns; the rest are the ones a NetBox instance
# accumulates on its own -- an environment, a maintenance window, a support
# date, a link to the runbook. They are deliberately one of each type the sync
# can meet: `select` and `object` because core.py fetches exactly
# text/object/select and hands their names to verify_hg_format, `multiselect`
# because it is the one custom field whose value is a list rather than a
# scalar, and `boolean`/`date`/`url` because they are what the inventory and
# usermacro maps are pointed at in practice.
ZABBIX_HOSTID_CF = "zabbix_hostid"
ZABBIX_TEMPLATE_CF = "zabbix_template"
ENVIRONMENT_CF = "environment"
COMPLIANCE_CF = "compliance_frameworks"
MONITORING_ENABLED_CF = "monitoring_enabled"
MAINTENANCE_WINDOW_CF = "maintenance_window"
SUPPORT_EXPIRY_CF = "support_expiry"
DOCUMENTATION_URL_CF = "documentation_url"
SERVICE_OWNER_CF = "service_owner"

# NetBox 3.6 moved the choices of a select field off the field itself and into
# a reusable choice set, so these have to exist before the fields that use them.
ENVIRONMENT_CHOICE_SET = "Environments"
ENVIRONMENT_CHOICES = [
    ["production", "Production"],
    ["staging", "Staging"],
    ["development", "Development"],
]
COMPLIANCE_CHOICE_SET = "Compliance frameworks"
COMPLIANCE_CHOICES = [
    ["iso27001", "ISO 27001"],
    ["pci-dss", "PCI DSS"],
    ["soc2", "SOC 2"],
    ["nis2", "NIS2"],
]

# Defaults NetBox stamps onto every device and VM created after the field
# exists, so a device out of `device_factory` carries a populated select and a
# populated boolean without any test asking for one.
DEFAULT_ENVIRONMENT = "production"
DEFAULT_MONITORING_ENABLED = True

DEVICE_AND_VM = ["dcim.device", "virtualization.virtualmachine"]


def _get_or_create(endpoint, search: dict, create: dict):
    """Return the existing object matching `search`, else create it."""
    existing = endpoint.get(**search)
    if existing:
        return existing
    return endpoint.create(**create)


def _reconcile(record, **wanted) -> None:
    """Bring an already-existing record up to date, and save it if it moved.

    Everything outside a get-or-create's *search* is only applied on create, so
    a NetBox seeded by an older version of this module keeps whatever it was
    created with. The fields that later assertions depend on are therefore set
    through here rather than left to the create call.

    A related object is compared on its id, because NetBox returns a nested
    Record where the caller holds an id; everything else is compared as a
    string, because NetBox returns its decimal fields as strings and the caller
    holds them the same way.
    """
    changed = False
    for name, value in wanted.items():
        current = getattr(record, name)
        current_id = getattr(current, "id", None)
        if current_id is not None:
            if current_id != value:
                setattr(record, name, value)
                changed = True
        elif str(current) != str(value):
            setattr(record, name, value)
            changed = True
    if changed:
        record.save()


def _choice_set(nb, name: str, choices: list[list[str]]):
    """Get-or-create the choice set a select or multiselect field needs."""
    return _get_or_create(
        nb.extras.custom_field_choice_sets,
        {"name": name},
        {
            "name": name,
            "extra_choices": choices,
            # Off so the choices keep the order they are written in above,
            # which is the order they appear in the NetBox UI.
            "order_alphabetically": False,
        },
    )


def seed_custom_fields(nb) -> dict:
    """Create the two custom fields the sync depends on, plus a realistic rest.

    Returns the choice sets, which are the only part of this a caller can need:
    the fields themselves are read off a device, not by id.
    """
    environments = _choice_set(nb, ENVIRONMENT_CHOICE_SET, ENVIRONMENT_CHOICES)
    compliance = _choice_set(nb, COMPLIANCE_CHOICE_SET, COMPLIANCE_CHOICES)

    definitions = [
        {
            "name": ZABBIX_HOSTID_CF,
            "label": "Zabbix host ID",
            "type": "integer",
            # The README claims VMs need a `zabbix_id` field. They do not: the
            # code uses config["device_cf"] for devices and VMs alike.
            "object_types": DEVICE_AND_VM,
            "group_name": "Zabbix",
            "description": "Written back by the sync; not filled in by hand.",
            # The sync owns this value, so keep it out of the clone defaults an
            # operator gets when copying a device in the UI.
            "is_cloneable": False,
        },
        {
            "name": ZABBIX_TEMPLATE_CF,
            "label": "Zabbix template",
            "type": "text",
            "object_types": ["dcim.devicetype"],
            "group_name": "Zabbix",
            "description": "Template linked to every device of this type.",
        },
        {
            "name": ENVIRONMENT_CF,
            "label": "Environment",
            "type": "select",
            "object_types": DEVICE_AND_VM,
            "choice_set": environments.id,
            "default": DEFAULT_ENVIRONMENT,
            "group_name": "Operations",
            "is_cloneable": True,
        },
        {
            "name": COMPLIANCE_CF,
            "label": "Compliance frameworks",
            "type": "multiselect",
            "object_types": DEVICE_AND_VM,
            "choice_set": compliance.id,
            "group_name": "Operations",
            "description": "Regimes this host is in scope for.",
            "is_cloneable": True,
        },
        {
            "name": MONITORING_ENABLED_CF,
            "label": "Monitoring enabled",
            "type": "boolean",
            "object_types": DEVICE_AND_VM,
            "default": DEFAULT_MONITORING_ENABLED,
            "group_name": "Operations",
            "is_cloneable": True,
        },
        {
            "name": MAINTENANCE_WINDOW_CF,
            "label": "Maintenance window",
            "type": "text",
            "object_types": DEVICE_AND_VM,
            "group_name": "Operations",
            "description": "Free-form, e.g. 'Sun 02:00-04:00 UTC'.",
        },
        {
            "name": SERVICE_OWNER_CF,
            "label": "Service owner",
            "type": "object",
            "object_types": DEVICE_AND_VM,
            # The one custom field whose value NetBox serialises as a nested
            # object, which is why `cf_to_string` exists (tools.py:102).
            "related_object_type": "tenancy.tenant",
            "group_name": "Operations",
        },
        {
            "name": SUPPORT_EXPIRY_CF,
            "label": "Support expiry",
            "type": "date",
            "object_types": ["dcim.device"],
            "group_name": "Lifecycle",
            "description": "End of the hardware support contract.",
        },
        {
            "name": DOCUMENTATION_URL_CF,
            "label": "Documentation",
            "type": "url",
            "object_types": DEVICE_AND_VM,
            "group_name": "Lifecycle",
            "description": "Runbook for this host.",
        },
    ]
    for definition in definitions:
        _get_or_create(
            nb.extras.custom_fields, {"name": definition["name"]}, definition
        )

    return {"environments": environments, "compliance": compliance}


def seed(nb) -> dict:
    """Seed NetBox and return the shared objects tests build devices from."""
    choice_sets = seed_custom_fields(nb)

    parent_region = _get_or_create(
        nb.dcim.regions,
        {"slug": PARENT_REGION_SLUG},
        {"name": PARENT_REGION_NAME, "slug": PARENT_REGION_SLUG},
    )
    region = _get_or_create(
        nb.dcim.regions,
        {"slug": REGION_SLUG},
        {"name": REGION_NAME, "slug": REGION_SLUG, "parent": parent_region.id},
    )
    site_group = _get_or_create(
        nb.dcim.site_groups,
        {"slug": SITE_GROUP_SLUG},
        {"name": SITE_GROUP_NAME, "slug": SITE_GROUP_SLUG},
    )
    tenant_group = _get_or_create(
        nb.tenancy.tenant_groups,
        {"slug": TENANT_GROUP_SLUG},
        {"name": TENANT_GROUP_NAME, "slug": TENANT_GROUP_SLUG},
    )
    tenant = _get_or_create(
        nb.tenancy.tenants,
        {"slug": TENANT_SLUG},
        {"name": TENANT_NAME, "slug": TENANT_SLUG, "group": tenant_group.id},
    )

    site = _get_or_create(
        nb.dcim.sites,
        {"slug": SITE_SLUG},
        {
            "name": SITE_NAME,
            "slug": SITE_SLUG,
            "status": "active",
            "latitude": SITE_LATITUDE,
            "longitude": SITE_LONGITUDE,
            "region": region.id,
            "group": site_group.id,
            "tenant": tenant.id,
            "description": SITE_DESCRIPTION,
            "facility": SITE_FACILITY,
            "physical_address": SITE_PHYSICAL_ADDRESS,
            "time_zone": SITE_TIME_ZONE,
        },
    )
    # An earlier run may have created the site with less on it than this seed
    # now wants -- no geo data, or no region -- so the fields the tests read
    # are set unconditionally rather than only on create, the same way the
    # device type's template custom field is below.
    _reconcile(
        site,
        latitude=SITE_LATITUDE,
        longitude=SITE_LONGITUDE,
        region=region.id,
        group=site_group.id,
        tenant=tenant.id,
    )

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
            "part_number": DEVICE_TYPE_PART_NUMBER,
            "u_height": 1,
            "is_full_depth": True,
            "custom_fields": {ZABBIX_TEMPLATE_CF: ZABBIX_TEMPLATE},
        },
    )
    # The custom field may not have existed when an earlier run created the
    # device type, so set it unconditionally rather than only on create.
    if device_type.custom_fields.get(ZABBIX_TEMPLATE_CF) != ZABBIX_TEMPLATE:
        device_type.custom_fields[ZABBIX_TEMPLATE_CF] = ZABBIX_TEMPLATE
        device_type.save()

    role = _get_or_create(
        nb.dcim.device_roles,
        {"slug": ROLE_SLUG},
        {
            "name": ROLE_NAME,
            "slug": ROLE_SLUG,
            "color": "00bcd4",
            # NetBox 4 dropped the separate device_role model, so the same role
            # serves devices and VMs -- but only a role flagged for VMs is
            # offered on one, and `vm_factory` assigns exactly this role.
            "vm_role": True,
            "description": "General purpose server",
        },
    )
    platform = _get_or_create(
        nb.dcim.platforms,
        {"slug": PLATFORM_SLUG},
        {"name": PLATFORM_NAME, "slug": PLATFORM_SLUG},
    )
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
        "parent_region": parent_region,
        "region": region,
        "site_group": site_group,
        "tenant_group": tenant_group,
        "tenant": tenant,
        "platform": platform,
        "choice_sets": choice_sets,
    }
