"""Device sync lifecycle against a real NetBox and a real Zabbix.

Assertions read state back through raw pynetbox / ZabbixAPI calls rather than
through the app, so a bug in host.py cannot mask itself.
"""

from tests.functional.bootstrap.seed_netbox import EXPECTED_HOSTGROUP, ZABBIX_TEMPLATE
from tests.functional.conftest import strip_ip_mask

# Zabbix interface type/port for a device with no `zabbix` config context:
# host.py:496 calls set_default_snmp(). Only VMs default to the agent
# interface, via virtual_machine.py:61.
SNMP_INTERFACE_TYPE = "2"
SNMP_PORT = "161"

ZABBIX_STATUS_ENABLED = "0"


def test_device_created_in_zabbix(device_factory, run_sync, zabbix_host):
    """A seeded device turns into a fully-formed Zabbix host."""
    device = device_factory(address="10.0.0.10/24")

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None, f"no Zabbix host named {device.name} after sync"
    assert host["host"] == device.name
    assert host["status"] == ZABBIX_STATUS_ENABLED

    groups = [group["name"] for group in host["hostgroups"]]
    assert EXPECTED_HOSTGROUP in groups

    templates = [template["host"] for template in host["parentTemplates"]]
    assert ZABBIX_TEMPLATE in templates

    assert len(host["interfaces"]) == 1
    interface = host["interfaces"][0]
    assert interface["type"] == SNMP_INTERFACE_TYPE
    assert interface["port"] == SNMP_PORT
    assert interface["useip"] == "1"
    assert interface["ip"] == strip_ip_mask(str(device.primary_ip4))


def test_hostid_written_back_to_netbox(nb, device_factory, run_sync, zabbix_host):
    """The Zabbix host ID lands in the device's custom field."""
    device = device_factory(address="10.0.0.11/24")

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None

    refreshed = nb.dcim.devices.get(device.id)
    assert refreshed.custom_fields["zabbix_hostid"] == int(host["hostid"])


def test_rerun_is_idempotent(device_factory, run_sync, zabbix_host):
    """A second sync changes nothing -- this is consistency_check's contract."""
    device = device_factory(address="10.0.0.12/24")

    run_sync(device.name)
    first = zabbix_host(device.name)
    assert first is not None

    run_sync(device.name)
    second = zabbix_host(device.name)
    assert second is not None

    assert second["hostid"] == first["hostid"], "device was recreated, not reconciled"

    def snapshot(host):
        return {
            "host": host["host"],
            "name": host["name"],
            "status": host["status"],
            "groups": sorted(group["name"] for group in host["hostgroups"]),
            "templates": sorted(t["host"] for t in host["parentTemplates"]),
            "interfaces": sorted(
                (i["type"], i["ip"], i["dns"], i["port"], i["useip"])
                for i in host["interfaces"]
            ),
        }

    assert snapshot(second) == snapshot(first)
