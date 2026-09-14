"""Which address a Zabbix interface gets, and how many interfaces there are.

Two settings decide this and neither is exercised elsewhere:

- `preferred_ip` picks between a device's v4 and v6 primary. Its three values
  are not symmetric -- "auto" defers to NetBox's own choice, while "ipv4" and
  "ipv6" are preferences with a fallback, not requirements. A dual-stack device
  is the only thing that tells the three apart, so every test here uses one.
- `oob_sync` adds a second Zabbix interface built from the device's OOB IP.

The interface count is asserted as well as its contents. A second interface
that silently fails to appear looks identical to a first interface that is
correct, so `len(host["interfaces"])` is what separates them.
"""

import pytest

from tests.functional.conftest import strip_ip_mask

pytestmark = pytest.mark.functional

SNMP_INTERFACE_TYPE = "2"
AGENT_INTERFACE_TYPE = "1"
IPMI_INTERFACE_TYPE = "3"

# Named so the interface-count assertions read as intent rather than as a
# magic number: "the main one and the OOB one", not "two of something".
MAIN_AND_OOB = 2

V4 = "10.44.0.1/24"
V6 = "2001:db8:44::1/64"

# An OOB interface has to differ in *type* from the main one or the host is
# rejected outright; see test_oob_sync_needs_a_distinct_interface_type.
OOB_AGENT_CONTEXT = {"zabbix": {"oob_interface_type": 1}}


def interface_ips(host: dict) -> set[str]:
    return {interface["ip"] for interface in host["interfaces"]}


def interface_by_type(host: dict, int_type: str) -> dict:
    matches = [i for i in host["interfaces"] if i["type"] == int_type]
    assert len(matches) == 1, f"expected one type-{int_type} interface, got {matches}"
    return matches[0]


def test_auto_defers_to_the_netbox_primary(device_factory, run_sync, zabbix_host):
    """ "auto" reads `nb.primary_ip`, which NetBox resolves to v6 when both exist.

    This is the default, and the behaviour most likely to surprise: setting a v6
    primary in NetBox silently moves an existing Zabbix interface off its v4
    address. Pinned so that stops being silent if it ever changes.
    """
    device = device_factory(address=V4, address6=V6)

    run_sync(device.name)

    interface = zabbix_host(device.name)["interfaces"][0]
    assert interface["ip"] == strip_ip_mask(V6)


def test_ipv4_preference_wins_over_the_netbox_primary(
    device_factory, run_sync, zabbix_host
):
    """The same device as above, with the setting changed and nothing else.

    Paired with the test above deliberately: identical NetBox state, different
    result, so the assertion can only be explained by the setting being read.
    """
    device = device_factory(address=V4, address6=V6)

    run_sync(device.name, preferred_ip="ipv4")

    assert zabbix_host(device.name)["interfaces"][0]["ip"] == strip_ip_mask(V4)


def test_ipv6_preference_selects_the_v6_address(device_factory, run_sync, zabbix_host):
    device = device_factory(address=V4, address6=V6)

    run_sync(device.name, preferred_ip="ipv6")

    assert zabbix_host(device.name)["interfaces"][0]["ip"] == strip_ip_mask(V6)


@pytest.mark.parametrize("preference", ["ipv4", "ipv6"])
def test_a_preference_falls_back_rather_than_failing(
    preference, device_factory, run_sync, zabbix_host
):
    """Neither setting is a requirement: a single-stack device still syncs.

    `preferred_ip` is a preference with an `or` behind it (host.py:169-172), so
    a v6-preferring config pointed at a v4-only estate must not drop every host.
    Both directions are parametrised because only one of them is the code path a
    given reader expects to be safe.
    """
    device = device_factory(address=V4)

    run_sync(device.name, preferred_ip=preference)

    host = zabbix_host(device.name)
    assert host is not None, f"preferred_ip={preference} dropped a v4-only device"
    assert host["interfaces"][0]["ip"] == strip_ip_mask(V4)


def test_oob_interface_absent_by_default(device_factory, run_sync, zabbix_host):
    """The off state: an OOB IP on the device changes nothing while oob_sync is off.

    The device carries a usable OOB IP and a context that would make the second
    interface valid, so the only reason for it not to appear is the setting.
    """
    device = device_factory(
        oob_address="10.44.9.1/24", config_context=OOB_AGENT_CONTEXT
    )

    run_sync(device.name)

    assert len(zabbix_host(device.name)["interfaces"]) == 1


def test_oob_sync_adds_a_second_interface(device_factory, run_sync, zabbix_host):
    """On, with the distinct type it requires: two interfaces, two addresses.

    Both are asserted by type rather than by list position -- Zabbix does not
    promise an order, and asserting `interfaces[1]` would make this test fail
    for a reason that has nothing to do with the setting.
    """
    oob = "10.44.9.2/24"
    device = device_factory(
        address=V4, oob_address=oob, config_context=OOB_AGENT_CONTEXT
    )

    run_sync(device.name, oob_sync=True)

    host = zabbix_host(device.name)
    assert len(host["interfaces"]) == MAIN_AND_OOB
    assert interface_by_type(host, SNMP_INTERFACE_TYPE)["ip"] == strip_ip_mask(V4)
    assert interface_by_type(host, AGENT_INTERFACE_TYPE)["ip"] == strip_ip_mask(oob)
    assert interface_ips(host) == {strip_ip_mask(V4), strip_ip_mask(oob)}


def test_oob_sync_needs_a_distinct_interface_type(
    device_factory, run_sync, zabbix_host
):
    """Enabling oob_sync without an `oob_interface_type` costs the host entirely.

    Both interfaces fall back to `set_default_snmp` (host.py:496), so both are
    type 2; `_verify_interfaces` rejects duplicate types (host.py:458) and the
    host raises `SyncInventoryError`. That is the documented behaviour rather
    than a bug -- Zabbix cannot hold two main interfaces of one type -- but it
    is a sharp edge: turning on one boolean drops every device with an OOB IP
    until a config context is added too.

    Asserting the host is absent rather than the exception: the run continues,
    and the observable cost is the missing host.
    """
    device = device_factory(oob_address="10.44.9.3/24")

    run_sync(device.name, oob_sync=True)

    assert zabbix_host(device.name) is None


def test_oob_sync_is_inert_for_a_device_with_no_oob_ip(
    device_factory, run_sync, zabbix_host
):
    """The guard in host.py:690 -- a device without an OOB IP is untouched.

    Necessary because the setting is global while OOB IPs are per device: if
    this were not inert, enabling oob_sync would break every device that has no
    OOB IP, which is most of them.
    """
    device = device_factory(address=V4)

    run_sync(device.name, oob_sync=True)

    host = zabbix_host(device.name)
    assert host is not None
    assert len(host["interfaces"]) == 1
    assert host["interfaces"][0]["ip"] == strip_ip_mask(V4)


def test_oob_interface_port_comes_from_config_context(
    device_factory, run_sync, zabbix_host
):
    """`oob_interface_port` is read separately from `interface_port`.

    The two share their code path and differ only by key prefix
    (interface.py:39-41), which is exactly the kind of thing that works in one
    direction and not the other. Both are set here, to different values, so a
    swap is visible.

    The `snmp` block is required rather than decorative: naming `interface_type`
    2 explicitly routes through `set_snmp`, which raises unless the context
    carries SNMP parameters (interface.py:120). Leaving the type unset would
    have fallen back to `set_default_snmp` and needed nothing -- so being
    explicit about the type is what makes the details mandatory.
    """
    device = device_factory(
        oob_address="10.44.9.4/24",
        config_context={
            "zabbix": {
                "interface_type": 2,
                "interface_port": 1161,
                "snmp": {"version": 2},
                "oob_interface_type": 1,
                "oob_interface_port": 1050,
            }
        },
    )

    run_sync(device.name, oob_sync=True)

    host = zabbix_host(device.name)
    assert interface_by_type(host, SNMP_INTERFACE_TYPE)["port"] == "1161"
    assert interface_by_type(host, AGENT_INTERFACE_TYPE)["port"] == "1050"


def test_oob_interface_can_be_ipmi(device_factory, run_sync, zabbix_host):
    """The realistic OOB case, and the one the feature exists for.

    A BMC is reached over IPMI, so type 3 on the OOB interface with SNMP on the
    main one is the shape a real user configures. Worth its own test because
    IPMI takes a different branch in `set_interface_details` (host.py:493) than
    the agent type used above.

    Note the `ipmi` block carries no `oob_` prefix. Type and port are read from
    OOB-specific keys, but the credentials block is not: `set_ipmi` reads
    `context["zabbix"]["ipmi"]` regardless of which interface it is building
    (interface.py:134). So a device whose main interface is also IPMI cannot
    give the two different credentials.
    """
    oob = "10.44.9.5/24"
    device = device_factory(
        address=V4,
        oob_address=oob,
        config_context={
            "zabbix": {
                "oob_interface_type": 3,
                "ipmi": {"username": "bmc-user", "password": "bmc-pass"},
            }
        },
    )

    run_sync(device.name, oob_sync=True)

    host = zabbix_host(device.name)
    assert len(host["interfaces"]) == MAIN_AND_OOB
    assert interface_by_type(host, IPMI_INTERFACE_TYPE)["ip"] == strip_ip_mask(oob)
    assert interface_by_type(host, IPMI_INTERFACE_TYPE)["port"] == "623"


def test_turning_oob_sync_off_does_not_remove_an_in_use_interface(
    device_factory, run_sync, zabbix_host
):
    """Disabling the setting is attempted but Zabbix refuses, and that is survivable.

    `consistency_check` does try to delete the now-unwanted interface, but
    Zabbix rejects the call for any interface an item is bound to -- here the
    template's "ICMP ping", which Zabbix attached to the agent interface the
    moment it appeared. The sync logs the rejection (host.py:1235) and carries
    on rather than failing the host.

    So the setting is effectively one-way for a host that has already synced:
    turning `oob_sync` off leaves the extra interface in place. Asserted as
    current behaviour rather than as a bug -- the alternative is for the sync
    to move or delete Zabbix items it did not create -- but it is worth pinning,
    because "the interface is still there" is otherwise indistinguishable from
    the setting having been ignored entirely.
    """
    device = device_factory(
        address=V4, oob_address="10.44.9.6/24", config_context=OOB_AGENT_CONTEXT
    )
    run_sync(device.name, oob_sync=True)
    assert len(zabbix_host(device.name)["interfaces"]) == MAIN_AND_OOB

    run_sync(device.name, oob_sync=False)

    host = zabbix_host(device.name)
    assert len(host["interfaces"]) == MAIN_AND_OOB, "Zabbix started allowing the delete"
    # The host itself is intact: the failed delete cost nothing else.
    assert interface_by_type(host, SNMP_INTERFACE_TYPE)["ip"] == strip_ip_mask(V4)
