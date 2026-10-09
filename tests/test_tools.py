from logging import getLogger
from unittest.mock import MagicMock

import pytest

from netbox_zabbix_sync.modules.exceptions import JinjaRenderError
from netbox_zabbix_sync.modules.tools import (
    build_path,
    cf_to_string,
    choice_value,
    field_mapper,
    jinjafy_config_context,
    sanatize_log_output,
)
from tests.netbox_payloads import EXPECTED, NETBOX_VERSIONS, cf_cases, cf_value


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


@pytest.fixture
def logger():
    return getLogger(__name__)


class TestFieldMapper:
    """field_mapper walks NetBox fields by name for inventory, macros and tags."""

    def test_maps_top_level_field(self, logger, netbox):
        nb = netbox.device(serial="SN-123")
        assert field_mapper("host", {"serial": "serialno_a"}, nb, logger) == {
            "serialno_a": "SN-123"
        }

    def test_walks_nested_fields_on_the_slash(self, logger, netbox):
        nb = netbox.device(device_type=netbox.device_type("X1", manufacturer="Acme"))
        mapper = {"device_type/manufacturer/name": "vendor"}
        assert field_mapper("host", mapper, nb, logger) == {"vendor": "Acme"}

    def test_values_are_stringified(self, logger, netbox):
        """Zabbix takes strings, so an int field must be converted, not sent raw."""
        nb = netbox.device(id=42)
        assert field_mapper("host", {"id": "{$NB_ID}"}, nb, logger) == {
            "{$NB_ID}": "42"
        }

    def test_zero_is_kept_rather_than_treated_as_empty(self, logger, netbox):
        """0 is a value, not an absence -- the reason for the int/float check."""
        nb = netbox.device(position=0)
        assert field_mapper("host", {"position": "site_rack"}, nb, logger) == {
            "site_rack": "0"
        }

    def test_empty_value_becomes_empty_string(self, logger, netbox):
        """None maps to "", which is what the Zabbix API accepts for a blank."""
        nb = netbox.device(asset_tag=None)
        assert field_mapper("host", {"asset_tag": "asset_tag"}, nb, logger) == {
            "asset_tag": ""
        }

    def test_empty_nested_parent_stops_the_walk(self, logger, netbox):
        """A null parent short-circuits instead of raising on the child."""
        nb = netbox.device(virtual_chassis=None)
        assert field_mapper(
            "host", {"virtual_chassis/name": "chassis"}, nb, logger
        ) == {"chassis": ""}

    def test_absent_field_maps_to_empty_string(self, logger, netbox):
        """An absent key should be treated like an empty value, not raise.

        NetBox serves related objects in a nested form carrying only some of
        their fields, so "the map names a field this object does not have" is
        an ordinary condition, not a programming error. field_mapper reads
        through dict(record), which does not lazy load: without
        extended_site_properties the site's latitude is simply not there.
        """
        nb = netbox.device(site=netbox.site("AMS-01", latitude=52.37))

        assert field_mapper("host", {"site/latitude": "location_lat"}, nb, logger) == {
            "location_lat": ""
        }

    @pytest.mark.parametrize("version", NETBOX_VERSIONS)
    def test_select_custom_field_maps_to_its_value(self, logger, netbox, version):
        """`custom_fields/<select>` sends the value, not the 4.7 dict's repr."""
        nb = netbox.device(custom_fields={"env": cf_value(version, "select")})

        assert field_mapper("host", {"custom_fields/env": "alias"}, nb, logger) == {
            "alias": "staging"
        }

    @pytest.mark.parametrize("version", NETBOX_VERSIONS)
    def test_multiselect_custom_field_is_the_same_on_every_version(
        self, logger, netbox, version
    ):
        """A multiselect is stringified as a list of values on 4.6 and 4.7 alike."""
        nb = netbox.device(custom_fields={"fw": cf_value(version, "multiselect")})

        assert field_mapper("host", {"custom_fields/fw": "alias"}, nb, logger) == {
            "alias": "['iso27001', 'soc2']"
        }

    @pytest.mark.parametrize("version", NETBOX_VERSIONS)
    def test_object_custom_field_can_be_walked_into(self, logger, netbox, version):
        """An object custom field is a nested dict, so its name is a path away."""
        nb = netbox.device(custom_fields={"owner": cf_value(version, "object")})

        assert field_mapper(
            "host", {"custom_fields/owner/name": "alias"}, nb, logger
        ) == {"alias": "Internal IT"}

    def test_label_path_into_a_47_select_keeps_working(self, logger, netbox):
        """A map written for 4.7 can still reach the label by walking into it.

        Only the value at the end of the path is resolved, so the dict stays
        walkable -- `custom_fields/<select>/label` is how to get the label.
        """
        nb = netbox.device(custom_fields={"env": cf_value("4.7", "select")})

        assert field_mapper(
            "host", {"custom_fields/env/label": "alias"}, nb, logger
        ) == {"alias": "Staging"}

    def test_choice_fields_outside_custom_fields_are_untouched(self, logger, netbox):
        """Only custom fields changed shape, so `status` keeps its dict.

        The shipped maps reach it as `status/label`, which has to keep
        resolving to the label rather than stopping at the value.
        """
        nb = netbox.device(status="active")

        assert field_mapper(
            "host", {"status/label": "deployment_status"}, nb, logger
        ) == {"deployment_status": "Active"}


class TestChoiceValue:
    """choice_value undoes NetBox 4.7's `{"value", "label"}` choice wrapping."""

    @pytest.mark.parametrize("version", NETBOX_VERSIONS)
    def test_select_resolves_to_its_value(self, version):
        assert choice_value(cf_value(version, "select")) == "staging"

    @pytest.mark.parametrize("version", NETBOX_VERSIONS)
    def test_multiselect_resolves_to_a_list_of_values(self, version):
        assert choice_value(cf_value(version, "multiselect")) == ["iso27001", "soc2"]

    @pytest.mark.parametrize(
        "value",
        [
            *(p.values[0] for p in cf_cases("object", "multiobject")),
            "hello",
            7,
            True,
            None,
            # A NetBox 2.7 choice carries an id as well. It is not the 4.7
            # custom field shape, so it is left for the caller to handle.
            {"id": 1, "value": "active", "label": "Active"},
        ],
    )
    def test_anything_else_is_returned_unchanged(self, value):
        assert choice_value(value) == value


class TestCfToString:
    """cf_to_string turns a custom field value into the name it stands for."""

    @pytest.mark.parametrize(("value", "cf_type"), cf_cases("text", "select", "object"))
    def test_resolves_to_the_same_name_on_every_version(self, value, cf_type):
        assert cf_to_string(value) == EXPECTED[cf_type]

    def test_empty_value_stays_empty(self):
        assert cf_to_string(None) is None

    def test_dict_without_the_key_logs_and_returns_none(self):
        """An object whose nested form lacks `key` is reported, not guessed at."""
        logger = MagicMock()

        assert cf_to_string({"id": 1, "display": "x"}, logger=logger) is None
        logger.error.assert_called_once()


class TestJinjafyConfigContext:
    """jinjafy_config_context renders the zabbix key with the object as `data`."""

    def test_renders_a_netbox_field_into_the_context(self, netbox):
        nb = netbox.device(
            serial="SN-123",
            config_context={"zabbix": {"usermacros": {"{$S}": "{{ data.serial }}"}}},
        )
        assert jinjafy_config_context(nb) == {"usermacros": {"{$S}": "SN-123"}}

    def test_renders_nested_data(self, netbox):
        nb = netbox.device(
            site="AMS-01",
            config_context={"zabbix": {"tags": [{"site": "{{ data.site.name }}"}]}},
        )
        assert jinjafy_config_context(nb) == {"tags": [{"site": "AMS-01"}]}

    def test_config_context_is_not_visible_to_the_template(self, netbox):
        """`data` excludes config_context, so a context cannot template itself.

        Dropped deliberately (tools.py:82-83): leaving it in would let a
        template read its own unrendered source. Referencing it is not an
        error, though -- Jinja's default undefined renders as an empty string,
        so the macro silently comes out blank rather than failing loudly.
        """
        nb = netbox.device(
            config_context={"zabbix": {"x": "{{ data.config_context }}"}}
        )

        assert jinjafy_config_context(nb) == {"x": ""}

    def test_explicit_context_overrides_the_objects_own(self, netbox):
        nb = netbox.device(serial="SN-123", config_context={"zabbix": {"a": "unused"}})
        assert jinjafy_config_context(nb, context={"b": "{{ data.serial }}"}) == {
            "b": "SN-123"
        }

    def test_unknown_filter_raises_jinja_render_error(self, netbox):
        """Jinja's own errors are wrapped, which is what core.py catches."""
        nb = netbox.device(
            config_context={"zabbix": {"x": "{{ data | no_such_filter }}"}}
        )

        with pytest.raises(JinjaRenderError):
            jinjafy_config_context(nb)

    def test_render_producing_invalid_json_raises(self, netbox):
        """The context is rendered as JSON text, so a stray quote is fatal.

        Rendering templates `dumps(context)` and parses the result back, so a
        value containing a quote breaks the document rather than the value.
        This is the failure mode users hit with unescaped NetBox comments.
        """
        nb = netbox.device(
            comments='has "quotes"',
            config_context={"zabbix": {"x": "{{ data.comments }}"}},
        )

        with pytest.raises(JinjaRenderError):
            jinjafy_config_context(nb)


def region(name: str, depth: int, parent: str | None):
    """One entry of the flat recordset build_path walks.

    Mirrors what convert_recordset produces from a pynetbox region: a dict with
    the record's own `name`, its `_depth` in the tree, and `parent` -- which is
    a Record whose str() is the parent's name, so a plain string stands in.
    """
    return {"name": name, "_depth": depth, "parent": parent}


class TestBuildPath:
    """build_path reconstructs a region/site-group ancestry for a hostgroup."""

    def test_top_level_object_is_its_own_path(self):
        records = [region("EU", 0, None)]
        assert build_path("EU", records) == ["EU"]

    def test_walks_up_through_every_parent(self):
        records = [
            region("EU", 0, None),
            region("NL", 1, "EU"),
            region("AMS", 2, "NL"),
        ]
        assert build_path("AMS", records) == ["EU", "NL", "AMS"]

    def test_returns_only_the_ancestry_of_the_requested_leaf(self):
        """A sibling branch in the same recordset must not bleed into the path."""
        records = [
            region("EU", 0, None),
            region("NL", 1, "EU"),
            region("US", 0, None),
        ]
        assert build_path("NL", records) == ["EU", "NL"]

    def test_unknown_endpoint_returns_empty(self):
        assert build_path("nope", [region("EU", 0, None)]) == []

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "build_path finds ancestors by name (tools.py:37). NetBox only "
            "enforces unique region names at the top level, so a name reused "
            "under two parents yields two matches; build_path cannot choose and "
            "returns [] (tools.py:32,35), dropping the whole path. The device "
            "then lands in the wrong hostgroup with no error -- see the "
            "functional test test_ambiguous_region_name_still_traverses. Remove "
            "this marker once build_path disambiguates by parent or id."
        ),
    )
    def test_reused_ancestor_name_still_resolves(self):
        """`North` under both EU and US should still resolve EU's leaf's path.

        The leaf is unambiguous -- it has one parent -- but its parent's name is
        not unique across the tree, which is all build_path keys on.
        """
        records = [
            region("EU", 0, None),
            region("US", 0, None),
            region("North", 1, "EU"),
            region("North", 1, "US"),
            region("Leaf", 2, "North"),
        ]
        assert build_path("Leaf", records) == ["EU", "North", "Leaf"]
