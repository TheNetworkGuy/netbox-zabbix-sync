"""Custom field values as NetBox actually serialises them, per version.

Captured from live NetBox v4.6.10 and v4.7.0 containers (the versions in the
functional test matrix) by reading back a device whose custom fields were all
set to the same input. They are kept verbatim rather than hand-written so a
unit test built on them is testing the shape NetBox really sends.

The only difference between the two versions is the choice fields. NetBox 4.7
returns a `select` value as a `{"value", "label"}` dict and a `multiselect`
as a list of those, where 4.6 returned the bare value(s). Every other type is
identical. Note the asymmetry with writes: 4.7 rejects that dict as input with a
400 and only accepts the bare value, so a record read from 4.7 cannot be saved
back unchanged unless pynetbox flattens it (see test_custom_fields.py).

The sync resolves a choice to its `value` on every version, so the same NetBox
data produces the same hostgroup, proxy and template names before and after an
upgrade to 4.7. `EXPECTED` holds that one version-independent result.
"""

import pytest

TENANT = {
    "id": 1,
    "url": "http://localhost:8000/api/tenancy/tenants/1/",
    "display": "Internal IT",
    "name": "Internal IT",
    "slug": "internal-it",
    "description": "",
}

CUSTOM_FIELDS = {
    "4.6": {
        "text": "hello",
        "integer": 7,
        "boolean": True,
        "select": "staging",
        "multiselect": ["iso27001", "soc2"],
        "object": TENANT,
        "multiobject": [TENANT],
    },
    "4.7": {
        "text": "hello",
        "integer": 7,
        "boolean": True,
        "select": {"value": "staging", "label": "Staging"},
        "multiselect": [
            {"value": "iso27001", "label": "ISO 27001"},
            {"value": "soc2", "label": "SOC 2"},
        ],
        "object": TENANT,
        "multiobject": [TENANT],
    },
}

# What a consumer that turns a custom field into a name (hostgroup segment,
# proxy, template) should get out, regardless of the NetBox version.
EXPECTED = {
    "text": "hello",
    "select": "staging",
    "object": "Internal IT",
}

NETBOX_VERSIONS = sorted(CUSTOM_FIELDS)


def cf_value(version: str, cf_type: str):
    """The value of a `cf_type` custom field as `version` serialises it."""
    return CUSTOM_FIELDS[version][cf_type]


def cf_cases(*cf_types: str) -> list:
    """`(value, cf_type)` params for each type, once per NetBox version.

    The ids read `4.7-select` and so on, so a failure names the version.
    """
    return [
        pytest.param(cf_value(version, cf_type), cf_type, id=f"{version}-{cf_type}")
        for cf_type in cf_types
        for version in NETBOX_VERSIONS
    ]


def device_payload(version: str) -> dict:
    """A trimmed device detail response carrying every custom field type.

    Enough of a real response for pynetbox to build a Record from, which is
    what the write-back contract test needs: the fields pynetbox serialises on
    `save()` are exactly the ones present here.
    """
    return {
        "id": 1,
        "url": "http://localhost:8000/api/dcim/devices/1/",
        "display": "probe",
        "name": "probe",
        "status": {"value": "active", "label": "Active"},
        "custom_fields": {
            "zabbix_hostid": None,
            **{f"cf_{t}": v for t, v in CUSTOM_FIELDS[version].items()},
        },
    }
