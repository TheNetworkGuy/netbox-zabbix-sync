"""Tests for the command-line interface."""

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from netbox_zabbix_sync.modules.cli import get_version


def test_get_version_from_package_metadata():
    """The version comes from the installed package."""
    with patch("netbox_zabbix_sync.modules.cli.version", return_value="4.1.0"):
        assert get_version() == "4.1.0"


def test_get_version_not_installed():
    """Running from source without an installed package gives 'unknown'."""
    with patch(
        "netbox_zabbix_sync.modules.cli.version",
        side_effect=PackageNotFoundError("netbox-zabbix-sync"),
    ):
        assert get_version() == "unknown"
