"""Tests for the core sync module."""

import unittest
from unittest.mock import MagicMock, patch

from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError

from netbox_zabbix_sync.modules.core import Sync


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
        virtual_chassis=None,
        device_type=None,
        tenant=None,
        device_role=None,
        role=None,
        platform=None,
        serial="",
        tags=None,
    ):
        self.id = device_id
        self.name = name
        self.status = MagicMock()
        self.status.label = status_label
        self.status.value = status_label.lower()
        self.custom_fields = {
            "zabbix_hostid": zabbix_hostid,
            "zabbix_template": "TestTemplate",
        }
        self.config_context = config_context or {}
        self.tenant = tenant
        self.platform = platform
        self.serial = serial
        self.asset_tag = None
        self.location = None
        self.rack = None
        self.position = None
        self.face = None
        self.latitude = None
        self.longitude = None
        self.parent_device = None
        self.airflow = None
        self.cluster = None
        self.vc_position = None
        self.vc_priority = None
        self.description = ""
        self.comments = ""
        self.tags = tags or []
        self.oob_ip = None

        # Setup site with proper structure
        if site is None:
            self.site = MagicMock()
            self.site.name = "TestSite"
            self.site.slug = "testsite"
        else:
            self.site = site

        # Setup primary IP with proper structure
        if primary_ip is None:
            self.primary_ip = MagicMock()
            self.primary_ip.address = "192.168.1.1/24"
        else:
            self.primary_ip = primary_ip

        self.primary_ip4 = self.primary_ip
        self.primary_ip6 = None

        # Setup device type with proper structure
        if device_type is None:
            self.device_type = MagicMock()
            self.device_type.custom_fields = {
                "zabbix_template": "TestTemplate"}
            self.device_type.manufacturer = MagicMock()
            self.device_type.manufacturer.name = "TestManufacturer"
            self.device_type.display = "Test Device Type"
            self.device_type.model = "Test Model"
            self.device_type.slug = "test-model"
        else:
            self.device_type = device_type

        # Setup device role (NetBox 2/3 compatibility) and role (NetBox 4+)
        if device_role is None and role is None:
            # Create default role
            mock_role = MagicMock()
            mock_role.name = "Switch"
            mock_role.slug = "switch"
            self.device_role = mock_role  # NetBox 2/3
            self.role = mock_role  # NetBox 4+
        else:
            self.device_role = device_role or role
            self.role = role or device_role

        self.virtual_chassis = virtual_chassis

    def save(self):
        """Mock save method for NetBox device."""
        pass


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

    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_error_on_netbox_connection_error(self, mock_api):
        """Test that sync returns False when NetBox connection fails."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        # Simulate connection error when accessing version
        type(mock_netbox).version = property(
            lambda self: (_ for _ in ()).throw(RequestsConnectionError())
        )

        syncer = Sync()
        result = syncer.connect(
            nb_host="http://netbox.local",
            nb_token="token",
            zbx_host="http://zabbix.local",
            zbx_user="user",
            zbx_pass="pass",
            zbx_token=None,
        )

        self.assertFalse(result)


class TestZabbixUserTokenConflict(unittest.TestCase):
    """Test that sync returns False when both ZABBIX_USER/PASS and ZABBIX_TOKEN are set."""

    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_error_on_user_token_conflict(self, mock_api):
        """Test that sync returns False when both user/pass and token are provided."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"

        syncer = Sync()
        result = syncer.connect(
            nb_host="http://netbox.local",
            nb_token="token",
            zbx_host="http://zabbix.local",
            zbx_user="user",
            zbx_pass="pass",
            zbx_token="token",  # Both token and user/pass provided
        )

        self.assertFalse(result)


class TestSyncZabbixConnection(unittest.TestCase):
    """Test Zabbix connection handling in sync function."""

    def _setup_netbox_mock(self, mock_api):
        """Helper to setup a working NetBox mock."""
        mock_netbox = MagicMock()
        mock_api.return_value = mock_netbox
        mock_netbox.version = "3.5"
        return mock_netbox

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_exits_on_zabbix_api_error(self, mock_api, mock_zabbix_api):
        """Test that sync exits when Zabbix API authentication fails."""
        # Simulate Netbox API
        self._setup_netbox_mock(mock_api)
        # Simulate Zabbix API error
        mock_zabbix_api.return_value.check_auth.side_effect = APIRequestError(
            "Invalid credentials"
        )
        # Start syncer and set connection details
        syncer = Sync()
        result = syncer.connect(
            "http://netbox.local",
            "token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        # Validate that result is False due to Zabbix API error
        self.assertFalse(result)


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

    def _setup_zabbix_mock(self, mock_zabbix_api, version="7.0"):
        """Helper to setup a working Zabbix mock."""
        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = version
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []
        mock_zabbix.proxygroup.get.return_value = []
        return mock_zabbix

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_user_password_when_no_token(self, mock_api, mock_zabbix_api):
        """Test that sync uses user/password auth when no token is provided."""
        self._setup_netbox_mock(mock_api)

        syncer = Sync()
        syncer.connect(
            nb_host="http://netbox.local",
            nb_token="nb_token",
            zbx_host="http://zabbix.local",
            zbx_user="zbx_user",
            zbx_pass="zbx_pass",
        )

        # Verify ZabbixAPI was called with user/password and without token
        mock_zabbix_api.assert_called_once()
        call_kwargs = mock_zabbix_api.call_args.kwargs
        self.assertEqual(call_kwargs["user"], "zbx_user")
        self.assertEqual(call_kwargs["password"], "zbx_pass")
        self.assertNotIn("token", call_kwargs)

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_token_when_provided(self, mock_api, mock_zabbix_api):
        """Test that sync uses token auth when token is provided."""
        self._setup_netbox_mock(mock_api)
        self._setup_zabbix_mock(mock_zabbix_api)

        syncer = Sync()
        syncer.connect(
            nb_host="http://netbox.local",
            nb_token="nb_token",
            zbx_host="http://zabbix.local",
            zbx_token="zbx_token",
        )

        # Verify ZabbixAPI was called with token and without user/password
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
        mock_zabbix.hostgroup.get.return_value = [
            {"groupid": "1", "name": "TestGroup"}]
        mock_zabbix.template.get.return_value = [
            {"templateid": "1", "name": "TestTemplate"}
        ]
        mock_zabbix.proxy.get.return_value = []
        mock_zabbix.proxygroup.get.return_value = []
        return mock_zabbix

    @patch("netbox_zabbix_sync.modules.core.PhysicalDevice")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
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

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify PhysicalDevice was instantiated for each device
        self.assertEqual(mock_physical_device.call_count, 2)

    @patch("netbox_zabbix_sync.modules.core.VirtualMachine")
    @patch("netbox_zabbix_sync.modules.core.PhysicalDevice")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
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

        syncer = Sync({"sync_vms": True})
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify VirtualMachine was instantiated for each VM
        self.assertEqual(mock_virtual_machine.call_count, 2)

    @patch("netbox_zabbix_sync.modules.core.VirtualMachine")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_skips_vms_when_disabled(
        self, mock_api, mock_zabbix_api, mock_virtual_machine
    ):
        """Test that sync does NOT process VMs when sync_vms is disabled."""
        vm1 = MockNetboxVM(vm_id=1, name="vm1")

        self._setup_netbox_mock(mock_api, vms=[vm1])
        self._setup_zabbix_mock(mock_zabbix_api)

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

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

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_host_proxy_name_for_zabbix_6(self, mock_api, mock_zabbix_api):
        """Test that sync uses 'host' as proxy name field for Zabbix 6."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = [
            {"proxyid": "1", "host": "proxy1"}]

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify proxy.get was called with 'host' field
        mock_zabbix.proxy.get.assert_called_with(output=["proxyid", "host"])

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_name_proxy_field_for_zabbix_7(self, mock_api, mock_zabbix_api):
        """Test that sync uses 'name' as proxy name field for Zabbix 7."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "7.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = [
            {"proxyid": "1", "name": "proxy1"}]
        mock_zabbix.proxygroup.get.return_value = []

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify proxy.get was called with 'name' field
        mock_zabbix.proxy.get.assert_called_with(output=["proxyid", "name"])

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
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

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify proxygroup.get was called for Zabbix 7
        mock_zabbix.proxygroup.get.assert_called_once()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_skips_proxygroups_for_zabbix_6(self, mock_api, mock_zabbix_api):
        """Test that sync does NOT fetch proxy groups for Zabbix 6."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

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

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_logs_out_from_zabbix(self, mock_api, mock_zabbix_api):
        """Test that sync calls logout on Zabbix API after completion."""
        self._setup_netbox_mock(mock_api)

        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = "6.0"
        mock_zabbix.hostgroup.get.return_value = []
        mock_zabbix.template.get.return_value = []
        mock_zabbix.proxy.get.return_value = []

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

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
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
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

        syncer = Sync()
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify proxy_prepper was called with sanitized proxy list
        call_args = mock_proxy_prepper.call_args[0]
        proxies = call_args[0]
        # Check that 'host' was renamed to 'name'
        for proxy in proxies:
            self.assertIn("name", proxy)
            self.assertNotIn("host", proxy)


class TestDeviceHandeling(unittest.TestCase):
    """
    Tests several devices which can be synced to Zabbix.
    This class contains a lot of data in order to validate proper handling of different device types and configurations.
    """

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

    def _setup_zabbix_mock(self, mock_zabbix_api, version=7.0):
        """Helper to setup a working Zabbix mock."""
        mock_zabbix = MagicMock()
        mock_zabbix_api.return_value = mock_zabbix
        mock_zabbix.version = version
        mock_zabbix.hostgroup.get.return_value = [
            {"groupid": "1", "name": "TestGroup"}]
        mock_zabbix.template.get.return_value = [
            {"templateid": "1", "name": "TestTemplate"}
        ]
        mock_zabbix.proxy.get.return_value = []
        mock_zabbix.proxygroup.get.return_value = []
        mock_zabbix.logout = MagicMock()
        # Mock host.get to return empty (host doesn't exist yet)
        mock_zabbix.host.get.return_value = []
        # Mock host.create to return success
        mock_zabbix.host.create.return_value = {"hostids": ["1"]}
        return mock_zabbix

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_cluster_where_device_is_primary(
        self, mock_api, mock_zabbix_api
    ):
        """Test that sync properly handles a device that is the primary in a virtual chassis."""
        # Create a device that is part of a virtual chassis and is the primary
        device = MockNetboxDevice(
            device_id=1,
            name="SW01N0",
            virtual_chassis=MagicMock(),
        )
        device.virtual_chassis.master = MagicMock()
        device.virtual_chassis.master.id = 1  # Same as device ID - device is primary
        device.virtual_chassis.name = "SW01"

        # Setup NetBox mock with a site for hostgroup
        mock_netbox = self._setup_netbox_mock(mock_api)
        mock_netbox.dcim.devices.filter.return_value = [device]

        # Create a mock site for hostgroup generation
        mock_site = MagicMock()
        mock_site.name = "TestSite"
        device.site = mock_site

        # Setup Zabbix mock
        mock_zabbix = self._setup_zabbix_mock(mock_zabbix_api)

        # Run the sync with clustering enabled
        syncer = Sync({"clustering": True})
        syncer.connect(
            "http://netbox.local",
            "nb_token",
            "http://zabbix.local",
            "user",
            "pass",
            None,
        )
        syncer.start()

        # Verify that host.create was called with the cluster name "SW01", not "SW01N0"
        mock_zabbix.host.create.assert_called_once()
        create_call_kwargs = mock_zabbix.host.create.call_args.kwargs

        # The host should be created with the virtual chassis name, not the device name
        self.assertEqual(create_call_kwargs["host"], "SW01")
