from logging import getLogger

import pytest

from netbox_zabbix_sync.modules.exceptions import JinjaRenderError
from netbox_zabbix_sync.modules.tools import (
    field_mapper,
    jinjafy_config_context,
    sanatize_log_output,
)


def test_sanatize_log_output_secrets():
    data = {
        "macros": [
            {"macro": "{$SECRET}", "type": "1", "value": "supersecret"},
            {"macro": "{$PLAIN}", "type": "0", "value": "notsecret"},
        ]
    }
    sanitized = sanatize_log_output(data)
    assert sanitized["macros"][0]["value"] == "********"
    assert sanitized["macros"][1]["value"] == "notsecret"


def test_sanatize_log_output_interface_secrets():
    data = {
        "interfaceid": 123,
        "details": {
            "authpassphrase": "supersecret",
            "privpassphrase": "anothersecret",
            "securityname": "sensitiveuser",
            "community": "public",
            "other": "normalvalue",
        },
    }
    sanitized = sanatize_log_output(data)
    # Sensitive fields should be sanitized
    assert sanitized["details"]["authpassphrase"] == "********"
    assert sanitized["details"]["privpassphrase"] == "********"
    assert sanitized["details"]["securityname"] == "********"
    # Non-sensitive fields should remain
    assert sanitized["details"]["community"] == "********"
    assert sanitized["details"]["other"] == "normalvalue"


def test_sanatize_log_output_interface_macros():
    data = {
        "interfaceid": 123,
        "details": {
            "authpassphrase": "{$SECRET_MACRO}",
            "privpassphrase": "{$SECRET_MACRO}",
            "securityname": "{$USER_MACRO}",
            "community": "{$SNNMP_COMMUNITY}",
        },
    }
    sanitized = sanatize_log_output(data)
    # Macro values should not be sanitized
    assert sanitized["details"]["authpassphrase"] == "{$SECRET_MACRO}"
    assert sanitized["details"]["privpassphrase"] == "{$SECRET_MACRO}"
    assert sanitized["details"]["securityname"] == "{$USER_MACRO}"
    assert sanitized["details"]["community"] == "{$SNNMP_COMMUNITY}"


def test_sanatize_log_output_plain_data():
    data = {"foo": "bar", "baz": 123}
    sanitized = sanatize_log_output(data)
    assert sanitized == data


def test_sanatize_log_output_non_dict():
    data = [1, 2, 3]
    sanitized = sanatize_log_output(data)
    assert sanitized == data


class DummyNB(dict):
    """A stand-in for a pynetbox Record.

    Subclasses dict because that is the part of a Record these functions use:
    field_mapper indexes (`value[item]`), and jinjafy_config_context calls
    dict() on the object. Attribute access is wired to the same data so
    `.config_context` and `.name` work like a Record's.
    """

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as e:
            raise AttributeError(item) from e


@pytest.fixture
def logger():
    return getLogger(__name__)


class TestFieldMapper:
    """field_mapper walks NetBox fields by name for inventory, macros and tags."""

    def test_maps_top_level_field(self, logger):
        nb = DummyNB(serial="SN-123")
        assert field_mapper("host", {"serial": "serialno_a"}, nb, logger) == {
            "serialno_a": "SN-123"
        }

    def test_walks_nested_fields_on_the_slash(self, logger):
        nb = DummyNB(device_type={"manufacturer": {"name": "Acme"}})
        mapper = {"device_type/manufacturer/name": "vendor"}
        assert field_mapper("host", mapper, nb, logger) == {"vendor": "Acme"}

    def test_values_are_stringified(self, logger):
        """Zabbix takes strings, so an int field must be converted, not sent raw."""
        nb = DummyNB(id=42)
        assert field_mapper("host", {"id": "{$NB_ID}"}, nb, logger) == {
            "{$NB_ID}": "42"
        }

    def test_zero_is_kept_rather_than_treated_as_empty(self, logger):
        """0 is a value, not an absence -- the reason for the int/float check."""
        nb = DummyNB(position=0)
        assert field_mapper("host", {"position": "site_rack"}, nb, logger) == {
            "site_rack": "0"
        }

    def test_empty_value_becomes_empty_string(self, logger):
        """None maps to "", which is what the Zabbix API accepts for a blank."""
        nb = DummyNB(serial=None)
        assert field_mapper("host", {"serial": "serialno_a"}, nb, logger) == {
            "serialno_a": ""
        }

    def test_empty_nested_parent_stops_the_walk(self, logger):
        """A null parent short-circuits instead of raising on the child."""
        nb = DummyNB(virtual_chassis=None)
        assert field_mapper(
            "host", {"virtual_chassis/name": "chassis"}, nb, logger
        ) == {"chassis": ""}

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "field_mapper indexes with value[item] (tools.py:130), so a key NetBox "
            "did not return raises KeyError instead of mapping to ''. The KeyError "
            "escapes Sync.start(), which catches SyncError only, and ends the whole "
            "run. Hit in practice by mapping a field that needs one of the "
            "extended_* settings without enabling it -- see the functional test "
            "test_unextended_mapped_field_aborts_the_run. Remove this marker once "
            "an absent key maps to '' like an empty value already does."
        ),
    )
    def test_absent_field_maps_to_empty_string(self, logger):
        """An absent key should be treated like an empty value, not raise.

        NetBox serves related objects in a nested form carrying only some of
        their fields, so "the map names a field this object does not have" is
        an ordinary condition, not a programming error.
        """
        nb = DummyNB(site={"name": "AMS-01"})

        assert field_mapper("host", {"site/latitude": "location_lat"}, nb, logger) == {
            "location_lat": ""
        }


class TestJinjafyConfigContext:
    """jinjafy_config_context renders the zabbix key with the object as `data`."""

    def test_renders_a_netbox_field_into_the_context(self):
        nb = DummyNB(
            serial="SN-123",
            config_context={"zabbix": {"usermacros": {"{$S}": "{{ data.serial }}"}}},
        )
        assert jinjafy_config_context(nb) == {"usermacros": {"{$S}": "SN-123"}}

    def test_renders_nested_data(self):
        nb = DummyNB(
            site={"name": "AMS-01"},
            config_context={"zabbix": {"tags": [{"site": "{{ data.site.name }}"}]}},
        )
        assert jinjafy_config_context(nb) == {"tags": [{"site": "AMS-01"}]}

    def test_config_context_is_not_visible_to_the_template(self):
        """`data` excludes config_context, so a context cannot template itself.

        Dropped deliberately (tools.py:82-83): leaving it in would let a
        template read its own unrendered source. Referencing it is not an
        error, though -- Jinja's default undefined renders as an empty string,
        so the macro silently comes out blank rather than failing loudly.
        """
        nb = DummyNB(config_context={"zabbix": {"x": "{{ data.config_context }}"}})

        assert jinjafy_config_context(nb) == {"x": ""}

    def test_explicit_context_overrides_the_objects_own(self):
        nb = DummyNB(serial="SN-123", config_context={"zabbix": {"a": "unused"}})
        assert jinjafy_config_context(nb, context={"b": "{{ data.serial }}"}) == {
            "b": "SN-123"
        }

    def test_unknown_filter_raises_jinja_render_error(self):
        """Jinja's own errors are wrapped, which is what core.py catches."""
        nb = DummyNB(config_context={"zabbix": {"x": "{{ data | no_such_filter }}"}})

        with pytest.raises(JinjaRenderError):
            jinjafy_config_context(nb)

    def test_render_producing_invalid_json_raises(self):
        """The context is rendered as JSON text, so a stray quote is fatal.

        Rendering templates `dumps(context)` and parses the result back, so a
        value containing a quote breaks the document rather than the value.
        This is the failure mode users hit with unescaped NetBox comments.
        """
        nb = DummyNB(
            comments='has "quotes"',
            config_context={"zabbix": {"x": "{{ data.comments }}"}},
        )

        with pytest.raises(JinjaRenderError):
            jinjafy_config_context(nb)

    @pytest.mark.parametrize(
        "config_context",
        [
            pytest.param({}, id="no-config-context"),
            pytest.param({"zabbix": {}}, id="empty-zabbix-key"),
            pytest.param({"other_app": {"k": "v"}}, id="no-zabbix-key"),
        ],
    )
    def test_returns_empty_dict_when_there_is_nothing_to_render(self, config_context):
        """The empty render, which the caller cannot tell from a failure.

        Pinned here because this return value is the root of a live bug: an
        empty dict is falsy, and core.py:211 reads any falsy render as an error
        and skips the host. So enabling render_config_context stops syncing
        every device without a zabbix config context -- most of them. See the
        functional test test_rendering_skips_hosts_with_no_zabbix_context.
        """
        nb = DummyNB(config_context=config_context)
        assert jinjafy_config_context(nb) == {}
