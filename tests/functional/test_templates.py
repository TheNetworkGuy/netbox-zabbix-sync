"""Where a device's Zabbix templates come from, and what happens when they don't.

`template_cf` names the NetBox custom field holding the template name. It is the
only one of the template settings not covered by test_config_context.py, and it
is easy to get wrong in a way that looks like it works: the seed data uses the
default field name, so a sync that ignored the setting entirely would still pass
any test that left it at its default. Every test here therefore uses a custom
field with a name the default would never find.

The custom field lives on the *device type*, not the device (host.py:275), which
is the other thing worth pinning -- it means templates are chosen per model, and
a device cannot override its type's template except through config context.
"""

import pytest

from tests.functional.bootstrap import seed_netbox

pytestmark = pytest.mark.functional

# A second real template, used wherever a test needs to tell "the configured
# template arrived" from "the seeded default arrived". ICMP Ping is chosen
# because it binds to no particular interface type, so it can be linked
# alongside or instead of the seeded SNMP template without Zabbix objecting.
ALTERNATE_TEMPLATE = "ICMP Ping"

# For the one test that links two templates at once. Zabbix rejects a host
# inheriting the same item key twice, and "Linux by SNMP" already carries the
# icmpping items -- so the second template has to be one that overlaps nothing,
# not merely one that is different.
NON_OVERLAPPING_TEMPLATE = "Zabbix server health"


@pytest.fixture
def device_type_cf(nb, custom_field_factory, seeded):
    """A custom field on the device type, set to a template name.

    Yields a `(field_name, set_value)` pair. The device type is session-seeded
    and shared, so the value is cleared afterwards however the test ends --
    otherwise the next test's device inherits a template it never asked for.
    """
    cf_name = custom_field_factory(["dcim.devicetype"])
    device_type_id = seeded["device_type"].id

    def set_value(value: str) -> None:
        # Refetched each time: the session-scoped Record keeps whatever
        # custom_fields it last saved, including ones since deleted.
        device_type = nb.dcim.device_types.get(device_type_id)
        device_type.custom_fields[cf_name] = value
        device_type.save()

    yield cf_name, set_value

    device_type = nb.dcim.device_types.get(device_type_id)
    device_type.custom_fields[cf_name] = None
    device_type.save()


def template_names(host: dict) -> list[str]:
    return sorted(template["host"] for template in host["parentTemplates"])


def test_templates_read_from_the_configured_custom_field(
    device_type_cf, device_factory, run_sync, zabbix_host
):
    """A non-default field name is honoured, and its value becomes the template.

    The chosen template differs from the seeded default too, so this fails both
    if the setting is ignored and if the field is read but its value is not.
    """
    cf_name, set_value = device_type_cf
    set_value(ALTERNATE_TEMPLATE)
    device = device_factory()

    run_sync(device.name, template_cf=cf_name)

    assert template_names(zabbix_host(device.name)) == [ALTERNATE_TEMPLATE]


def test_the_default_field_is_not_consulted_when_the_setting_points_elsewhere(
    device_type_cf, device_factory, run_sync, zabbix_host
):
    """The seeded `zabbix_template` field is ignored once `template_cf` moves.

    The device type carries both fields with different templates, so a sync
    that fell back to the default name -- or read both -- lands a different
    answer than a sync that read only the configured one.
    """
    cf_name, set_value = device_type_cf
    set_value(ALTERNATE_TEMPLATE)
    device = device_factory()

    run_sync(device.name, template_cf=cf_name)

    templates = template_names(zabbix_host(device.name))
    assert templates == [ALTERNATE_TEMPLATE]
    assert seed_netbox.ZABBIX_TEMPLATE not in templates


def test_a_missing_custom_field_costs_only_its_own_host(
    device_factory, run_sync, zabbix_host, tag_factory, sync_runner
):
    """Pointing `template_cf` at a field that does not exist skips the host.

    `_get_templates_cf` raises `TemplateError`, which is a `SyncError`, so the
    per-host handler catches it and the run continues (host.py:283). That is
    the containment this test is really about: `template_cf` is global, so a
    typo in it would otherwise take down the whole run.

    Both devices share a tag and one run, so "the run continued" is observed
    rather than assumed -- except that here the typo affects every device
    equally, so what is asserted is that the run *ended normally* with both
    hosts skipped, not that one survived.
    """
    tag = tag_factory()
    first = device_factory(tags=[tag.id])
    second = device_factory(tags=[tag.id])

    sync_runner(device_filter={"tag": tag.slug}, template_cf="cf_does_not_exist")

    assert zabbix_host(first.name) is None
    assert zabbix_host(second.name) is None


def test_an_empty_custom_field_value_costs_only_its_own_host(
    device_type_cf, device_factory, run_sync, zabbix_host
):
    """The field exists but is unset: no template, so no host.

    A device type nobody has filled in yet is the ordinary way to reach this,
    and it must not be an error -- Zabbix rejects a host with no template, so
    skipping is the only option. Pinned because the alternative implementation,
    creating the host templateless, fails at the API instead.
    """
    cf_name, _ = device_type_cf
    device = device_factory()

    run_sync(device.name, template_cf=cf_name)

    assert zabbix_host(device.name) is None


def test_config_context_still_overrules_a_custom_template_field(
    device_type_cf, device_factory, run_sync, zabbix_host
):
    """`templates_config_context_overrule` works against any field name.

    The overrule path and the `template_cf` path are separate reads, so this
    checks they compose: a renamed field must still lose to the config context
    when the overrule setting says it should.
    """
    cf_name, set_value = device_type_cf
    set_value(ALTERNATE_TEMPLATE)
    device = device_factory(
        config_context={"zabbix": {"templates": [seed_netbox.ZABBIX_TEMPLATE]}}
    )

    run_sync(
        device.name,
        template_cf=cf_name,
        templates_config_context_overrule=True,
    )

    assert template_names(zabbix_host(device.name)) == [seed_netbox.ZABBIX_TEMPLATE]


def test_multiple_templates_from_config_context_all_reach_zabbix(
    device_factory, run_sync, zabbix_host
):
    """A list of templates is linked in full, not just its first entry.

    The custom field can only hold one template name, so a multi-template host
    has to come from config context -- which makes this the only path where the
    list handling in `zbx_template_prepper` is exercised end to end.
    """
    device = device_factory(
        config_context={
            "zabbix": {
                "templates": [seed_netbox.ZABBIX_TEMPLATE, NON_OVERLAPPING_TEMPLATE]
            }
        }
    )

    run_sync(device.name, templates_config_context=True)

    assert template_names(zabbix_host(device.name)) == sorted(
        [seed_netbox.ZABBIX_TEMPLATE, NON_OVERLAPPING_TEMPLATE]
    )


def test_an_unknown_template_name_costs_only_its_own_host(
    device_factory, run_sync, zabbix_host
):
    """A template Zabbix does not have skips the host rather than the run.

    The realistic cause is a template renamed in Zabbix while NetBox still
    names the old one, which is a per-device data problem and has to stay one.
    """
    device = device_factory(
        config_context={"zabbix": {"templates": ["fn-no-such-template"]}}
    )
    ordinary = device_factory()

    run_sync(device.name, templates_config_context=True)
    run_sync(ordinary.name)

    assert zabbix_host(device.name) is None
    assert zabbix_host(ordinary.name) is not None
