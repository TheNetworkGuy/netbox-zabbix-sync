"""Block until the functional stack is actually usable.

`docker compose up --wait` only honours healthchecks, which is not enough here:
the Zabbix API answers apiinfo.version as soon as the schema exists, but the
default templates are imported some seconds later. Polling the API alone gives
an intermittent "template not found" in the first test that runs.
"""

import os
import sys
import time

import requests

from tests.functional.bootstrap.seed_netbox import ZABBIX_TEMPLATE

NETBOX_URL = os.environ.get("FUNC_NETBOX_URL", "http://localhost:8000")
ZABBIX_URL = os.environ.get("FUNC_ZABBIX_URL", "http://localhost:8081")
ZABBIX_USER = os.environ.get("FUNC_ZABBIX_USER", "Admin")
ZABBIX_PASS = os.environ.get("FUNC_ZABBIX_PASS", "zabbix")

DEADLINE_SECONDS = 300
INTERVAL_SECONDS = 3

HTTP_OK = 200


def _rpc(method: str, params, auth: str | None = None):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    response = requests.post(
        f"{ZABBIX_URL}/api_jsonrpc.php",
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def netbox_ready() -> bool:
    """NetBox serves /login/ without auth once migrations are done.

    /api/status/ is not usable as a probe: NetBox defaults to LOGIN_REQUIRED,
    so it answers 403 even when perfectly healthy.
    """
    return requests.get(f"{NETBOX_URL}/login/", timeout=10).status_code == HTTP_OK


def zabbix_ready() -> bool:
    """True once the API answers *and* the default templates are imported."""
    if "result" not in _rpc("apiinfo.version", {}):
        return False
    login = _rpc("user.login", {"username": ZABBIX_USER, "password": ZABBIX_PASS})
    if "result" not in login:
        return False
    templates = _rpc(
        "template.get",
        {"filter": {"host": ZABBIX_TEMPLATE}, "output": ["host"]},
        auth=login["result"],
    )
    return bool(templates.get("result"))


def main() -> int:
    checks = {"netbox": netbox_ready, "zabbix": zabbix_ready}
    deadline = time.monotonic() + DEADLINE_SECONDS

    while checks:
        for name, check in list(checks.items()):
            try:
                if check():
                    print(f"{name}: ready")
                    del checks[name]
            except (requests.RequestException, ValueError, KeyError):
                pass  # not up yet; keep polling until the deadline

        if not checks:
            break
        if time.monotonic() > deadline:
            print(
                f"timed out after {DEADLINE_SECONDS}s waiting for: "
                f"{', '.join(sorted(checks))}",
                file=sys.stderr,
            )
            return 1
        time.sleep(INTERVAL_SECONDS)

    print("stack ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
