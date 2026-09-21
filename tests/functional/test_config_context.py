"""Config context: what it configures, and rendering it through Jinja2.

Config context is the main customisation surface -- it sets the interface type,
the templates, the macros and the tags for one device without touching the
config file. Two things here are worth a live stack:

- **The config context the sync reads is NetBox's, not the test's.** These
  tests set `local_context_data` and assert on the result, so what they
  exercise is the context NetBox rendered and served. A mock is handed the
  final dict and skips the step that can actually be wrong.
- **Jinja renders against real NetBox data.** `jinjafy_config_context` templates
  the zabbix key with `data` bound to the device dict (tools.py:67). Whether
  `data.serial` resolves depends on how NetBox serialises a device, which is
  precisely what a DummyNB cannot tell you.

The rendering tests pair each assertion with its unrendered state: a test that
only asserts `{{ data.serial }}` becomes the serial would also pass if Jinja
ran unconditionally, which would be a bug -- render_config_context is
experimental and off by default.
"""

import pytest

from tests.functional.bootstrap.seed_netbox import ZABBIX_TEMPLATE
from tests.functional.conftest import macro_values, tag_pairs

AGENT_INTERFACE_TYPE = "1"
SNMP_INTERFACE_TYPE = "2"

# A second template from the Zabbix default dataset. SNMP, like ZABBIX_TEMPLATE:
# Zabbix refuses to link an agent template to a host with no agent interface,
# and a device with no config context gets an SNMP interface (host.py:496).
SECOND_TEMPLATE = "Generic by SNMP"


# --- config context as host configuration -----------------------------------


def test_interface_type_and_port_from_config_context(
    device_factory, run_sync, zabbix_host
):
    """Config context turns the default SNMP interface into an agent one.

    Also the reason the seeded template is SNMP: this is the one device in the
    suite that legitimately has an agent interface, so it takes an agent
    template to match (interface.py:38-68).
    """
    device = device_factory(
        address="10.0.0.80/24",
        config_context={
            "zabbix": {
                "interface_type": "agent",
                "interface_port": "10055",
                "templates": ["Zabbix agent"],
            }
        },
    )

    run_sync(device.name, templates_config_context=True)

    host = zabbix_host(device.name)
    assert host is not None
    assert len(host["interfaces"]) == 1
    interface = host["interfaces"][0]
    assert interface["type"] == AGENT_INTERFACE_TYPE
    assert interface["port"] == "10055", "config context port was ignored"


def test_snmp_details_from_config_context(device_factory, run_sync, zabbix_host):
    """SNMP v2 community and bulk settings reach the Zabbix interface details.

    `details` is a nested structure Zabbix validates per SNMP version, so a
    wrong shape is rejected at host.create rather than quietly stored.
    """
    device = device_factory(
        address="10.0.0.81/24",
        config_context={
            "zabbix": {
                "interface_type": "snmp",
                "snmp": {"community": "{$SNMP_COMMUNITY}", "version": 2, "bulk": 0},
            }
        },
    )

    run_sync(device.name)

    host = zabbix_host(device.name)
    assert host is not None
    interface = host["interfaces"][0]
    assert interface["type"] == SNMP_INTERFACE_TYPE
    details = interface["details"]
    assert details["version"] == "2"
    assert details["bulk"] == "0"
    assert details["community"] == "{$SNMP_COMMUNITY}"


def test_templates_from_config_context(device_factory, run_sync, zabbix_host):
    """templates_config_context=True takes templates from the context.

    The device type's custom field still says `Linux by SNMP`, so a sync that
    ignored the flag would link that instead -- the assertion excludes it
    rather than only looking for the context's template.
    """
    device = device_factory(
        address="10.0.0.82/24",
        config_context={"zabbix": {"templates": [SECOND_TEMPLATE]}},
    )

    run_sync(device.name, templates_config_context=True)

    host = zabbix_host(device.name)
    assert host is not None
    templates = {template["host"] for template in host["parentTemplates"]}
    assert templates == {SECOND_TEMPLATE}
    assert ZABBIX_TEMPLATE not in templates, "custom field template was used anyway"


def test_config_context_overrules_custom_field_template(
    device_factory, run_sync, zabbix_host
):
    """With overrule set, a context template beats the custom field's."""
    device = device_factory(
        address="10.0.0.83/24",
        config_context={"zabbix": {"templates": [SECOND_TEMPLATE]}},
    )

    run_sync(device.name, templates_config_context_overrule=True)

    host = zabbix_host(device.name)
    assert host is not None
    templates = {template["host"] for template in host["parentTemplates"]}
    assert templates == {SECOND_TEMPLATE}


def test_overrule_falls_back_to_custom_field(device_factory, run_sync, zabbix_host):
    """Overrule with no context templates falls back to the custom field.

    The other half of the flag (host.py:261-268): overruling must not mean
    "context or nothing", or every device without a context would stop syncing.
    """
    device = device_factory(address="10.0.0.84/24")

    run_sync(device.name, templates_config_context_overrule=True)

    host = zabbix_host(device.name)
    assert host is not None
    templates = {template["host"] for template in host["parentTemplates"]}
    assert templates == {ZABBIX_TEMPLATE}


# --- Jinja2 rendering (experimental) ----------------------------------------


def test_config_context_not_rendered_by_default(device_factory, run_sync, zabbix_host):
    """Without render_config_context, Jinja syntax is a literal string.

    The off state that gives the rendering tests below their meaning, and the
    documented default: rendering is experimental (settings.py:92).
    """
    device = device_factory(
        address="10.0.0.85/24",
        serial="SN-RAW",
        config_context={"zabbix": {"usermacros": {"{$SERIAL}": "{{ data.serial }}"}}},
    )

    run_sync(device.name, usermacro_sync=True, device_usermacro_map={})

    host = zabbix_host(device.name)
    assert host is not None
    assert macro_values(host)["{$SERIAL}"] == "{{ data.serial }}", (
        "config context was rendered despite render_config_context=False"
    )


def test_jinja_renders_netbox_field_into_macro(device_factory, run_sync, zabbix_host):
    """render_config_context=True resolves `data.serial` from the real device."""
    device = device_factory(
        address="10.0.0.86/24",
        serial="SN-RENDERED",
        config_context={"zabbix": {"usermacros": {"{$SERIAL}": "{{ data.serial }}"}}},
    )

    run_sync(
        device.name,
        render_config_context=True,
        usermacro_sync=True,
        device_usermacro_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert macro_values(host)["{$SERIAL}"] == "SN-RENDERED"


def test_jinja_renders_nested_netbox_data_into_tags(
    device_factory, run_sync, zabbix_host
):
    """Rendering reaches nested NetBox data, and feeds the tag path too.

    `data.site.name` proves the device dict Jinja gets keeps its nested
    objects, which is the difference between config context rendering being
    useful and being a string formatter.
    """
    device = device_factory(
        address="10.0.0.87/24",
        config_context={"zabbix": {"tags": [{"site": "{{ data.site.name }}"}]}},
    )

    run_sync(
        device.name,
        render_config_context=True,
        tag_sync=True,
        tag_name=False,
        device_tag_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert tag_pairs(host) == {("site", "ams-01")}


def test_jinja_custom_filter_transforms_netbox_data(
    device_factory, run_sync, zabbix_host
):
    """The bundled custom filters are loaded into the Jinja environment.

    `regex_replace` comes from jinja_filters and is registered by name at
    render time (tools.py:89). If that registration broke, this raises inside
    Jinja rather than returning the wrong string -- so the failure is the
    host's absence, not a bad value.
    """
    device = device_factory(
        address="10.0.0.88/24",
        serial="SN-ABC-123",
        config_context={
            "zabbix": {
                "usermacros": {
                    "{$SHORT_SERIAL}": "{{ data.serial | regex_replace('^SN-', '') }}"
                }
            }
        },
    )

    run_sync(
        device.name,
        render_config_context=True,
        usermacro_sync=True,
        device_usermacro_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None, "custom filter failed to load; the render raised"
    assert macro_values(host)["{$SHORT_SERIAL}"] == "ABC-123"


def test_jinja_ipaddr_filter_available(device_factory, run_sync, zabbix_host):
    """The j2ipaddr filters are loaded alongside the local ones.

    They come from a separate package and a separate `filters.update` call
    (tools.py:88), so one can be lost without the other noticing.
    """
    device = device_factory(
        address="10.0.0.89/24",
        config_context={
            "zabbix": {
                "usermacros": {
                    "{$NETMASK}": "{{ data.primary_ip4.address | ip_netmask }}"
                }
            }
        },
    )

    run_sync(
        device.name,
        render_config_context=True,
        usermacro_sync=True,
        device_usermacro_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None, "j2ipaddr filters failed to load; the render raised"
    assert macro_values(host)["{$NETMASK}"] == "255.255.255.0"


def test_jinja_conditional_builds_context(device_factory, run_sync, zabbix_host):
    """Jinja control flow works, not just interpolation.

    Rendering templates the *serialised JSON* of the zabbix key, so a
    conditional has to survive being embedded in a JSON string and parsed back
    (tools.py:91-93). That round trip is the part worth testing.
    """
    device = device_factory(
        address="10.0.0.90/24",
        status="active",
        config_context={
            "zabbix": {
                "usermacros": {
                    "{$MONITORED}": "{% if data.status.value == 'active' %}yes{% else %}no{% endif %}"
                }
            }
        },
    )

    run_sync(
        device.name,
        render_config_context=True,
        usermacro_sync=True,
        device_usermacro_map={},
    )

    host = zabbix_host(device.name)
    assert host is not None
    assert macro_values(host)["{$MONITORED}"] == "yes"


def test_broken_jinja_skips_host_without_killing_the_run(
    device_factory, sync_runner, zabbix_host
):
    """A device whose context will not render is skipped; the run continues.

    Both devices are in one sync. The broken one references a filter that does
    not exist, so its render raises and core.py:198-210 skips it -- and the
    healthy device must still be synced, or one bad config context would take
    down every host behind it in the loop.
    """
    broken = device_factory(
        address="10.0.0.91/24",
        config_context={
            "zabbix": {"usermacros": {"{$X}": "{{ data | no_such_filter }}"}}
        },
    )
    # The healthy device needs a zabbix config context of its own: with
    # rendering on, a device without one is skipped too, which would make this
    # test pass for the wrong reason. See test_rendering_skips_hosts_with_no_zabbix_context.
    healthy = device_factory(
        address="10.0.0.92/24",
        config_context={"zabbix": {"usermacros": {"{$OK}": "yes"}}},
    )

    sync_runner(
        device_filter={"name": [broken.name, healthy.name]},
        render_config_context=True,
        usermacro_sync=True,
    )

    assert zabbix_host(broken.name) is None, "host with unrenderable context was synced"
    assert zabbix_host(healthy.name) is not None, (
        "a broken config context stopped an unrelated device from syncing"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "render_config_context=True skips every host whose config context has no "
        "usable zabbix key. jinjafy_config_context returns {} for those "
        "(tools.py:78-79, 96), and core.py:211 reads a falsy render as a failure "
        "and skips the host. Remove this marker once an empty render leaves the "
        "host's config context alone instead."
    ),
)
@pytest.mark.parametrize(
    "context",
    [
        pytest.param(None, id="no-config-context"),
        pytest.param({"zabbix": {}}, id="empty-zabbix-key"),
        pytest.param({"other_app": {"key": "value"}}, id="no-zabbix-key"),
    ],
)
def test_rendering_skips_hosts_with_no_zabbix_context(
    device_factory, run_sync, zabbix_host, context
):
    """Turning rendering on must not stop unrelated devices from syncing.

    Config context is shared with the rest of NetBox, and a `zabbix` key is not
    something most devices have -- `no-config-context` is simply the default
    device, which is to say the majority of a real inventory. All three of
    these sync normally with rendering off.

    With it on they are all skipped: an empty render is indistinguishable from
    a failed one at core.py:211, so the feature quietly stops syncing every
    host it has nothing to do. That makes the blast radius of the experimental
    flag the whole inventory rather than the hosts using it.
    """
    device = device_factory(address="10.0.0.93/24", config_context=context)

    run_sync(device.name, render_config_context=True)

    assert zabbix_host(device.name) is not None, (
        "rendering skipped a device over a config context it should ignore"
    )
