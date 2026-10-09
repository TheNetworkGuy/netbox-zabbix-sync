"""Tests for HostgroupFormat: which variables a hostgroup format may use, and
the explanation given when an item in a format is not supported.

The explanation is the same whether it is raised at startup (`verify`) or while
a host is synced (`Hostgroup.generate`), so both are tested against it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from netbox_zabbix_sync.modules.exceptions import HostgroupError
from netbox_zabbix_sync.modules.hostgroups import Hostgroup, HostgroupFormat

# A custom field that was made for devices, and later wanted for VM's as well
CF = "zabbix_additional_groups"


def custom_field(name):
    """What core.py hands to `verify`: NetBox custom fields, of which only the
    name is used."""
    return SimpleNamespace(name=name)


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def vm(netbox):
    return netbox.virtual_machine(
        "vm-ai001",
        site=netbox.site("Braillestraat"),
        role=netbox.role("Virtual Machine (QEMU)"),
        cluster=netbox.cluster("NUC-SRV", type="Proxmox"),
    )


@pytest.fixture
def device(netbox):
    return netbox.device("sw01")


class TestValidOptions:
    def test_each_type_has_its_own_variables(self):
        device_only = {"manufacturer", "location", "rack"}
        vm_only = {"cluster", "cluster_type"}
        dev = set(HostgroupFormat.valid_options("dev"))
        vm = set(HostgroupFormat.valid_options("vm"))

        assert device_only <= dev
        assert not device_only & vm
        assert vm_only <= vm
        assert not vm_only & dev

    def test_common_variables_are_valid_for_every_type(self):
        for hg_type in HostgroupFormat.TYPES:
            assert set(HostgroupFormat.COMMON) <= set(
                HostgroupFormat.valid_options(hg_type)
            )

    def test_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="Unknown hostgroup type 'x'"):
            HostgroupFormat.valid_options("x")


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ("'Datacenter'", True),
        ('"Datacenter"', True),
        ("'two words'", True),
        ("'abc", False),  # not closed
        ("abc'", False),  # not opened
        ("'abc\"", False),  # different quotes
        ("''", False),  # nothing between the quotes
        ("site", False),
    ],
)
def test_is_literal(item, expected):
    assert HostgroupFormat.is_literal(item) is expected


class TestVerifyAccepts:
    def test_variables_literals_and_custom_fields(self):
        HostgroupFormat.verify(
            "site/rack/'Datacenter'/my_cf",
            custom_fields=[custom_field("my_cf")],
            hg_type="dev",
        )

    def test_list_of_formats(self):
        HostgroupFormat.verify(
            ["cluster_type/cluster/role", "tenant_group/tenant"], hg_type="vm"
        )

    def test_no_custom_fields_given(self):
        HostgroupFormat.verify("site/role", custom_fields=None, hg_type="dev")


# (id, type, format, custom fields, fragments the explanation must contain)
REJECTED = [
    (
        "empty item",
        "dev",
        "site//role",
        [],
        ["hostgroup_format 'site//role'", "empty item", "double '/'"],
    ),
    (
        "whitespace",
        "dev",
        "site/ role",
        [],
        ["' role'", "hostgroup_format 'site/ role'", "leading or trailing whitespace"],
    ),
    (
        "quote not closed",
        "dev",
        "site/'abc",
        [],
        ["quoted literal", "same quote character", "cannot contain a '/'"],
    ),
    ("empty literal", "dev", "site/''", [], ["quoted literal", "at least one char"]),
    (
        "vm variable on a device",
        "dev",
        "site/cluster",
        [],
        [
            "'cluster' in hostgroup_format 'site/cluster'",
            "only available for virtual machines, not for devices",
            "Use it in 'vm_hostgroup_format'",
        ],
    ),
    (
        "device variable on a vm",
        "vm",
        "site/rack",
        [],
        [
            "'rack' in vm_hostgroup_format 'site/rack'",
            "only available for devices, not for virtual machines",
            "Use it in 'hostgroup_format'",
        ],
    ),
    ("capitalisation", "dev", "Site/role", [], ["Did you mean 'site'?"]),
    ("typo in a variable", "vm", "tenat", [], ["Did you mean 'tenant'?"]),
    (
        "typo in a custom field",
        "vm",
        "zabbix_additional_group",
        [CF],
        [f"Did you mean '{CF}'?"],
    ),
    (
        "custom field not assigned to the object type",
        "vm",
        CF,
        ["some_other_cf"],
        [
            f"'{CF}' in vm_hostgroup_format '{CF}'",
            "no custom field with this name exists on this virtual machine",
            "assigned to the virtual machine object type",
            "text, select or object",
        ],
    ),
]


class TestVerifyRejects:
    @pytest.mark.parametrize(
        ("hg_type", "hg_format", "custom_fields", "fragments"),
        [case[1:] for case in REJECTED],
        ids=[case[0] for case in REJECTED],
    )
    def test_explains_why(self, logger, hg_type, hg_format, custom_fields, fragments):
        with pytest.raises(HostgroupError) as error:
            HostgroupFormat.verify(
                hg_format,
                custom_fields=[custom_field(name) for name in custom_fields],
                hg_type=hg_type,
                logger=logger,
            )

        for fragment in fragments:
            assert fragment in str(error.value)
        # The same explanation ends up in the log
        logger.warning.assert_called_once_with(str(error.value))

    def test_reports_the_first_item_in_reading_order(self):
        with pytest.raises(HostgroupError, match="'zzz'"):
            HostgroupFormat.verify("site/zzz/aaa", hg_type="dev")

    def test_reports_the_format_that_contains_the_item(self):
        with pytest.raises(HostgroupError) as error:
            HostgroupFormat.verify(["site/role", "site/bogus"], hg_type="dev")

        assert "hostgroup_format 'site/bogus'" in str(error.value)
        assert "'site/role'" not in str(error.value)

    def test_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="Unknown hostgroup type"):
            HostgroupFormat.verify("site", hg_type="x")


class TestCustomFieldMissingForVMs:
    """The reported problem: a custom field that was only assigned to devices
    was used in the format for VM's."""

    def test_verify_at_startup_tells_where_to_look(self, logger):
        device_cfs = [custom_field(CF)]
        # core.py fetches the custom fields per object type: VM's have none yet
        vm_cfs = []

        HostgroupFormat.verify(f"site/{CF}", device_cfs, hg_type="dev")
        with pytest.raises(HostgroupError) as error:
            HostgroupFormat.verify(
                ["tenant_group/tenant", f"cluster_type/{CF}"],
                vm_cfs,
                hg_type="vm",
                logger=logger,
            )

        message = str(error.value)
        assert f"'{CF}' in vm_hostgroup_format 'cluster_type/{CF}'" in message
        assert "assigned to the virtual machine object type" in message

    def test_verify_passes_once_the_field_is_assigned_to_vms(self):
        HostgroupFormat.verify(
            ["tenant_group/tenant", f"cluster_type/{CF}"],
            [custom_field(CF)],
            hg_type="vm",
        )

    def test_generate_tells_where_to_look(self, vm, logger):
        hostgroup = Hostgroup("vm", vm, "4.2", logger)

        with pytest.raises(HostgroupError) as error:
            hostgroup.generate(f"cluster_type/{CF}")

        message = str(error.value)
        assert message.startswith("Unable to generate hostgroup for host vm-ai001. ")
        assert f"'{CF}' in vm_hostgroup_format 'cluster_type/{CF}'" in message
        assert "assigned to the virtual machine object type" in message
        logger.error.assert_called_once_with(message)

    def test_generate_works_once_the_field_is_assigned(self, netbox, logger):
        vm = netbox.virtual_machine(
            "vm-ai001",
            cluster=netbox.cluster("NUC-SRV", type="Proxmox"),
            custom_fields={CF: "Linux"},
        )

        hostgroup = Hostgroup("vm", vm, "4.2", logger)

        assert hostgroup.generate(f"cluster_type/{CF}") == "Proxmox/Linux"

    def test_an_assigned_field_without_a_value_is_left_out(self, netbox, logger):
        vm = netbox.virtual_machine(
            "vm-ai001",
            cluster=netbox.cluster("NUC-SRV", type="Proxmox"),
            custom_fields={CF: None},
        )

        hostgroup = Hostgroup("vm", vm, "4.2", logger)

        assert hostgroup.generate(f"cluster_type/{CF}") == "Proxmox"


class TestGenerateExplains:
    @pytest.mark.parametrize(
        ("hg_type", "hg_format", "fragments"),
        [
            ("vm", "site/rack", ["only available for devices, not for virtual"]),
            ("dev", "site/cluster", ["only available for virtual machines, not for"]),
            ("vm", "site//role", ["empty item"]),
            ("dev", "Site", ["Did you mean 'site'?"]),
        ],
    )
    def test_explains_why(self, vm, device, hg_type, hg_format, fragments):
        hostgroup = Hostgroup(hg_type, vm if hg_type == "vm" else device, "4.2")

        with pytest.raises(HostgroupError) as error:
            hostgroup.generate(hg_format)

        for fragment in fragments:
            assert fragment in str(error.value)

    @pytest.mark.parametrize(
        ("hg_type", "hg_format"),
        [
            ("vm", "site/rack"),
            ("dev", "site/cluster"),
            ("vm", "site//role"),
            ("dev", "Site"),
            ("vm", f"site/{CF}"),
        ],
    )
    def test_gives_the_same_explanation_as_verify(
        self, vm, device, logger, hg_type, hg_format
    ):
        nb = vm if hg_type == "vm" else device
        # Both know the custom fields of the object, here only zabbix_hostid
        custom_fields = [custom_field(name) for name in nb.custom_fields]

        with pytest.raises(HostgroupError) as from_verify:
            HostgroupFormat.verify(hg_format, custom_fields, hg_type=hg_type)
        with pytest.raises(HostgroupError) as from_generate:
            Hostgroup(hg_type, nb, "4.2", logger).generate(hg_format)

        assert str(from_verify.value) in str(from_generate.value)
