"""Fixtures for the functional suite.

Everything here is lazy. This module is imported even during the default
deselected run (`pytest tests` with `-m "not functional"` from addopts), so
nothing may connect to anything at import time.
"""

import contextlib
import os
from ipaddress import ip_interface
from itertools import count
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pynetbox
import pytest
import requests
from zabbix_utils import ZabbixAPI

from netbox_zabbix_sync import Sync
from tests.functional.bootstrap import seed_netbox
from tests.functional.bootstrap.netbox_token import provision_token

# Config the sync runs with. Built as an explicit dict and handed straight to
# Sync(), which merges it over DEFAULT_CONFIG and never calls load_config().
# That keeps the tests clear of config.py discovery and of the NBZX_* env vars,
# whose values are never coerced (NBZX_SYNC_VMS=False is the truthy *string*
# "False").
BASE_CONFIG = {
    "create_hostgroups": True,
    "hostgroup_format": "site/manufacturer/role",
    "create_journal": False,
    "skip_version_check": True,  # Running tests against version matrix, allow for experimental versions
}

# Distinguishes "the caller said None" from "the caller said nothing", where
# None is a meaningful value rather than an absence -- vm_factory's config
# context being the case that needs it.
UNSET = object()

# NetBox rejects a duplicate address in the global table, so every device and VM
# needs its own even when the test does not care what it is. 10.128/9 is left
# free by the tests that do hard-code an address (all of them in 10.0, 10.1,
# 10.20 or 192.168), so a generated address can never collide with a named one.
_address_counter = count()


def next_address() -> str:
    """An address no other host in the run is using."""
    n = next(_address_counter)
    return f"10.128.{n // 254}.{n % 254 + 1}/24"


FUNCTIONAL_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items):
    """Mark everything under tests/functional/ so it can't be forgotten.

    This hook is called with every collected item in the run, not just the ones
    below this conftest, so it must filter by path -- otherwise it marks the
    whole mocked suite as functional and deselects it.
    """
    for item in items:
        if FUNCTIONAL_DIR in Path(item.path).parents:
            item.add_marker(pytest.mark.functional)


# --- GitHub Actions job summary -------------------------------------------
#
# The report is written straight from the test run rather than from a JUnit
# file, so nothing is left on disk. Inert outside Actions: without
# GITHUB_STEP_SUMMARY set, none of this does anything.

ICONS = {"passed": "✅", "failed": "❌", "error": "💥", "skipped": "⏭️"}

# Test reports identify themselves by nodeid, which is rootdir-relative --
# unlike collection items, whose .path is absolute. Match on that.
FUNCTIONAL_NODEID_PREFIX = "tests/functional/"

_results: dict[str, dict] = {}


def pytest_runtest_logreport(report):
    """Accumulate per-test outcome and time across setup/call/teardown.

    Like the collection hook above, this fires for every test in the session,
    so it filters by path -- otherwise the mocked suite would report itself.
    """
    if not report.nodeid.startswith(FUNCTIONAL_NODEID_PREFIX):
        return

    entry = _results.setdefault(
        report.nodeid,
        {
            "name": report.nodeid.split("::")[-1],
            "time": 0.0,
            "outcome": "passed",
            "detail": "",
        },
    )
    entry["time"] += report.duration

    if report.failed:
        # A failure outside the call phase is an error (broken fixture), not a
        # failed assertion.
        entry["outcome"] = "failed" if report.when == "call" else "error"
        entry["detail"] = str(report.longrepr)
    elif report.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"


def _render_summary() -> str:
    counts = dict.fromkeys(ICONS, 0)
    for entry in _results.values():
        counts[entry["outcome"]] += 1

    executed = len(_results) - counts["skipped"]
    failed = counts["failed"] + counts["error"]
    duration = sum(entry["time"] for entry in _results.values())

    netbox = os.environ.get("NETBOX_TAG")
    zabbix = os.environ.get("ZABBIX_TAG")
    title = (
        f"NetBox `{netbox}` / Zabbix `{zabbix}`"
        if netbox and zabbix
        else "Functional test run"
    )

    lines = ["## Functional Tests", "", f"### {title}", ""]

    if executed == 0:
        lines.append("⏭️ **No tests executed** (all skipped)")
    else:
        # Skips are not failures, but they are not evidence either, so rate
        # against what actually ran and show the skip count separately.
        rate = counts["passed"] / executed * 100
        icon = "✅" if failed == 0 else "❌"
        lines.append(f"{icon} **{counts['passed']}/{executed} passed — {rate:.0f}%**")

    lines += [
        "",
        "| Result | Count |",
        "| --- | --- |",
        f"| ✅ Passed | {counts['passed']} |",
        f"| ❌ Failed | {counts['failed']} |",
        f"| 💥 Errors | {counts['error']} |",
        f"| ⏭️ Skipped | {counts['skipped']} |",
        f"| ⏱️ Duration | {duration:.1f}s |",
        "",
        "<details><summary>Per-test results</summary>",
        "",
        "| | Test | Time |",
        "| --- | --- | --- |",
    ]
    for entry in _results.values():
        lines.append(
            f"| {ICONS[entry['outcome']]} | `{entry['name']}` | {entry['time']:.2f}s |"
        )
    lines += ["", "</details>", ""]

    failures = [e for e in _results.values() if e["outcome"] in ("failed", "error")]
    if failures:
        lines += ["### Failures", ""]
        for entry in failures:
            lines += [
                f"<details><summary>{ICONS[entry['outcome']]} "
                f"<code>{entry['name']}</code></summary>",
                "",
                "```",
                entry["detail"].strip()[:2000],
                "```",
                "",
                "</details>",
                "",
            ]

    return "\n".join(lines)


def pytest_terminal_summary():
    """Append the report to the GitHub Actions job summary."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file or not _results:
        return
    with open(summary_file, "a", encoding="utf-8") as handle:
        handle.write(_render_summary() + "\n")


def _require_env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    if not value:
        pytest.skip(f"{name} is not set; functional stack unavailable")
    return value


@pytest.fixture(scope="session")
def netbox_url() -> str:
    return _require_env("FUNC_NETBOX_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def zabbix_url() -> str:
    return _require_env("FUNC_ZABBIX_URL", "http://localhost:8081")


@pytest.fixture(scope="session")
def zabbix_credentials() -> tuple[str, str]:
    return (
        os.environ.get("FUNC_ZABBIX_USER", "Admin"),
        os.environ.get("FUNC_ZABBIX_PASS", "zabbix"),
    )


@pytest.fixture(scope="session")
def netbox_token(netbox_url) -> str:
    """Use a token handed in by CI, else provision one directly."""
    token = os.environ.get("FUNC_NETBOX_TOKEN")
    if token:
        return token
    return provision_token(netbox_url)


@pytest.fixture(scope="session")
def nb(netbox_url, netbox_token):
    return pynetbox.api(netbox_url, token=netbox_token, threading=True)


@pytest.fixture(scope="session")
def zapi(zabbix_url, zabbix_credentials):
    user, password = zabbix_credentials
    api = ZabbixAPI(zabbix_url, user=user, password=password, skip_version_check=True)
    yield api
    api.logout()


@pytest.fixture(scope="session")
def seeded(nb):
    return seed_netbox.seed(nb)


@pytest.fixture
def device_factory(nb, zapi, seeded):
    """Create uniquely-named NetBox devices and clean up after the test.

    Cleanup covers Zabbix too: a synced device leaves a host behind, and the
    next test would otherwise see it.

    The optional arguments exist for the attribute-generation features. Config
    context arrives as `local_context_data`, the device-local layer NetBox
    merges into the rendered `config_context` the sync reads: it needs no
    ConfigContext object, no assignment rules, and it cannot leak into another
    test's device the way a site- or role-scoped context would.
    """
    created = []

    def make(
        status: str = "active",
        address: str | None = None,
        config_context: dict | None = None,
        tags: list[int] | None = None,
        dns_name: str = "",
        oob_address: str | None = None,
        address6: str | None = None,
        site: int | None = None,
        **fields,
    ):
        name = f"fn-{uuid4().hex[:8]}"
        device = nb.dcim.devices.create(
            name=name,
            device_type=seeded["device_type"].id,
            role=seeded["role"].id,
            # Named rather than left to **fields: the seeded site is the default
            # and would collide with a site= passed through as a plain field.
            # Overridden by the hostgroup tests, which need a site carrying a
            # region or a site group.
            site=site or seeded["site"].id,
            status=status,
            local_context_data=config_context,
            tags=tags or [],
            **fields,
        )
        # Deleted in list order, so anything that must go before the device it
        # points at is appended after it.
        dependents = [device]
        # Registered before the dependents exist so a failure below still tears
        # the device down. `dependents` is mutated in place from here on, so the
        # tuple already in `created` keeps seeing the additions.
        created.append((device, dependents))
        address = address or next_address()
        interface = nb.dcim.interfaces.create(
            device=device.id, name="eth0", type="1000base-t"
        )
        ip = nb.ipam.ip_addresses.create(
            address=address,
            dns_name=dns_name,
            assigned_object_type="dcim.interface",
            assigned_object_id=interface.id,
        )
        dependents += [ip, interface]
        device.primary_ip4 = ip.id

        if address6:
            # Hung off the same interface as the v4 address, which is how a
            # dual-stack host is normally modelled and keeps `preferred_ip`
            # the only thing choosing between the two.
            ip6 = nb.ipam.ip_addresses.create(
                address=address6,
                assigned_object_type="dcim.interface",
                assigned_object_id=interface.id,
            )
            dependents.insert(1, ip6)
            device.primary_ip6 = ip6.id

        if oob_address:
            oob_interface = nb.dcim.interfaces.create(
                device=device.id, name="mgmt0", type="1000base-t"
            )
            oob_ip = nb.ipam.ip_addresses.create(
                address=oob_address,
                assigned_object_type="dcim.interface",
                assigned_object_id=oob_interface.id,
            )
            dependents += [oob_ip, oob_interface]
            device.oob_ip = oob_ip.id

        device.save()
        # Refetched so the caller sees the primary IP it just assigned. The
        # copy registered for cleanup above stays as it is -- teardown only
        # needs the id and the name, and both are already on it.
        return nb.dcim.devices.get(device.id)

    yield make

    for device, dependents in reversed(created):
        for host in zapi.host.get(filter={"host": device.name}, output=["hostid"]):
            zapi.host.delete(host["hostid"])
        # The device goes last: NetBox refuses to delete an IP's interface
        # while the device still names that IP as its primary or OOB.
        for obj in (*dependents[1:], device):
            # Already gone, or removed by a cascading delete.
            with contextlib.suppress(pynetbox.RequestError):
                obj.delete()


def vm_context(**zabbix_keys) -> dict:
    """A VM config context carrying the agent template, plus whatever is added.

    Every VM that is expected to sync needs one. A VM takes its templates from
    the config context alone -- `set_vm_template` deliberately skips the custom
    field lookup devices use (virtual_machine.py:35) -- and core.py:371 drops a
    VM with no templates before it ever reaches Zabbix. So a VM test that sets a
    config context for some other purpose has to carry the template along, or it
    silently stops testing what it meant to.
    """
    return {"zabbix": {"templates": [seed_netbox.ZABBIX_VM_TEMPLATE], **zabbix_keys}}


@pytest.fixture
def vm_factory(nb, zapi, seeded):
    """Create uniquely-named NetBox VMs and clean up after the test.

    The device_factory's counterpart. Two differences are worth knowing:
    `config_context` defaults to `vm_context()` rather than nothing, because a
    VM without one never reaches Zabbix at all (see `vm_context`); and the VM
    gets `site` as well as `cluster`, since the default vm_tag_map maps
    `site/name` and a site is optional for VMs.
    """
    created = []

    def make(
        status: str = "active",
        address: str | None = None,
        config_context=UNSET,
        tags: list[int] | None = None,
        dns_name: str = "",
        **fields,
    ):
        name = f"fn-vm-{uuid4().hex[:8]}"
        vm = nb.virtualization.virtual_machines.create(
            name=name,
            cluster=seeded["cluster"].id,
            role=seeded["role"].id,
            site=seeded["site"].id,
            status=status,
            local_context_data=vm_context()
            if config_context is UNSET
            else config_context,
            tags=tags or [],
            **fields,
        )
        dependents = [vm]
        # Registered before its dependents, so a failure below still cleans up.
        # See the same note in device_factory.
        created.append((vm, dependents))
        interface = nb.virtualization.interfaces.create(
            virtual_machine=vm.id, name="eth0"
        )
        ip = nb.ipam.ip_addresses.create(
            address=address or next_address(),
            dns_name=dns_name,
            assigned_object_type="virtualization.vminterface",
            assigned_object_id=interface.id,
        )
        dependents += [ip, interface]
        vm.primary_ip4 = ip.id
        vm.save()
        return nb.virtualization.virtual_machines.get(vm.id)

    yield make

    for vm, dependents in reversed(created):
        for host in zapi.host.get(filter={"host": vm.name}, output=["hostid"]):
            zapi.host.delete(host["hostid"])
        # The VM goes last, for the same reason the device does: NetBox refuses
        # to delete the interface an IP hangs off while the VM still names that
        # IP as its primary.
        for obj in (*dependents[1:], vm):
            with contextlib.suppress(pynetbox.RequestError):
                obj.delete()


@pytest.fixture
def tag_factory(nb):
    """Create uniquely-named NetBox tags and remove them afterwards.

    Tags are global, like custom fields, so each test gets its own rather than
    risking a leftover tag turning up in another test's Zabbix host tags.
    """
    created = []

    def make(name: str | None = None):
        slug = f"tag-{uuid4().hex[:8]}"
        tag = nb.extras.tags.create(name=name or slug.upper(), slug=slug)
        created.append(tag)
        return tag

    yield make

    for tag in reversed(created):
        with contextlib.suppress(pynetbox.RequestError):
            tag.delete()


@pytest.fixture
def virtual_chassis_factory(nb, zapi):
    """Create a virtual chassis over existing devices, master first.

    Torn down before `device_factory`'s devices: this fixture is requested
    after it, so its finalizer runs first, and NetBox nulls the members'
    `virtual_chassis` on delete rather than blocking on it.

    Cleanup deletes the Zabbix host named after the chassis as well. With
    `clustering` on, the master is promoted to the chassis name (device.py:57),
    so the host it leaves behind is not named after any device and
    `device_factory` would never find it.
    """
    created = []

    def make(master, *members, domain: str = ""):
        # domain is nullable in NetBox's model but not over the API, which
        # rejects an explicit null with a 400.
        vc = nb.dcim.virtual_chassis.create(
            name=f"vc-{uuid4().hex[:8]}", master=master.id, domain=domain
        )
        for position, device in enumerate((master, *members), start=1):
            device.virtual_chassis = vc.id
            device.vc_position = position
            device.save()
        created.append(vc)
        return vc

    yield make

    for vc in reversed(created):
        for host in zapi.host.get(filter={"host": vc.name}, output=["hostid"]):
            zapi.host.delete(host["hostid"])
        with contextlib.suppress(pynetbox.RequestError):
            vc.delete()


@pytest.fixture
def sync_runner(
    netbox_url, netbox_token, zabbix_url, zabbix_credentials, _netbox_recording
):
    """Run the real Sync with full control over both filters and the config.

    `run_sync` is the common case built on this one. Use this fixture directly
    when the filters themselves are what's under test, or when `start()` is
    expected to raise -- the logout here is in a `finally`, so a raising sync
    still hands its Zabbix session back.
    """
    user, password = zabbix_credentials

    def _run(device_filter=None, vm_filter=None, **overrides):
        syncer = Sync(config={**BASE_CONFIG, **overrides})
        assert syncer.connect(
            nb_host=netbox_url,
            nb_token=netbox_token,
            zbx_host=zabbix_url,
            zbx_user=user,
            zbx_pass=password,
            skip_version_check=BASE_CONFIG["skip_version_check"],
        ), "Sync.connect() failed; it returns False rather than raising"
        # Everything up to here -- fixture setup, seeding, connect()'s auth
        # probe -- is noise to `netbox_requests`, whose contract is the traffic
        # of the sync itself.
        _netbox_recording.clear()
        try:
            # Sync.start() catches SyncError per host and still returns truthy,
            # so its return value proves nothing. Assert on end state instead.
            syncer.start(device_filter=device_filter, vm_filter=vm_filter)
        finally:
            syncer.logout()

    return _run


@pytest.fixture
def run_sync(sync_runner):
    """Run the real Sync, scoped to one device."""

    def _run(device_name: str, **overrides):
        sync_runner(device_filter={"name": device_name}, **overrides)

    return _run


# A device name no device has, used to scope a VM sync down to its VM. The
# devices half of start() runs regardless of sync_vms, so without this a VM test
# also syncs whatever devices another test left in NetBox.
NO_DEVICES = {"name": "fn-no-such-device"}


@pytest.fixture
def run_vm_sync(sync_runner):
    """Run the real Sync, scoped to one VM, with sync_vms on.

    `sync_vms` defaults to False, so a VM test that forgets it passes an empty
    NetBox for the VM half and asserts nothing. It is set here rather than left
    to each test, and can still be overridden to test the off state.
    """

    def _run(vm_name: str, **overrides):
        sync_runner(
            device_filter=NO_DEVICES,
            vm_filter={"name": vm_name},
            **{"sync_vms": True, **overrides},
        )

    return _run


class Exchange:
    """One NetBox request the sync made, and what came back."""

    def __init__(self, method: str, url: str, response):
        self.method = method
        self.url = url
        self._response = response

    @property
    def params(self) -> dict[str, list[str]]:
        """The query string, as pynetbox sent it."""
        return parse_qs(urlparse(self.url).query)

    @property
    def results(self) -> list[dict]:
        """The `results` of a NetBox list response, else an empty list."""
        try:
            body = self._response.json()
        except ValueError:
            return []
        return body.get("results", []) if isinstance(body, dict) else []


@pytest.fixture
def _netbox_recording(monkeypatch):
    """Record NetBox traffic at `requests.Session.send`.

    That is the one chokepoint every pynetbox call passes through, including
    the threaded ones -- Sync builds its own `nbapi(..., threading=True)`
    internally and never exposes the session, so there is nothing narrower to
    hook. Zabbix traffic goes through zabbix_utils rather than requests, so
    none of it lands here.

    Recording the *request* alone would be a trap: NetBox silently ignores
    filter params it does not know, so a param in a query string is no evidence
    it did anything. The response is kept so a test can assert on what the
    filter actually excluded. Reading `.json()` off it is safe -- these
    responses are not streamed, so the body is already in memory and stays
    readable by pynetbox afterwards.
    """
    recorded = []
    original = requests.Session.send

    def spy(self, request, **kwargs):
        response = original(self, request, **kwargs)
        recorded.append(Exchange(request.method, request.url, response))
        return response

    monkeypatch.setattr(requests.Session, "send", spy)
    return recorded


@pytest.fixture
def netbox_requests(_netbox_recording):
    """The NetBox requests made by the most recent `Sync.start()`.

    Only that: `sync_runner` empties the recording just before it calls
    `start()`, so fixture setup, the seeding, and `connect()`'s own auth probe
    (a `.count()` on the device endpoint) are all discarded rather than left
    for each test to filter back out. Without that the assertions here would
    depend on which test first triggered the session-scoped seed.
    """
    return _netbox_recording


@pytest.fixture
def custom_field_factory(nb):
    """Create uniquely-named custom fields and remove them afterwards.

    Names are unique per test because a custom field is global: a leftover one
    from a previous run would be picked up by `verify_hg_format` and quietly
    change what these tests prove.
    """
    created = []

    def make(object_types: list[str], cf_type: str = "text") -> str:
        name = f"cf_{uuid4().hex[:8]}"
        created.append(
            nb.extras.custom_fields.create(
                name=name,
                label=name,
                type=cf_type,
                object_types=object_types,
            )
        )
        return name

    yield make

    for cf in reversed(created):
        with contextlib.suppress(pynetbox.RequestError):
            cf.delete()


@pytest.fixture
def zabbix_host(zapi):
    """Read a Zabbix host back with everything the assertions need.

    Tags, macros and inventory are selected here rather than in a second
    fixture because Zabbix omits each of them unless asked, and a test that
    forgot the select would read an absent key as an absent value -- passing
    for a host whose tags were never synced.
    """

    def _get(name: str):
        hosts = zapi.host.get(
            filter={"host": name},
            output="extend",
            selectHostGroups=["name"],
            selectParentTemplates=["host"],
            selectInterfaces="extend",
            selectTags="extend",
            selectMacros="extend",
            selectInventory="extend",
        )
        return hosts[0] if hosts else None

    return _get


def tag_pairs(host: dict) -> set[tuple[str, str]]:
    """The host's Zabbix tags as (tag, value) pairs."""
    return {(tag["tag"], tag["value"]) for tag in host["tags"]}


def macro_values(host: dict) -> dict[str, str | None]:
    """The host's Zabbix usermacros, keyed by macro name.

    A secret macro's value is `None`: Zabbix withholds it by dropping the key
    from the response entirely, rather than blanking it. Read `macro_types` to
    assert on one.
    """
    return {macro["macro"]: macro.get("value") for macro in host["macros"]}


def macro_types(host: dict) -> dict[str, str]:
    """The host's usermacro types (0 text, 1 secret, 2 vault), by macro name."""
    return {macro["macro"]: macro["type"] for macro in host["macros"]}


def strip_ip_mask(address: str) -> str:
    return str(ip_interface(address).ip)


def requests_to(exchanges: list[Exchange], path: str) -> list[Exchange]:
    """The recorded exchanges whose URL path is exactly `path`.

    Matched on the parsed path rather than a substring so that
    `/api/dcim/devices/` does not also collect `/api/dcim/device-types/`.
    """
    return [ex for ex in exchanges if urlparse(ex.url).path == path]
