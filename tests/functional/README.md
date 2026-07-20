# Functional tests

These tests run the real `Sync` against a real NetBox and a real Zabbix in
Docker, rather than against the mocks used by the rest of `tests/`. They exist
to catch the things a mock cannot: that Zabbix still accepts the payloads we
build, that a template and interface type are actually compatible, and that the
custom-field write-back round-trips through NetBox.

They are **deselected by default**. `pytest` and `pytest tests` skip them via
`addopts = "-m 'not functional'"` in `pyproject.toml`, so the normal suite is
unaffected and needs no containers.

## Running locally

Bring the stack up, wait for it, run the tests:

```bash
docker compose -f tests/functional/docker/compose.yaml up -d --wait
uv run python -m tests.functional.docker.wait_for_stack
uv run pytest tests/functional -m functional -v
```

Tear it down (`-v` also drops the databases, so the next run starts clean):

```bash
docker compose -f tests/functional/docker/compose.yaml down -v
```

First boot takes a couple of minutes: NetBox runs its migrations and Zabbix
imports its schema and ~350 default templates. `wait_for_stack.py` polls for
both, including the template import specifically — the Zabbix API starts
answering before the templates exist, so waiting on the API alone is a race.

The tests provision their own NetBox API token, so no credentials are needed.
To point them at an existing stack, set `FUNC_NETBOX_URL`, `FUNC_NETBOX_TOKEN`,
`FUNC_ZABBIX_URL`, `FUNC_ZABBIX_USER` and `FUNC_ZABBIX_PASS`. With
`FUNC_NETBOX_URL` unset to an empty value the tests skip rather than fail.

## Changing versions

Image tags come from `NETBOX_TAG` and `ZABBIX_TAG`. They are literal Docker
tags — there is no translation from a version number, so anything the projects
publish works. See `.env.example`.

```bash
ZABBIX_TAG=alpine-7.4-latest NETBOX_TAG=v4.6.5 \
  docker compose -f tests/functional/docker/compose.yaml up -d --wait
```

In CI the versions are the matrix in `.github/workflows/functional_tests.yml`.
Adding a version is one line:

```yaml
matrix:
  include:
    - netbox: "v4.6.5"
      zabbix: "alpine-7.0-latest"
    - netbox: "v4.6.5"
      zabbix: "alpine-7.4-latest"
```

A pre-release can be marked `experimental: true` so `continue-on-error` keeps it
from blocking the build.

## The CI test report

Each matrix entry writes a report to its job summary on the workflow run page:
a pass percentage, a result breakdown, per-test timings, and the traceback for
anything that failed. The title carries the two image tags, so with several
matrix entries each job's summary says which combination it describes.

The report is written by hooks in `conftest.py` directly from the test run, so
there is no report file on disk and no separate reporting step — a failing run
reports for the same reason a passing one does. Skips are rated separately: the
percentage is passed/*executed*, so a skipped test does not read as a failure.

The hooks are inert outside GitHub Actions; they do nothing unless
`GITHUB_STEP_SUMMARY` is set. To see one locally:

```bash
GITHUB_STEP_SUMMARY=/tmp/summary.md uv run pytest tests/functional -m functional
cat /tmp/summary.md
```

Both hooks filter by node ID, so the report only ever covers
`tests/functional/`. Note that pytest hooks fire for every test in the session,
not just those under this directory's `conftest.py` — without that filter the
mocked suite would report itself.

## How the tests are built

- **The real `Sync` is driven, never reimplemented.** `Sync(config=dict)` merges
  over `DEFAULT_CONFIG` and never calls `load_config()`, so the tests pass an
  explicit dict and sidestep both `config.py` discovery and the `NBZX_*` env
  vars (whose values are never coerced — `NBZX_SYNC_VMS=False` is the truthy
  *string* `"False"`).
- **Assertions read state back through raw pynetbox / ZabbixAPI calls**, not
  through app code, so a bug in `host.py` cannot mask itself.
- **Never assert on `Sync.start()`'s return value.** It catches `SyncError` per
  host and still returns truthy, so a test doing that would pass while syncing
  nothing. Assert on end state.
- **Each test gets a uniquely-named device** and scopes the sync to it with
  `Sync.start(device_filter=...)`, so tests cannot interfere with each other.

`seed_netbox.py` creates the shared objects: the `zabbix_hostid` and
`zabbix_template` custom fields, plus the site, manufacturer, device type and
role that the default `hostgroup_format` of `site/manufacturer/role` requires,
and a cluster and cluster type for the VM tests' `cluster_type/cluster/role`.
The site also carries latitude/longitude, which `extended_site_properties` is
the only way to reach. Per-test devices come from the `device_factory` fixture
and per-test VMs from `vm_factory`.

Both factories allocate a unique IP by default, out of `10.128/9`, which is kept
clear of every hard-coded address in the suite. Pass `address=` only when the
test asserts on the value; NetBox rejects a duplicate address globally, so a
shared default would make any two-device test fail for a reason unrelated to what
it is testing. Both factories also register their object for cleanup *before*
creating its interface and IP, so a failure part-way through still tears down.

The seeded template is `Linux by SNMP`, and that pairing is deliberate: a device
with no `zabbix` config context gets an **SNMP** interface (`host.py:496` calls
`set_default_snmp()`; only VMs default to agent), and Zabbix refuses to link an
agent template to a host with no agent interface.

## Testing the NetBox filters

`test_netbox_filters.py` covers the filters the sync sends and exists because of
a bug that mocks are structurally unable to catch: the sync asked for device
custom fields with `content_types="dcim.device"`, and **NetBox silently ignored
the parameter** and returned every custom field — behaving exactly as it does
for a made-up `bogus_filter=xyz`. The param that filters is `object_type`
(singular); `object_types` is ignored too. Note the asymmetry with writes, where
the field really is `object_types` — `seed_netbox.py` uses that to *create* a
custom field, while the sync uses `object_type` to *filter* one.

The rule that follows, and the reason these tests are shaped the way they are:

> **A filter in the query string is not evidence that it filtered.** A mocked
> `custom_fields.filter()` asserts the kwarg you passed, which is precisely the
> thing that was wrong. Only a real NetBox can say whether it did anything.

So every filter test seeds a **decoy** the filter must exclude — a VM-only
custom field, a second device — and asserts on what came back. Two fixtures
support this:

- **`sync_runner`** — `run_sync` with full control over `device_filter`,
  `vm_filter` and config overrides. Its logout is in a `finally`, so it also
  suits syncs expected to raise (`verify_hg_format` raises `HostgroupError`
  straight out of `start()`).
- **`netbox_requests`** — every NetBox request of the most recent
  `Sync.start()`, with its response, recorded at `requests.Session.send`.
  That hook point is not a preference: `Sync` builds its own
  `nbapi(..., threading=True)` internally and never exposes the session.
  `sync_runner` clears the recording just before `start()`, so fixture setup,
  seeding and `connect()`'s auth probe stay out of the assertions and results
  do not depend on which test happened to trigger the session-scoped seed.

Use the recorded **response** (`.results`) to prove a filter worked, and the
recorded **request** (`.params`) only to pin down *how* — server-side rather
than in Python, once per run rather than once per host. Asserting a request
count is how the N+1 tests work: custom field definitions are fetched once no
matter how many devices sync, and `sync_vms=False` must touch neither VM
endpoint.

Custom fields are global in NetBox, so a leftover one would quietly change what
these tests prove; `custom_field_factory` gives each test unique names and
deletes them afterwards. `tag_factory` exists for the same reason.

## Testing the generated host attributes

`test_host_attributes.py` (tags, usermacros, inventory), `test_config_context.py`
(interface/template config, Jinja2 rendering) and `test_extended_models.py` (the
`extended_*` settings) cover the mapping features. The mocked suite already
covers how those structures are *built*; these cover the two ends a mock cannot:

- **Zabbix accepts them.** An inventory key that is not one of Zabbix's ~70
  inventory fields is an API error, not a stored value. A mocked test asserting
  on the dict handed to a mock passes with a typo'd field name in the map.
- **NetBox serves what the map asks for.** A map names fields by path
  (`site/latitude`); whether that path exists on the object NetBox actually
  returns is not something a `DummyNB` can answer.

Devices get their config context from `local_context_data`, the device-local
layer NetBox merges into the rendered `config_context`. It needs no
ConfigContext object or assignment rules, and it cannot leak into another
test's device the way a site- or role-scoped context would.

The rule from the filter tests has a counterpart here: **a feature switch that
is only tested in its on state is not tested.** Asserting tags appear with
`tag_sync=True` proves nothing unless something also proves they stay away when
it is off — so each switch is paired with its off state.

`test_host_attributes.py` also covers what a *bad* map does, which is where the
two ends part company. A key Zabbix rejects — a typo'd inventory field, a macro
or tag value past Zabbix's length limit — fails only its own host: Zabbix
refuses the `host.create`, the sync catches it and moves on. A NetBox path that
does not resolve does the opposite and ends the whole run (see the xfail
markers). The client-side length checks in `usermacros.py` and `tags.py` are
load-bearing for the same reason — dropping an oversized value before the call
is what keeps one verbose NetBox `comments` field from failing the host.

## Testing the shipped default maps

Every other mapping test names its own map, which exercises the mechanism and
says nothing about the ~20 mappings in `DEFAULT_CONFIG` that users get out of
the box. `test_default_maps.py` covers those specifically: it passes **no** map,
lets the defaults apply, and asserts on what Zabbix stored. It is the only test
that fails when a shipped default drifts from either end — a Zabbix inventory
field that no longer exists, or a NetBox path (`device_type/manufacturer/name`)
that a NetBox release stops nesting. It asserts every entry rather than a
sample, because a spot check leaves the rest free to rot, and it runs against
both a fully-populated device (every path resolves) and a bare one (every
nullable path maps to `""` rather than raising).

## Testing hostgroups and VMs

`test_hostgroups.py` covers the most visible mapping of all — where each host
lands in Zabbix. Beyond the default `site/manufacturer/role`, it exercises the
things only a real NetBox tree can show: nested regions and site groups walked
by `build_path` (which reconstructs ancestry from `_depth`/`parent`), a quoted
literal segment, a custom-field segment, and an empty segment dropping out. Each
`traverse_*` flag is paired with its off state.

`test_vm_sync.py` is the VM counterpart to the device tests. A VM is not a
device with a different endpoint: it takes a different branch in `start()`, a
different class, and four different config keys (`vm_inventory_map`,
`vm_usermacro_map`, `vm_tag_map`, `vm_hostgroup_format`), none of which a device
test reaches. Two divergences are the kind only a real Zabbix enforces: a VM
defaults to an **agent** interface where a device defaults to SNMP, and a VM's
templates come **only** from its config context (there is no custom-field
fallback), so a VM with no `zabbix` context is dropped before Zabbix sees it —
which is why `vm_factory` gives every VM one by default.

## Testing the settings one by one

Four files exist to cover the settings in `DEFAULT_CONFIG` that nothing else
reaches. They share a rule, which is the one worth internalising before adding
to them:

> **A setting is only tested if changing it changes the assertion.** A test that
> sets `zabbix_device_disable` to a status the default list already contains
> passes whether or not the setting was ever read. So each test either drives a
> value the defaults put somewhere else, or is paired with an off-state test
> using identical NetBox data.

- **`test_status_and_journal.py`** — `zabbix_device_removal` and
  `zabbix_device_disable` (both driven with statuses the defaults classify
  differently), `create_hostgroups`, and `create_journal`, the one setting whose
  effect lands in NetBox rather than Zabbix. Note the config lists are written in
  status **labels** (`"Offline"`), not slugs — `Host.status` is `nb.status.label`.
- **`test_interfaces.py`** — `preferred_ip` and `oob_sync`. Every `preferred_ip`
  test uses a dual-stack device, because that is the only thing that tells the
  three values apart; `"auto"` defers to NetBox, which resolves `primary_ip` to
  v6 when both exist. `oob_sync` has a sharp edge worth knowing: both interfaces
  fall back to SNMP, `_verify_interfaces` rejects duplicate types, so enabling
  the flag without an `oob_interface_type` in config context drops every device
  that has an OOB IP.
- **`test_proxies.py`** — `proxy_cf`, `proxy_group_cf` and `full_proxy_sync`,
  against proxies that really exist in Zabbix, since the whole feature is a
  name lookup. Covers the three-level precedence (device custom field, then the
  same field on the site, then config context) with each level set to a
  *different* proxy so the order is observable. `full_proxy_sync` is the only
  setting in the suite that deletes Zabbix configuration, so both its states are
  pinned.
- **`test_templates.py`** — `template_cf`. Every test uses a custom field whose
  name the default would never find, because the seed data uses the default name
  and a sync ignoring the setting would otherwise pass.

Two teardown-ordering traps show up here and are worth copying rather than
rediscovering. Zabbix refuses to delete a hostgroup that still holds a host, or a
proxy a host still points at — so `zabbix_hostgroups` and `proxy_factory` are
requested **before** `device_factory` in a test signature. Finalizers run in
reverse setup order, so first in the signature means last at teardown. And any
test that writes a custom field onto a session-seeded object (the site, the
device type) refetches it first: the shared `Record` keeps whatever
`custom_fields` it last saved, including ones a fixture has since deleted, and
sending those back is a 400.

Two bugs found here are filed as ISSUES.md 5 and 6 and pinned as xfails (see
below). A third finding is pinned as a **passing** test instead: **turning
`oob_sync` off does not remove the interface** from a host that already synced,
because Zabbix refuses to delete an interface with template items bound to it.
The sync logs the rejection and carries on, so the setting is effectively
one-way. That is pinned as-is rather than xfailed deliberately — forcing the
removal would mean unlinking Zabbix items the sync did not create, so an xfail
would encode a fix nobody should make (ISSUES.md issue 7).

## The xfail markers

Seven tests are marked `xfail(strict=True)`. They are not flaky, and they are not
aspirational: each is a bug these tests found, asserting the behaviour that
should hold, with the reason on the marker. Strict means they fail the moment
the behaviour is fixed, which is the signal to drop the marker. Each corresponds
to a numbered entry in `ISSUES.md`, where the trade-offs a fix has to weigh are
written up.

- `field_mapper` raises `KeyError` on a mapped field NetBox did not nest, rather
  than mapping it to `""` like an empty value. It escapes `Sync.start()`, which
  catches `SyncError` only, so one such field ends the whole run
  (`test_unextended_mapped_field_aborts_the_run`, and at unit level
  `tests/test_tools.py::TestFieldMapper::test_absent_field_maps_to_empty_string`).
  Contrast `test_unknown_inventory_field_costs_only_its_own_host`, which pins the
  Zabbix-side version of the same user error: it fails one host, not the run.
- `render_config_context=True` skips every host whose config context has no
  usable `zabbix` key — which is most of an inventory — because
  `jinjafy_config_context` returns `{}` for those and `core.py:211` reads a
  falsy render as a failure (`test_rendering_skips_hosts_with_no_zabbix_context`,
  three parametrised cases).
- `build_path` matches ancestors by **name**, but NetBox only enforces unique
  region names at the top level. A region name reused under two parents makes the
  ancestry ambiguous, `build_path` returns `[]`, and the device silently lands in
  a hostgroup with the whole region path missing
  (`test_ambiguous_region_name_still_traverses`, and at unit level
  `tests/test_tools.py::TestBuildPath::test_reused_ancestor_name_still_resolves`).
- The **host description is never reconciled**. `Description.generate()` is
  called only from `create_in_zabbix`, so `description` and
  `description_dt_format` silently do nothing on any host Zabbix already has
  (`test_a_changed_description_reaches_an_existing_host`). The test uses a
  macro-free description on purpose, so it does not prejudge how a fix should
  handle `{datetime}` — see ISSUES.md issue 5.
- A **meaningless `description_dt_format` reaches Zabbix verbatim**. `strftime`
  passes unknown directives like `%Q` through rather than raising, so the
  `except ValueError` guard is unreachable and only a non-string format falls
  back (`test_a_meaningless_dt_format_falls_back_to_the_default`).

One non-obvious finding is pinned as a passing test rather than an xfail:
`extended_site_properties` is a no-op for devices. `Hostgroup` reads
`self.nb.site.region` for every device with a site, and a nested pynetbox
`Record` lazily fetches its full details on *attribute* access, so the site is
fully populated either way — same data, same request count with the flag on and
off (`test_site_costs_one_fetch_per_host_either_way`). That also explains why
`field_mapper` misses fields that are "there": it walks with `Record[key]`,
which is `dict(self)[key]` and does not lazy-load.
