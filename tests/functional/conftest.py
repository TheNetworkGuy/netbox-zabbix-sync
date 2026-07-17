"""Fixtures for the functional suite.

Everything here is lazy. This module is imported even during the default
deselected run (`pytest tests` with `-m "not functional"` from addopts), so
nothing may connect to anything at import time.
"""

import contextlib
import os
from ipaddress import ip_interface
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
    """
    created = []

    def make(status: str = "active", address: str = "10.0.0.1/24"):
        name = f"fn-{uuid4().hex[:8]}"
        device = nb.dcim.devices.create(
            name=name,
            device_type=seeded["device_type"].id,
            role=seeded["role"].id,
            site=seeded["site"].id,
            status=status,
        )
        interface = nb.dcim.interfaces.create(
            device=device.id, name="eth0", type="1000base-t"
        )
        ip = nb.ipam.ip_addresses.create(
            address=address,
            assigned_object_type="dcim.interface",
            assigned_object_id=interface.id,
        )
        device.primary_ip4 = ip.id
        device.save()
        device = nb.dcim.devices.get(device.id)
        created.append((device, interface, ip))
        return device

    yield make

    for device, interface, ip in reversed(created):
        for host in zapi.host.get(filter={"host": device.name}, output=["hostid"]):
            zapi.host.delete(host["hostid"])
        for obj in (ip, interface, device):
            # Already gone, or removed by a cascading delete.
            with contextlib.suppress(pynetbox.RequestError):
                obj.delete()


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
    """Read a Zabbix host back with everything the assertions need."""

    def _get(name: str):
        hosts = zapi.host.get(
            filter={"host": name},
            output="extend",
            selectHostGroups=["name"],
            selectParentTemplates=["host"],
            selectInterfaces="extend",
        )
        return hosts[0] if hosts else None

    return _get


def strip_ip_mask(address: str) -> str:
    return str(ip_interface(address).ip)


def requests_to(exchanges: list[Exchange], path: str) -> list[Exchange]:
    """The recorded exchanges whose URL path is exactly `path`.

    Matched on the parsed path rather than a substring so that
    `/api/dcim/devices/` does not also collect `/api/dcim/device-types/`.
    """
    return [ex for ex in exchanges if urlparse(ex.url).path == path]
