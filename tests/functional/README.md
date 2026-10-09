# Functional tests

These tests run the real `Sync` against a real NetBox and a real Zabbix in
Docker. The unit tests in `tests/` cover the logic of the sync against mocked
API responses; the functional tests cover what a mock cannot tell you:

- that Zabbix accepts the payloads the sync builds,
- that NetBox actually serves the data, filters and response shapes the sync
  relies on,
- and that this still holds for every NetBox and Zabbix version we support.

That last point is the main reason they exist. When a NetBox or Zabbix release
changes its API, these tests are where it shows up, and the CI matrix runs them
against each supported version combination.

They are **deselected by default**. `pytest` and `pytest tests` skip them via
`addopts = "-m 'not functional'"` in `pyproject.toml`, so the normal suite needs
no containers.

## Running locally

Bring the stack up, wait for it, and run the tests:

```bash
docker compose -f tests/functional/docker/compose.yaml up -d --wait
uv run python -m tests.functional.docker.wait_for_stack
uv run pytest tests/functional -m functional -v
```

First boot takes a couple of minutes while NetBox runs its migrations and Zabbix
imports its default templates. `wait_for_stack` waits for both.

Tear the stack down afterwards (`-v` also drops the databases, so the next run
starts clean):

```bash
docker compose -f tests/functional/docker/compose.yaml down -v
```

The tests provision their own NetBox API token and seed the NetBox objects they
need, so no manual setup is required.

### Using an existing stack

To run against NetBox and Zabbix instances you already have, set:

| Variable            | Default                   |
| ------------------- | ------------------------- |
| `FUNC_NETBOX_URL`   | `http://localhost:8000`   |
| `FUNC_NETBOX_TOKEN` | provisioned automatically |
| `FUNC_ZABBIX_URL`   | `http://localhost:8081`   |
| `FUNC_ZABBIX_USER`  | `Admin`                   |
| `FUNC_ZABBIX_PASS`  | `zabbix`                  |

With `FUNC_NETBOX_URL` set to an empty value the tests are skipped rather than
failed.

## Changing versions

The image tags come from `NETBOX_TAG` and `ZABBIX_TAG`. They are literal Docker
tags, so any tag the projects publish works (see `docker/.env.example`):

```bash
NETBOX_TAG=v4.7.0 ZABBIX_TAG=alpine-7.4-latest \
  docker compose -f tests/functional/docker/compose.yaml up -d --wait
```

In CI the versions come from the matrix in
`.github/workflows/functional_tests.yml`. Adding a version is one entry:

```yaml
matrix:
  include:
    - netbox: "v4.7.0"
      zabbix: "alpine-7.0-latest"
```

Mark a pre-release `experimental: true` to keep it from blocking the build.

## CI report

In GitHub Actions each matrix entry writes a summary to its job page: the pass
rate, a result breakdown, per-test timings and the traceback of anything that
failed. To produce the same report locally:

```bash
GITHUB_STEP_SUMMARY=/tmp/summary.md uv run pytest tests/functional -m functional
```

## Writing tests

- Drive the real `Sync` with an explicit config dict; don't reimplement it.
- Assert on the end state, read back through pynetbox and the Zabbix API
  directly. `Sync.start()` returns truthy even when every host fails, so its
  return value proves nothing.
- Give every test its own objects (the `device_factory`, `vm_factory`,
  `custom_field_factory` and similar fixtures in `conftest.py`) so tests can't
  interfere with each other and clean up after themselves.
- Test a setting in both its on and off state; a test that passes either way
  doesn't test the setting.

Tests marked `xfail(strict=True)` document known bugs: each asserts the
behaviour that _should_ hold, with the reason on the marker. Once the bug is
fixed the test starts passing, and the strict marker then fails the run as a
reminder to remove it.
