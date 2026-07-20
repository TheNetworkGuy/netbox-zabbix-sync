"""The Zabbix host description, which is built entirely from settings.

`description` is unusual among the settings: it is not a boolean or a map but a
small template language. It takes one of two named defaults ("static",
"dynamic"), a literal `False` meaning empty, or an arbitrary string that may
contain `{datetime}` and `{owner}` macros -- and any of those four can be
overridden per host from the config context. `description_dt_format` then
controls how `{datetime}` renders.

The failure mode worth testing is the quiet one: an unknown macro does not fail
the host, it silently falls back to the static default (host_description.py:70).
A test that only checked "some description arrived" would pass for every one of
those fallbacks, so each test below asserts the exact string.
"""

from datetime import datetime

import pytest

pytestmark = pytest.mark.functional

STATIC_DEFAULT = "Host added by NetBox sync script."


def description_of(host: dict) -> str:
    return host["description"]


def test_static_is_the_default(device_factory, run_sync, zabbix_host):
    """No `description` setting at all still produces the static text."""
    device = device_factory()

    run_sync(device.name)

    assert description_of(zabbix_host(device.name)) == STATIC_DEFAULT


def test_description_false_produces_an_empty_description(
    device_factory, run_sync, zabbix_host
):
    """`False` is a real option, distinct from unset (host_description.py:110).

    Worth its own test because `False` and "unset" are both falsy in Python but
    mean opposite things here: unset gives the static default, `False` gives an
    empty string.
    """
    device = device_factory()

    run_sync(device.name, description=False)

    assert description_of(zabbix_host(device.name)) == ""


def test_dynamic_resolves_both_macros(device_factory, run_sync, zabbix_host):
    """ "dynamic" is the other named default, and it exercises both macros.

    The date is asserted rather than the full timestamp: minutes and seconds
    are a race against the clock between the sync and the assertion, but the
    date is not. Checking the prefix and suffix around it pins the template's
    shape without pinning the moment it ran.
    """
    device = device_factory()

    run_sync(device.name, description="dynamic")

    description = description_of(zabbix_host(device.name))
    assert description.startswith("Host by owner ")
    assert " added by NetBox sync script on " in description
    assert datetime.now().strftime("%Y-%m-%d") in description
    # No macro survived unresolved -- the fallback would have replaced the
    # whole string with the static default, but a partial resolve would not.
    assert "{" not in description


def test_a_custom_description_is_used_verbatim(device_factory, run_sync, zabbix_host):
    """Any string that is not a named default is the description itself."""
    device = device_factory()

    run_sync(device.name, description="Managed by the network team")

    assert description_of(zabbix_host(device.name)) == "Managed by the network team"


def test_custom_dt_format_changes_how_datetime_renders(
    device_factory, run_sync, zabbix_host
):
    """`description_dt_format` is read, not just accepted.

    A year-only format is used so the expected value is exact and stable: a
    format including minutes would leave this test racing the clock.
    """
    device = device_factory()

    run_sync(
        device.name,
        description="synced in {datetime}",
        description_dt_format="%Y",
    )

    expected = f"synced in {datetime.now().strftime('%Y')}"
    assert description_of(zabbix_host(device.name)) == expected


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A meaningless datetime format reaches Zabbix verbatim. The guard at "
        "host_description.py:39-49 catches ValueError/TypeError around "
        "`strftime`, but `strftime` does not validate directives -- an unknown "
        "one like %Q is passed through literally rather than raising -- so the "
        "ValueError arm is unreachable and only a non-str format (TypeError) "
        "ever hits the fallback. The user gets a Zabbix description containing "
        "their format string instead of a date, with nothing logged. Remove "
        "this marker once the format is validated up front; see ISSUES.md "
        "issue 6."
    ),
)
def test_a_meaningless_dt_format_falls_back_to_the_default(
    device_factory, run_sync, zabbix_host
):
    """A format that cannot render a date should fall back, as a bad type does.

    Asserted as "a real date arrived", which is what the existing fallback
    produces for the `TypeError` case (see the test below). The two inputs are
    the same user error -- a `description_dt_format` that cannot produce a
    datetime -- so they should reach the same place.
    """
    device = device_factory()

    run_sync(
        device.name,
        description="synced in {datetime}",
        description_dt_format="%Q-not-a-format",
    )

    description = description_of(zabbix_host(device.name))
    assert description.startswith("synced in ")
    assert datetime.now().strftime("%Y-%m-%d") in description


def test_a_non_string_dt_format_hits_the_guard_and_falls_back(
    device_factory, run_sync, zabbix_host
):
    """The reachable half of the guard: `TypeError`, not `ValueError`.

    `strftime` rejects a non-str argument, which is the case a config.py can
    produce by assigning a number or leaving the value None. The fallback to
    the default format is what keeps the host syncing, so the assertion is that
    a real date arrived.
    """
    device = device_factory()

    run_sync(
        device.name,
        description="synced in {datetime}",
        description_dt_format=None,
    )

    description = description_of(zabbix_host(device.name))
    assert description.startswith("synced in ")
    assert datetime.now().strftime("%Y-%m-%d") in description


def test_unknown_macro_falls_back_to_static(device_factory, run_sync, zabbix_host):
    """The quiet failure: one bad macro discards the whole custom description.

    Not a partial render and not an error -- the entire string is replaced by
    the static default (host_description.py:70,119). Asserted exactly, because
    a test that merely checked the macro was gone would pass for a render that
    dropped only the macro.
    """
    device = device_factory()

    run_sync(device.name, description="site is {site} at {datetime}")

    assert description_of(zabbix_host(device.name)) == STATIC_DEFAULT


def test_config_context_overrides_the_configured_description(
    device_factory, run_sync, zabbix_host
):
    """A per-host description beats the global setting.

    Both are set, to different values, so this fails if the precedence is the
    other way round rather than merely proving the context was read.
    """
    device = device_factory(
        config_context={"zabbix": {"description": "this host is special"}}
    )

    run_sync(device.name, description="the global default")

    assert description_of(zabbix_host(device.name)) == "this host is special"


def test_config_context_description_resolves_macros_too(
    device_factory, run_sync, zabbix_host
):
    """The override goes through the same macro resolution as the setting.

    Easy to get wrong in the other direction -- returning the context value
    untouched would leave a literal `{datetime}` on the Zabbix host.
    """
    device = device_factory(
        config_context={"zabbix": {"description": "built {datetime}"}}
    )

    run_sync(device.name, description_dt_format="%Y")

    expected = f"built {datetime.now().strftime('%Y')}"
    assert description_of(zabbix_host(device.name)) == expected


def test_bad_macro_in_config_context_falls_back_to_static(
    device_factory, run_sync, zabbix_host
):
    """The override falls back to the *static* default, not to the setting.

    A user with `description = "the global default"` and a typo in one host's
    config context gets the static text on that host, not their configured
    description (host_description.py:100). Pinned because it is the kind of
    precedence a reasonable person would guess the other way.
    """
    device = device_factory(config_context={"zabbix": {"description": "{nonsense}"}})

    run_sync(device.name, description="the global default")

    assert description_of(zabbix_host(device.name)) == STATIC_DEFAULT


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The description is written once at host creation and never reconciled. "
        "`Description.generate()` is called only from `create_in_zabbix` "
        "(host.py:698-714); `consistency_check` compares status, templates, "
        "groups, interfaces, inventory, macros, tags and proxy, but not the "
        "description. So on any installation whose hosts already exist -- which "
        "is every installation past its first run -- editing `description` or "
        "`description_dt_format` silently does nothing. Remove this marker once "
        "consistency_check compares the description. Note that a fix has to "
        "keep `{datetime}` stable, or every host is rewritten on every run and "
        "'added on <date>' stops being true; see ISSUES.md issue 5."
    ),
)
def test_a_changed_description_reaches_an_existing_host(
    device_factory, run_sync, zabbix_host
):
    """Changing the setting should reach a host Zabbix already has."""
    device = device_factory()
    run_sync(device.name)
    assert description_of(zabbix_host(device.name)) == STATIC_DEFAULT

    run_sync(device.name, description="changed on the second run")

    assert description_of(zabbix_host(device.name)) == "changed on the second run"
