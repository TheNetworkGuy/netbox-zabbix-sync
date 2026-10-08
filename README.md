# NetBox to Zabbix synchronization

A script to create, update and delete Zabbix hosts using NetBox device and virtual machine objects, keeping NetBox the single source of truth. Tested and compatible with all [currently supported Zabbix releases](https://www.zabbix.com/life_cycle_and_release_policy).

The full documentation is in the [project wiki](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki).

## Features

- Syncs NetBox **devices** and, optionally, **virtual machines** to Zabbix hosts
- Creates, updates, disables and deletes hosts based on the NetBox status
- Builds **hostgroups** from NetBox data such as site, region, role, tenant or custom fields
- Links **templates** from a device type custom field or from config context
- Configures agent, SNMP, IPMI and JMX **interfaces**, and out-of-band interfaces, from config context
- Assigns **proxies** and **proxy groups**
- Syncs **inventory**, **tags** and **usermacros** from NetBox fields and config context
- Treats virtual chassis as a single Zabbix host (**clustering**)
- Writes **journal entries** in NetBox for every change

## Requirements

- A NetBox instance and an API token with write access
- A Zabbix server and an API token or user
- Python 3.12 or later, unless you use the Docker image
- A `zabbix_hostid` custom field in NetBox, see [Preparation](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Netbox-preperations)

## Quick start

After [preparing NetBox](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Netbox-preperations), run a sync with Docker:

```bash
docker run --rm \
  -e NETBOX_HOST=https://netbox.example.com \
  -e NETBOX_TOKEN=your_netbox_token \
  -e ZABBIX_HOST=https://zabbix.example.com \
  -e ZABBIX_TOKEN=your_zabbix_token \
  ghcr.io/thenetworkguy/netbox-zabbix-sync:latest
```

Each run performs one sync and exits, so run it on a schedule to keep Zabbix up to date. See the [Quick Start](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Quick-Start) for a step-by-step guide.

## Installation

The sync can run in three ways, described in [Installation](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Installation):

- **Docker:** `ghcr.io/thenetworkguy/netbox-zabbix-sync`
- **Python package:** `pip install netbox-zabbix-sync`, which provides the `netbox-zabbix-sync` command and a `Sync` class to use in your own code
- **From source:** clone this repository, install the dependencies with `uv sync` or `pip install -r requirements.txt`, and run `python3 netbox_zabbix_sync.py`

The connection to NetBox and Zabbix is configured with the `NETBOX_HOST`, `NETBOX_TOKEN`, `ZABBIX_HOST` and `ZABBIX_TOKEN` (or `ZABBIX_USER` and `ZABBIX_PASS`) environment variables.

## Configuration

The script works with its built-in defaults. To change them, copy `config.py.example` to `config.py`, or use `NBZX_` environment variables or command-line flags. Run `netbox_zabbix_sync.py --help` for all flags.

## Documentation

| Page | Contents |
| ---- | -------- |
| [Quick Start](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Quick-Start) | A first sync in a few minutes |
| [Installation](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Installation) | Docker, Python package and source installs, environment variables, command-line flags |
| [Preparation](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Netbox-preperations) | Custom fields, API permissions, custom links |
| [Configuration](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Configuration) | All settings, environment variables and logging |
| [Sync Behavior](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Sync-Behavior) | Hostgroups, templates, interfaces, proxies, inventory, tags, usermacros and more |
| [Experimental Features](https://github.com/TheNetworkGuy/netbox-zabbix-sync/wiki/Experimental-Features) | Jinja2 rendering of config context |

## Development

Install the development dependencies and run the tests and checks with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

The functional tests run the sync against real NetBox and Zabbix containers. See [tests/functional/README.md](https://github.com/TheNetworkGuy/netbox-zabbix-sync/blob/main/tests/functional/README.md).

## License

This project is licensed under the MIT license. See [LICENSE](https://github.com/TheNetworkGuy/netbox-zabbix-sync/blob/main/LICENSE).
