"""Every place the sync reads a custom field, fed what each NetBox version sends.

NetBox 4.7 changed how choice custom fields are returned (see
netbox_payloads.py), and the sync reads custom fields in several unrelated
places. Each is tested here against the captured payload of every version in
the functional matrix, and each asserts the same result for all of them: a
NetBox upgrade must not change which hostgroup, proxy or template a host gets.

The consumers:

- a hostgroup format segment that is not a built-in option (hostgroups.py)
- `proxy_cf` / `proxy_group_cf`, on the device and then on its site (host.py)
- `template_cf` on the device type (host.py)
- `custom_fields/...` paths in the inventory, tag and usermacro maps, which all
  go through `field_mapper` and are tested with it in test_tools.py
- the `device_cf` write-back, which goes through pynetbox's `save()`

Only text, select and object are tested as names: those are the types
core.py fetches for `verify_hg_format`, so the only ones a hostgroup format can
name, and the ones documented for the proxy settings.
"""

from unittest.mock import MagicMock

import pynetbox
import pytest
from pynetbox.core.response import Record

from netbox_zabbix_sync.modules.device import PhysicalDevice
from netbox_zabbix_sync.modules.hostgroups import Hostgroup
from tests.netbox_payloads import (
    EXPECTED,
    NETBOX_VERSIONS,
    cf_cases,
    device_payload,
)

NAMED_TYPES = ("text", "select", "object")

ZABBIX_HOSTID = 42


def nb_device(custom_fields=None, site_custom_fields=None, type_custom_fields=None):
    """A NetBox device mock carrying the given custom fields."""
    nb = MagicMock()
    nb.id = 1
    nb.name = "test-device"
    nb.status.label = "Active"
    nb.config_context = {}
    nb.oob_ip = None
    nb.primary_ip = nb.primary_ip4 = MagicMock(address="192.168.1.1/24")
    nb.primary_ip6 = None
    nb.custom_fields = {"zabbix_hostid": None, **(custom_fields or {})}
    nb.site.name = "AMS-01"
    nb.site.custom_fields = site_custom_fields or {}
    nb.role.name = "Server"
    nb.device_type.custom_fields = type_custom_fields or {}
    return nb


def physical_device(nb, **config):
    zabbix = MagicMock()
    zabbix.version = 7.0
    return PhysicalDevice(
        nb,
        zabbix,
        MagicMock(),
        "4.7",
        logger=MagicMock(),
        config={
            "device_cf": "zabbix_hostid",
            "preferred_ip": "auto",
            "prefer_dns": False,
            "proxy_cf": False,
            "proxy_group_cf": False,
            **config,
        },
    )


def proxies(*names, proxy_type="proxy"):
    """Proxies in the shape proxy_prepper hands to `_set_proxy`."""
    return [
        {"name": name, "type": proxy_type, "id": str(i), "monitored_by": 1}
        for i, name in enumerate(names, start=1)
    ]


class TestHostgroupSegment:
    """A custom field named in the hostgroup format contributes its value."""

    @pytest.mark.parametrize(("value", "cf_type"), cf_cases(*NAMED_TYPES))
    def test_segment_resolves_to_the_same_name_on_every_version(self, value, cf_type):
        nb = nb_device(custom_fields={"zone": value})

        hostgroup = Hostgroup("dev", nb, "4.7", MagicMock()).generate("site/zone/role")

        assert hostgroup == f"AMS-01/{EXPECTED[cf_type]}/Server"


class TestProxyCustomField:
    """`proxy_cf` / `proxy_group_cf` name a custom field holding the proxy name."""

    @pytest.mark.parametrize(("value", "cf_type"), cf_cases(*NAMED_TYPES))
    def test_proxy_from_the_device(self, value, cf_type):
        device = physical_device(nb_device({"proxy": value}), proxy_cf="proxy")

        assert device._set_proxy(proxies("decoy", EXPECTED[cf_type]))
        assert device.zbxproxy["name"] == EXPECTED[cf_type]

    @pytest.mark.parametrize(("value", "cf_type"), cf_cases(*NAMED_TYPES))
    def test_proxy_falls_back_to_the_site(self, value, cf_type):
        nb = nb_device(site_custom_fields={"proxy": value})
        device = physical_device(nb, proxy_cf="proxy")

        assert device._set_proxy(proxies("decoy", EXPECTED[cf_type]))
        assert device.zbxproxy["name"] == EXPECTED[cf_type]

    @pytest.mark.parametrize(("value", "cf_type"), cf_cases(*NAMED_TYPES))
    def test_proxy_group_from_the_device(self, value, cf_type):
        device = physical_device(nb_device({"pg": value}), proxy_group_cf="pg")
        groups = proxies("decoy", EXPECTED[cf_type], proxy_type="proxy_group")

        assert device._set_proxy(groups)
        assert device.zbxproxy["name"] == EXPECTED[cf_type]


class TestTemplateCustomField:
    """`template_cf` names a device type custom field holding the template."""

    @pytest.mark.parametrize(("value", "cf_type"), cf_cases("text", "select"))
    def test_template_name_is_the_same_on_every_version(self, value, cf_type):
        nb = nb_device(type_custom_fields={"zabbix_template": value})
        device = physical_device(nb, template_cf="zabbix_template")

        assert device._get_templates_cf() == [EXPECTED[cf_type]]


class TestWriteBack:
    """The host ID write-back must survive a device with choice fields set.

    `create_in_zabbix` writes the Zabbix host ID into `device_cf` and calls
    `save()`, NetBox 4.7 returns read-only dicts for choice fields, which need to be handled by pynetbox.
    """

    @pytest.mark.parametrize("version", NETBOX_VERSIONS)
    def test_save_payload_has_the_bare_values_netbox_accepts(self, version):
        # A real api object for URL parsing; nothing here touches the network.
        api = pynetbox.api("http://localhost:8000")
        record = Record(device_payload(version), api, api.dcim.devices)
        record.custom_fields["zabbix_hostid"] = ZABBIX_HOSTID

        sent = record.serialize()["custom_fields"]

        assert sent["zabbix_hostid"] == ZABBIX_HOSTID
        assert sent["cf_select"] == "staging"
        assert sent["cf_multiselect"] == ["iso27001", "soc2"]
        assert sent["cf_object"] == 1
