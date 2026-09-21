"""Provision an API token against a freshly started NetBox.

Run as a module to print a token to stdout, which is how the CI workflow feeds
one into the test step::

    python -m tests.functional.bootstrap.netbox_token http://localhost:8000
"""

import sys

import requests

DEFAULT_URL = "http://localhost:8000"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"

TOKEN_VERSION_V2 = 2


def provision_token(
    url: str = DEFAULT_URL,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    timeout: int = 30,
) -> str:
    """Ask NetBox for an API token and return it in its usable form.

    Uses the token provisioning endpoint rather than the container's
    SUPERUSER_API_TOKEN path, which is pepper-gated, v2-only, and would mean
    hard-coding a token format that differs across NetBox releases.

    NetBox >= 4.5 returns a v2 token split over two fields: ``key`` is the
    public identifier and ``token`` the secret. Neither authenticates alone --
    the string the API accepts is ``nbt_{key}.{token}``. Older releases return a
    single 40-character v1 ``key``, which is used as-is.
    """
    response = requests.post(
        f"{url}/api/users/tokens/provision/",
        json={"username": username, "password": password},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("version") == TOKEN_VERSION_V2:
        return f"nbt_{payload['key']}.{payload['token']}"
    return payload["key"]


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(provision_token(url))
