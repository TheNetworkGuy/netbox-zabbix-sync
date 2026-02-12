"""Tests for the core sync module."""

import unittest
from unittest.mock import MagicMock, patch

from pynetbox.core.query import RequestError as NBRequestError
from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError, ProcessingError

from netbox_zabbix_sync.modules.core import sync

# Minimal config for testing - includes all keys used by sync()
TEST_CONFIG = {
    "hostgroup_format": "site",
    "vm_hostgroup_format": "site",
    "sync_vms": False,
    "nb_device_filter": {},
    "nb_vm_filter": {},
    "create_journal": False,
    "templates_config_context": False,
    "templates_config_context_overrule": False,
    "create_hostgroups": False,
    "clustering": False,
    "zabbix_device_removal": ["Decommissioning", "Inventory"],
    "zabbix_device_disable": ["Offline", "Planned", "Staged", "Failed"],
    "full_proxy_sync": False,
    "extended_site_properties": False,
}


class MockNetboxDevice:
    """Mock NetBox device object."""

    def __init__(
        self,
        device_id=1,
        name="test-device",
        status_label="Active",
        zabbix_hostid=None,
        config_context=None,
        site=None,
        primary_ip=None,
    ):
        self.id = device_id
        self.name = name
        self.status = MagicMock()
        self.status.label = status_label
        self.custom_fields = {"zabbix_hostid": zabbix_hostid}
        self.config_context = config_context or {}
        self.site = site
        self.primary_ip = primary_ip


class MockNetboxVM:
    """Mock NetBox virtual machine object."""

    def __init__(
        self,
        vm_id=1,
        name="test-vm",
        status_label="Active",
        zabbix_hostid=None,
        config_context=None,
        site=None,
        primary_ip=None,
    ):
        self.id = vm_id
        self.name = name
        self.status = MagicMock()
        self.status.label = status_label
        self.custom_fields = {"zabbix_hostid": zabbix_hostid}
        self.config_context = config_context or {}
        self.site = site
        self.primary_ip = primary_ip


class TestSyncNetboxConnection(unittest.TestCase):
    """Test NetBox connection handling in sync function."""

    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_exits_on_netbox_connection_error(self, mock_api):
        """Test that sync exits when NetBox connection fails."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        # Simulate connection error when accessing version
        type(mock_netbox).version = property(
            lambda self: (_ for _ in ()).throw(RequestsConnectionError())
        )

        with self.assertRaises(SystemExit) as context:
            sync(
                "http://netbox.local",
                "token",
                "http://zabbix.local",
                "user",
                "pass",
                None,
            )

        self.assertEqual(context.exception.code, 1)

    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_exits_on_netbox_request_error(self, mock_api):
        """Test that sync exits when NetBox returns a request error."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        # Simulate NetBox request error
        type(mock_netbox).version = property(
            lambda self: (_ for _ in ()).throw(NBRequestError(MagicMock()))
        )

        with self.assertRaises(SystemExit) as context:
            sync(
                "http://netbox.local",
                "token",
                "http://zabbix.local",
                "user",
                "pass",
                None,
            )

        self.assertEqual(context.exception.code, 1)


class TestSyncZabbixConnection(unittest.TestCase):
    """Test Zabbix connection handling in sync function."""

    def _setup_netbox_mock(self, mock_api):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        mock_netbox.extras.custom_fields.filter.return_value = []
        mock_netbox.dcim.devices.filter.return_value = []
        mock_netbox.virtualization.virtual_machines.filter.return_value = []
        mock_netbox.dcim.site_groups.all.return_value = []
        mock_netbox.dcim.regions.all.return_value = []
        return mock_netbox

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_exits_on_zabbix_api_error(self, mock_api, mock_zabbix_api):
        """Test that sync exits when Zabbix API authentication fails."""
        self._setup_netbox_mock(mock_api)

        # Simulate Zabbix API error
        mock_zabbix_api.return_value.check_auth.side_effect = APIRequestError(
            "Invalid credentials"
        )

        with self.assertRaises(SystemExit) as context:
            sync(
                "http://netbox.local",
                "token",
                "http://zabbix.local",
                "user",
                "pass",
                None,
            )

        self.assertEqual(context.exception.code, 1)

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_exits_on_zabbix_processing_error(self, mock_api, mock_zabbix_api):
        """Test that sync exits when Zabbix has processing error."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix_api.return_value.check_auth.side_effect = ProcessingError(
            "Processing failed"
        )

        with self.assertRaises(SystemExit) as context:
            sync(
                "http://netbox.local",
                "token",
                "http://zabbix.local",
                "user",
                "pass",
                None,
            )

        self.assertEqual(context.exception.code, 1)


class TestSyncZabbixAuthentication(unittest.TestCase):
    """Test Zabbix authentication methods."""

    def _setup_netbox_mock(self, mock_api):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        mock_netbox.extras.custom_fields.filter.return_value = []
        mock_netbox.dcim.devices.filter.return_value = []
        mock_netbox.virtualization.virtual_machines.filter.return_value = []
        mock_netbox.dcim.site_groups.all.return_value = []
        mock_netbox.dcim.regions.all.return_value = []
        return mock_netbox

    def _setup_zabbix_mock(self, mock_zabbix_api, version="6.0"):
        """Helper to setup a working Zabbix mock."""
        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = version
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []
        mock_zabbix.proxygroup.get.return_value = []
        return mock_zabbix

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_uses_user_password_when_no_token(self, mock_api, mock_zabbix_api):
        """Test that sync uses user/password auth when no token is provided."""
        self._setup_netbox_mock(mock_api)
        self._setup_zabbix_mock(mock_zabbix_api)

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "zbx_user",
            "zbx_pass",
            None,  # No token
        )

        # Verify ZabbixAPI was called with user/password
        mock_zabbix_api.assert_called_once()
        call_kwargs = mock_zabbix_api.call_args.kwargs
        self.assertEqual(call_kwargs["user"], "zbx_user")
        self.assertEqual(call_kwargs["password"], "zbx_pass")
        self.assertNotIn("token", call_kwargs)

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_uses_token_when_provided(self, mock_api, mock_zabbix_api):
        """Test that sync uses token auth when token is provided."""
        self._setup_netbox_mock(mock_api)
        self._setup_zabbix_mock(mock_zabbix_api)

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "zbx_user",
            "zbx_pass",
            "zbx_token",  # Token provided
        )

        # Verify ZabbixAPI was called with token
        mock_zabbix_api.assert_called_once()
        call_kwargs = mock_zabbix_api.call_args.kwargs
        self.assertEqual(call_kwargs["token"], "zbx_token")
        self.assertNotIn("user", call_kwargs)
        self.assertNotIn("password", call_kwargs)


class TestSyncDeviceProcessing(unittest.TestCase):
    """Test device processing in sync function."""

    def _setup_netbox_mock(self, mock_api, devices=None, vms=None):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        mock_netbox.extras.custom_fields.filter.return_value = []
        mock_netbox.dcim.devices.filter.return_value = devices or []
        mock_netbox.virtualization.virtual_machines.filter.return_value = vms or []
        mock_netbox.dcim.site_groups.all.return_value = []
        mock_netbox.dcim.regions.all.return_value = []
        mock_netbox.extras.journal_entries = MagicMock()
        return mock_netbox

    def _setup_zabbix_mock(self, mock_zabbix_api, version="6.0"):
        """Helper to setup a working Zabbix mock."""
        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = version
        mock_zabbix.hostgroup.get.return_value = [{"groupid": "1", "name": "TestGroup"}]
        mock_zabbix.template.get.return_value = [
            {"templateid": "1", "name": "TestTemplate"}
        ]
        mock_zabbix.proxy.get.return_value = []
        mock_zabbix.proxygroup.get.return_value = []
        return mock_zabbix

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.PhysicalDevice")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_processes_devices_from_netbox(
        self, mock_api, mock_zabbix_api, mock_physical_device
    ):
        """Test that sync creates PhysicalDevice instances for NetBox devices."""
        device1 = MockNetboxDevice(device_id=1, name="device1")
        device2 = MockNetboxDevice(device_id=2, name="device2")

        self._setup_netbox_mock(mock_api, devices=[device1, device2])
        self._setup_zabbix_mock(mock_zabbix_api)

        # Mock PhysicalDevice to have no template (skip further processing)
        mock_device_instance = MagicMock()
        mock_device_instance.zbx_template_names = []
        mock_physical_device.return_value = mock_device_instance

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify PhysicalDevice was instantiated for each device
        self.assertEqual(mock_physical_device.call_count, 2)

    @patch("netbox_zabbix_sync.modules.core.config", {**TEST_CONFIG, "sync_vms": True})
    @patch("netbox_zabbix_sync.modules.core.VirtualMachine")
    @patch("netbox_zabbix_sync.modules.core.PhysicalDevice")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_processes_vms_when_enabled(
        self, mock_api, mock_zabbix_api, mock_physical_device, mock_virtual_machine
    ):
        """Test that sync processes VMs when sync_vms is enabled."""
        vm1 = MockNetboxVM(vm_id=1, name="vm1")
        vm2 = MockNetboxVM(vm_id=2, name="vm2")

        self._setup_netbox_mock(mock_api, vms=[vm1, vm2])
        self._setup_zabbix_mock(mock_zabbix_api)

        # Mock VM to have no template (skip further processing)
        mock_vm_instance = MagicMock()
        mock_vm_instance.zbx_template_names = []
        mock_virtual_machine.return_value = mock_vm_instance

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify VirtualMachine was instantiated for each VM
        self.assertEqual(mock_virtual_machine.call_count, 2)

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.VirtualMachine")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_skips_vms_when_disabled(
        self, mock_api, mock_zabbix_api, mock_virtual_machine
    ):
        """Test that sync does NOT process VMs when sync_vms is disabled."""
        vm1 = MockNetboxVM(vm_id=1, name="vm1")

        self._setup_netbox_mock(mock_api, vms=[vm1])
        self._setup_zabbix_mock(mock_zabbix_api)

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify VirtualMachine was never called
        mock_virtual_machine.assert_not_called()


class TestSyncZabbixVersionHandling(unittest.TestCase):
    """Test Zabbix version-specific handling."""

    def _setup_netbox_mock(self, mock_api):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        mock_netbox.extras.custom_fields.filter.return_value = []
        mock_netbox.dcim.devices.filter.return_value = []
        mock_netbox.virtualization.virtual_machines.filter.return_value = []
        mock_netbox.dcim.site_groups.all.return_value = []
        mock_netbox.dcim.regions.all.return_value = []
        return mock_netbox

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_uses_host_proxy_name_for_zabbix_6(self, mock_api, mock_zabbix_api):
        """Test that sync uses 'host' as proxy name field for Zabbix 6."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = [{"proxyid": "1", "host": "proxy1"}]

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify proxy.get was called with 'host' field
        mock_zabbix.proxy.get.assert_called_with(output=["proxyid", "host"])

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_uses_name_proxy_field_for_zabbix_7(self, mock_api, mock_zabbix_api):
        """Test that sync uses 'name' as proxy name field for Zabbix 7."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "7.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = [{"proxyid": "1", "name": "proxy1"}]
        mock_zabbix.proxygroup.get.return_value = []

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify proxy.get was called with 'name' field
        mock_zabbix.proxy.get.assert_called_with(output=["proxyid", "name"])

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_fetches_proxygroups_for_zabbix_7(self, mock_api, mock_zabbix_api):
        """Test that sync fetches proxy groups for Zabbix 7."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "7.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []
        mock_zabbix.proxygroup.get.return_value = []

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify proxygroup.get was called for Zabbix 7
        mock_zabbix.proxygroup.get.assert_called_once()

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_skips_proxygroups_for_zabbix_6(self, mock_api, mock_zabbix_api):
        """Test that sync does NOT fetch proxy groups for Zabbix 6."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify proxygroup.get was NOT called for Zabbix 6
        mock_zabbix.proxygroup.get.assert_not_called()


class TestSyncLogout(unittest.TestCase):
    """Test that sync properly logs out from Zabbix."""

    def _setup_netbox_mock(self, mock_api):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        mock_netbox.extras.custom_fields.filter.return_value = []
        mock_netbox.dcim.devices.filter.return_value = []
        mock_netbox.virtualization.virtual_machines.filter.return_value = []
        mock_netbox.dcim.site_groups.all.return_value = []
        mock_netbox.dcim.regions.all.return_value = []
        return mock_netbox

    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_logs_out_from_zabbix(self, mock_api, mock_zabbix_api):
        """Test that sync calls logout on Zabbix API after completion."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify logout was called
        mock_zabbix.logout.assert_called_once()


class TestSyncProxyNameSanitization(unittest.TestCase):
    """Test proxy name field sanitization for Zabbix 6."""

    def _setup_netbox_mock(self, mock_api):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        mock_netbox.extras.custom_fields.filter.return_value = []
        mock_netbox.dcim.devices.filter.return_value = []
        mock_netbox.virtualization.virtual_machines.filter.return_value = []
        mock_netbox.dcim.site_groups.all.return_value = []
        mock_netbox.dcim.regions.all.return_value = []
        return mock_netbox

    @patch("netbox_zabbix_sync.modules.core.proxy_prepper")
    @patch("netbox_zabbix_sync.modules.core.config", TEST_CONFIG)
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.api")
    def test_sync_renames_host_to_name_for_zabbix_6_proxies(
        self, mock_api, mock_zabbix_api, mock_proxy_prepper
    ):
        """Test that for Zabbix 6, proxy 'host' field is renamed to 'name'."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        # Zabbix 6 returns 'host' field
        mock_zabbix.proxy.get.return_value = [
            {"proxyid": "1", "host": "proxy1"},
            {"proxyid": "2", "host": "proxy2"},
        ]
        mock_proxy_prepper.return_value = []

        sync(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )

        # Verify proxy_prepper was called with sanitized proxy list
        call_args = mock_proxy_prepper.call_args[0]
        proxies = call_args[0]
        # Check that 'host' was renamed to 'name'
        for proxy in proxies:
            self.assertIn("name", proxy)
            self.assertNotIn("host", proxy)


if __name__ == "__main__":
    unittest.main()
