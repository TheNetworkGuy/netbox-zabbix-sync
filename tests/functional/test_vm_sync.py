"""Virtual machine sync: the VM maps, and where VMs diverge from devices.

A VM is not a device with a different endpoint. It takes a different branch in
`start()` (core.py:348), a different class, and four different config keys --
`vm_inventory_map`, `vm_usermacro_map`, `vm_tag_map` and `vm_hostgroup_format`.
Every one of those is a separate mapping that no device test can reach.

Two of the divergences are the kind only a real Zabbix will fail on:

- **A VM defaults to an agent interface**, where a device defaults to SNMP
  (virtual_machine.py:44 against host.py:496). Zabbix refuses to link an agent
  template to a host with no agent interface and vice versa, so the interface
  default and the template have to agree -- and the seeded VM template is an
  agent one for exactly that reason.
- **A VM's templates come only from its config context.** `set_vm_template`
  skips the custom field lookup devices use, so a VM with no `zabbix` context is
  dropped by core.py:371 before Zabbix sees it. That is why `vm_factory` gives
  every VM a context by default; see `vm_context` in conftest.
"""

import pytest

from tests.functional.bootstrap import seed_netbox
from tests.functional.conftest import (
    macro_values,
    strip_ip_mask,
    tag_pairs,
    vm_context,
)

pytestmark = pytest.mark.functional

ZABBIX_AGENT_INTERFACE = "1"
ZABBIX_SNMP_INTERFACE = "2"
AGENT_PORT = "10050"

INVENTORY_MANUAL_CONFIG = {"inventory_sync": True, "inventory_mode": "manual"}


def test_vm_created_in_zabbix(vm_factory, run_vm_sync, zabbix_host):
    vm = vm_factory()

    run_vm_sync(vm.name)

    host = zabbix_host(vm.name)
    assert host is not None, "the VM did not reach Zabbix"
    assert [t["host"] for t in host["parentTemplates"]] == [
        seed_netbox.ZABBIX_VM_TEMPLATE
    ]


def test_vm_gets_an_agent_interface_by_default(vm_factory, run_vm_sync, zabbix_host):
    """The VM default, and the opposite of the device default.

    Asserting the port as well as the type: an agent interface on the SNMP port
    would still report type 1 while talking to nothing.
    """
    vm = vm_factory(address="10.1.0.5/24")

    run_vm_sync(vm.name)

    interfaces = zabbix_host(vm.name)["interfaces"]
    assert [i["type"] for i in interfaces] == [ZABBIX_AGENT_INTERFACE]
    assert interfaces[0]["ip"] == strip_ip_mask("10.1.0.5/24")
    assert interfaces[0]["port"] == AGENT_PORT


def test_vm_snmp_interface_from_config_context(vm_factory, run_vm_sync, zabbix_host):
    """A config context still overrules the agent default (virtual_machine.py:54).

    The template has to change with it: Zabbix rejects the agent template on a
    host whose only interface is SNMP, so this doubles as proof that the two
    halves of the context are applied together.
    """
    vm = vm_factory(
        config_context={
            "zabbix": {
                "templates": [seed_netbox.ZABBIX_TEMPLATE],
                "interface_type": 2,
                "interface_port": 161,
                "snmp": {"community": "public", "version": 2, "bulk": 1},
            }
        }
    )

    run_vm_sync(vm.name)

    interfaces = zabbix_host(vm.name)["interfaces"]
    assert [i["type"] for i in interfaces] == [ZABBIX_SNMP_INTERFACE]
    assert interfaces[0]["port"] == "161"


def test_vm_without_config_context_is_skipped(vm_factory, run_vm_sync, zabbix_host):
    """No context means no template, and core.py:371 drops the VM.

    The off state of `vm_context`: it is what makes every other test in this
    file meaningful, so it is worth one test of its own rather than a comment.
    """
    vm = vm_factory(config_context=None)

    run_vm_sync(vm.name)

    assert zabbix_host(vm.name) is None


def test_vms_untouched_when_sync_vms_disabled(vm_factory, run_vm_sync, zabbix_host):
    """The off state of the switch every other test here turns on."""
    vm = vm_factory()

    run_vm_sync(vm.name, sync_vms=False)

    assert zabbix_host(vm.name) is None


def test_vm_hostgroup_from_cluster_type_and_cluster(
    vm_factory, run_vm_sync, zabbix_host
):
    """The default vm_hostgroup_format, which no device can produce.

    `cluster_type` is reached as `self.nb.cluster.type.name`
    (hostgroups.py:88) -- attribute access, so pynetbox lazily fetches the
    cluster. Worth contrasting with the tag map, which cannot reach the same
    field: see test_default_maps.py.
    """
    vm = vm_factory()

    run_vm_sync(vm.name)

    groups = [group["name"] for group in zabbix_host(vm.name)["hostgroups"]]
    assert groups == [seed_netbox.EXPECTED_VM_HOSTGROUP]


def test_vm_hostgroup_format_is_separate_from_the_device_one(
    vm_factory, run_vm_sync, zabbix_host
):
    """`hostgroup_format` must not leak into VMs.

    The device format here would resolve for a VM -- it has a site and a role --
    but names `manufacturer`, which is a device-only option (hostgroups.py:80).
    If the VM read it, the sync would raise HostgroupError rather than quietly
    grouping wrong, so the assertion is that the VM synced at all.
    """
    vm = vm_factory()

    run_vm_sync(vm.name, vm_hostgroup_format="cluster/role")

    groups = [group["name"] for group in zabbix_host(vm.name)["hostgroups"]]
    assert groups == [f"{seed_netbox.CLUSTER_NAME}/{seed_netbox.ROLE_NAME}"]


def test_vm_inventory_map_is_used_not_the_device_map(
    vm_factory, run_vm_sync, zabbix_host
):
    """VirtualMachine._inventory_map returns vm_inventory_map (virtual_machine.py:24).

    Both maps are set, to different Zabbix fields, so a VM reading the device
    map would be caught rather than merely failing to be proven.
    """
    vm = vm_factory(comments="vm note")

    run_vm_sync(
        vm.name,
        **INVENTORY_MANUAL_CONFIG,
        vm_inventory_map={"comments": "notes"},
        device_inventory_map={"comments": "location"},
    )

    inventory = zabbix_host(vm.name)["inventory"]
    assert inventory["notes"] == "vm note"
    assert inventory["location"] == ""


def test_vm_usermacro_map_is_used_not_the_device_map(
    vm_factory, run_vm_sync, zabbix_host
):
    vm = vm_factory(memory=4096)

    run_vm_sync(
        vm.name,
        usermacro_sync=True,
        vm_usermacro_map={"memory": "{$TOTAL_MEMORY}"},
        device_usermacro_map={"memory": "{$WRONG_MAP}"},
    )

    macros = macro_values(zabbix_host(vm.name))
    assert macros == {"{$TOTAL_MEMORY}": "4096"}


def test_vm_tag_map_is_used_not_the_device_map(vm_factory, run_vm_sync, zabbix_host):
    vm = vm_factory()

    run_vm_sync(
        vm.name,
        tag_sync=True,
        tag_name=None,
        vm_tag_map={"cluster/name": "cluster"},
        device_tag_map={"cluster/name": "wrong_map"},
    )

    assert tag_pairs(zabbix_host(vm.name)) == {
        ("cluster", seed_netbox.CLUSTER_NAME.lower())
    }


def test_vm_memory_is_stringified_for_zabbix(vm_factory, run_vm_sync, zabbix_host):
    """`memory` is an int in NetBox and must reach Zabbix as a string.

    The default vm_usermacro_map ships this mapping, and it is the only default
    map entry on either side whose NetBox value is not already a string. Zabbix
    rejects a non-string macro value, so field_mapper's str() is load-bearing
    here rather than cosmetic.
    """
    vm = vm_factory(memory=2048)

    run_vm_sync(vm.name, usermacro_sync=True, vm_usermacro_map={"memory": "{$MEM}"})

    assert macro_values(zabbix_host(vm.name)) == {"{$MEM}": "2048"}


def test_vm_config_context_macros_are_rendered(vm_factory, run_vm_sync, zabbix_host):
    """Jinja rendering reaches VMs too -- core.py:366 is the VM branch's call."""
    vm = vm_factory(config_context=vm_context(usermacros={"{$VM}": "{{ data.name }}"}))

    run_vm_sync(vm.name, usermacro_sync=True, render_config_context=True)

    assert macro_values(zabbix_host(vm.name))["{$VM}"] == vm.name


def test_vm_status_maps_to_zabbix_status(vm_factory, run_vm_sync, zabbix_host):
    """A VM in a disable-listed status syncs as a disabled host, not a missing one."""
    vm = vm_factory(status="offline")

    run_vm_sync(vm.name)

    assert zabbix_host(vm.name)["status"] == "1"


def test_vm_removal_status_deletes_the_host(vm_factory, run_vm_sync, zabbix_host):
    """A VM moved into a removal status is deleted from Zabbix on the next run."""
    vm = vm_factory()
    run_vm_sync(vm.name)
    assert zabbix_host(vm.name) is not None, "precondition: the VM synced"

    vm.status = "decommissioning"
    vm.save()
    run_vm_sync(vm.name)

    assert zabbix_host(vm.name) is None
