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
role that the default `hostgroup_format` of `site/manufacturer/role` requires.
Per-test devices come from the `device_factory` fixture.

The seeded template is `Linux by SNMP`, and that pairing is deliberate: a device
with no `zabbix` config context gets an **SNMP** interface (`host.py:496` calls
`set_default_snmp()`; only VMs default to agent), and Zabbix refuses to link an
agent template to a host with no agent interface.
