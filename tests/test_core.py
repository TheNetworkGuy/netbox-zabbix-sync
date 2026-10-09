"""Tests for the core sync module."""

import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch

from requests.exceptions import ConnectionError as RequestsConnectionError
from zabbix_utils import APIRequestError

from netbox_zabbix_sync.modules.core import Sync
from tests.fakes import FakeNetBox, connect, netbox_mock, zabbix_host, zabbix_mock

# A VM only gets templates from its config context, so one without this is
# skipped before it reaches Zabbix.
VM_CONTEXT = {"zabbix": {"templates": ["TestTemplate"]}}


class TestNetboxTokenHandling(unittest.TestCase):
    """Test that sync properly handles NetBox token authentication."""

    def test_v1_token_with_netbox_45(self):
        """Test that v1 token with NetBox 4.5+ logs warning but returns True."""
        syncer = Sync()

        with self.assertLogs("NetBox-Zabbix-sync", level="WARNING") as log_context:
            result = syncer._validate_netbox_token("token123", "4.5")

        self.assertTrue(result)
        self.assertTrue(
            any("v1 token format" in record.message for record in log_context.records)
        )

    def test_v2_token_with_netbox_35(self):
        """Test that v2 token with NetBox < 4.5 logs error and returns False."""
        syncer = Sync()

        with self.assertLogs("NetBox-Zabbix-sync", level="ERROR") as log_context:
            result = syncer._validate_netbox_token("nbt_key123.token123", "3.5")

        self.assertFalse(result)
        self.assertTrue(
            any(
                "v2 token format with Netbox version lower than 4.5" in record.message
                for record in log_context.records
            )
        )

    def test_v2_token_with_netbox_45(self):
        """Test that v2 token with NetBox 4.5+ logs debug and returns True."""
        syncer = Sync()

        with self.assertLogs("NetBox-Zabbix-sync", level="DEBUG") as log_context:
            result = syncer._validate_netbox_token("nbt_key123.token123", "4.5")

        self.assertTrue(result)
        self.assertTrue(
            any("v2 token format" in record.message for record in log_context.records)
        )

    def test_v1_token_with_netbox_35(self):
        """Test that v1 token with NetBox < 4.5 logs debug and returns True."""
        syncer = Sync()

        with self.assertLogs("NetBox-Zabbix-sync", level="DEBUG") as log_context:
            result = syncer._validate_netbox_token("token123", "3.5")

        self.assertTrue(result)
        self.assertTrue(
            any("v1 token format" in record.message for record in log_context.records)
        )


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

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_exits_on_zabbix_api_error(self, mock_api, mock_zabbix_api):
        """Test that sync exits when Zabbix API authentication fails."""
        # Simulate Netbox API
        netbox_mock(mock_api, self.netbox)
        # Simulate Zabbix API error
        mock_zabbix_api.return_value.check_auth.side_effect = APIRequestError(
            "Invalid credentials"
        )
        # Start syncer and set connection details
        syncer = Sync()
        result = syncer.connect(
            nb_host="http://netbox.local",
            nb_token="token",
            zbx_host="http://zabbix.local",
            zbx_user="user",
            zbx_pass="pass",
            zbx_token=None,
        )
        # Should return False due to Zabbix API error
        self.assertFalse(result)
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

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_user_password_when_no_token(self, mock_api, mock_zabbix_api):
        """Test that sync uses user/password auth when no token is provided."""
        netbox_mock(mock_api, self.netbox)

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
        netbox_mock(mock_api, self.netbox)
        zabbix_mock(mock_zabbix_api, version="7.0")

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

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.PhysicalDevice")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_processes_devices_from_netbox(
        self, mock_api, mock_zabbix_api, mock_physical_device
    ):
        """Test that sync creates PhysicalDevice instances for NetBox devices."""
        device1 = self.netbox.device(id=1, name="device1")
        device2 = self.netbox.device(id=2, name="device2")

        netbox_mock(mock_api, self.netbox, devices=[device1, device2])
        zabbix_mock(mock_zabbix_api, version="6.0")

        # Mock PhysicalDevice to have no template (skip further processing)
        mock_device_instance = MagicMock()
        mock_device_instance.zbx_template_names = []
        mock_physical_device.return_value = mock_device_instance

        syncer = Sync()
        connect(syncer)
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
        vm1 = self.netbox.virtual_machine(id=1, name="vm1")
        vm2 = self.netbox.virtual_machine(id=2, name="vm2")

        netbox_mock(mock_api, self.netbox, vms=[vm1, vm2])
        zabbix_mock(mock_zabbix_api, version="6.0")

        # Mock VM to have no template (skip further processing)
        mock_vm_instance = MagicMock()
        mock_vm_instance.zbx_template_names = []
        mock_virtual_machine.return_value = mock_vm_instance

        syncer = Sync({"sync_vms": True})
        connect(syncer)
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
        vm1 = self.netbox.virtual_machine(id=1, name="vm1")

        netbox_mock(mock_api, self.netbox, vms=[vm1])
        zabbix_mock(mock_zabbix_api, version="6.0")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        # Verify VirtualMachine was never called
        mock_virtual_machine.assert_not_called()


class TestSyncZabbixVersionHandling(unittest.TestCase):
    """Test Zabbix version-specific handling."""

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_host_proxy_name_for_zabbix_6(self, mock_api, mock_zabbix_api):
        """Test that sync uses 'host' as proxy name field for Zabbix 6."""
        netbox_mock(mock_api, self.netbox)

        mock_zabbix = zabbix_mock(mock_zabbix_api, version="6.0")
        mock_zabbix.proxy.get.return_value = [{"proxyid": "1", "host": "proxy1"}]

        syncer = Sync()
        connect(syncer)
        syncer.start()

        # Verify proxy.get was called with 'host' field
        mock_zabbix.proxy.get.assert_called_with(output=["proxyid", "host"])

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_uses_name_proxy_field_for_zabbix_7(self, mock_api, mock_zabbix_api):
        """Test that sync uses 'name' as proxy name field for Zabbix 7."""
        netbox_mock(mock_api, self.netbox)

        mock_zabbix = zabbix_mock(mock_zabbix_api, version="7.0")
        mock_zabbix.proxy.get.return_value = [{"proxyid": "1", "name": "proxy1"}]

        syncer = Sync()
        connect(syncer)
        syncer.start()

        # Verify proxy.get was called with 'name' field
        mock_zabbix.proxy.get.assert_called_with(output=["proxyid", "name"])

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_fetches_proxygroups_for_zabbix_7(self, mock_api, mock_zabbix_api):
        """Test that sync fetches proxy groups for Zabbix 7."""
        netbox_mock(mock_api, self.netbox)

        mock_zabbix = zabbix_mock(mock_zabbix_api, version="7.0")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        # Verify proxygroup.get was called for Zabbix 7
        mock_zabbix.proxygroup.get.assert_called_once()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_skips_proxygroups_for_zabbix_6(self, mock_api, mock_zabbix_api):
        """Test that sync does NOT fetch proxy groups for Zabbix 6."""
        netbox_mock(mock_api, self.netbox)

        mock_zabbix = zabbix_mock(mock_zabbix_api, version="6.0")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        # Verify proxygroup.get was NOT called for Zabbix 6
        mock_zabbix.proxygroup.get.assert_not_called()


class TestSyncProxyNameSanitization(unittest.TestCase):
    """Test proxy name field sanitization for Zabbix 6."""

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.proxy_prepper")
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_renames_host_to_name_for_zabbix_6_proxies(
        self, mock_api, mock_zabbix_api, mock_proxy_prepper
    ):
        """Test that for Zabbix 6, proxy 'host' field is renamed to 'name'."""
        netbox_mock(mock_api, self.netbox)

        mock_zabbix = zabbix_mock(mock_zabbix_api, version="6.0")
        # Zabbix 6 returns 'host' field
        mock_zabbix.proxy.get.return_value = [
            {"proxyid": "1", "host": "proxy1"},
            {"proxyid": "2", "host": "proxy2"},
        ]
        mock_proxy_prepper.return_value = []

        syncer = Sync()
        connect(syncer)
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

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_cluster_where_device_is_primary(self, mock_api, mock_zabbix_api):
        """Test that sync properly handles a device that is the primary in a virtual chassis."""
        # The chassis master is device 1, the device itself
        virtual_chassis = self.netbox.virtual_chassis("SW01", master=1)
        device = self.netbox.device("SW01N0", id=1, virtual_chassis=virtual_chassis)
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api)

        # Run the sync with clustering enabled
        syncer = Sync({"clustering": True})
        connect(syncer)
        syncer.start()

        # The host should be created with the virtual chassis name, not the device name
        mock_zabbix.host.create.assert_called_once()
        create_call_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_call_kwargs["host"], "SW01")

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_cluster_failover_keeps_existing_host(self, mock_api, mock_zabbix_api):
        """After a failover the new primary takes over the Zabbix host of the former primary."""
        virtual_chassis = self.netbox.virtual_chassis("SW01", master=2)
        new_primary = self.netbox.device(
            "SW01N1", id=2, virtual_chassis=virtual_chassis
        )
        former_primary = self.netbox.device(
            "SW01N0",
            id=1,
            custom_fields={"zabbix_hostid": 42},
            virtual_chassis=virtual_chassis,
        )

        mock_netbox = netbox_mock(mock_api, self.netbox)
        mock_netbox.dcim.devices.filter.side_effect = lambda **kwargs: (
            [new_primary, former_primary]
            if "virtual_chassis_id" in kwargs
            else [new_primary]
        )
        mock_zabbix = zabbix_mock(mock_zabbix_api)

        syncer = Sync({"clustering": True})
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        # The ID is moved in NetBox, not just on the local objects
        self.assertEqual(
            self.netbox.patches,
            [
                (new_primary.url, {"custom_fields": {"zabbix_hostid": 42}}),
                (former_primary.url, {"custom_fields": {"zabbix_hostid": None}}),
            ],
        )

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_sync_cluster_where_device_is_not_primary(self, mock_api, mock_zabbix_api):
        """Test that a non-primary cluster member is skipped and not created in Zabbix."""
        # The chassis master is device 2, so device 1 is secondary
        virtual_chassis = self.netbox.virtual_chassis("SW01", master=2)
        device = self.netbox.device("SW01N1", id=1, virtual_chassis=virtual_chassis)
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api)

        syncer = Sync({"clustering": True})
        connect(syncer)
        syncer.start()

        # Secondary cluster member must be skipped — no host should be created
        mock_zabbix.host.create.assert_not_called()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_removal_state_secondary_cluster_member_is_not_deleted(
        self, mock_api, mock_zabbix_api
    ):
        """A secondary member's Zabbix ID can belong to the cluster host, so it is kept."""
        virtual_chassis = self.netbox.virtual_chassis("SW01", master=2)
        device = self.netbox.device(
            "SW01N0",
            id=1,
            status="Decommissioning",
            custom_fields={"zabbix_hostid": 42},
            virtual_chassis=virtual_chassis,
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api)
        mock_zabbix.host.get.return_value = [{"hostid": "42"}]

        syncer = Sync({"clustering": True})
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_not_called()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_removal_state_primary_cluster_member_is_deleted(
        self, mock_api, mock_zabbix_api
    ):
        """The primary member in a removal state is still deleted."""
        virtual_chassis = self.netbox.virtual_chassis("SW01", master=1)
        device = self.netbox.device(
            "SW01N0",
            id=1,
            status="Decommissioning",
            custom_fields={"zabbix_hostid": 42},
            virtual_chassis=virtual_chassis,
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api)
        mock_zabbix.host.get.return_value = [{"hostid": "42"}]

        syncer = Sync({"clustering": True})
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_called_once_with(42)

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_templates_from_config_context(self, mock_api, mock_zabbix_api):
        """Test that templates_config_context=True uses the config context template."""
        device = self.netbox.device(
            id=1,
            name="Router01",
            config_context={
                "zabbix": {
                    "templates": ["ContextTemplate"],
                }
            },
        )

        netbox_mock(mock_api, self.netbox, devices=[device])

        mock_zabbix = zabbix_mock(mock_zabbix_api)
        # Both templates exist in Zabbix
        mock_zabbix.template.get.return_value = [
            {"templateid": "1", "name": "TestTemplate"},
            {"templateid": "2", "name": "ContextTemplate"},
        ]

        syncer = Sync({"templates_config_context": True})
        connect(syncer)
        syncer.start()

        # Verify host was created with the config context template, not the custom field one
        mock_zabbix.host.create.assert_called_once()
        create_call_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_call_kwargs["templates"], [{"templateid": "2"}])

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_templates_config_context_overrule(self, mock_api, mock_zabbix_api):
        """Test that templates_config_context_overrule=True prefers config context over custom field.

        The device has:
          - Custom field template (device type): "TestTemplate"
          - Config context template (device):    "ContextTemplate"

        With overrule enabled the config context should win and the host should
        be created with "ContextTemplate" only.
        """
        device = self.netbox.device(
            id=1,
            name="Router01",
            config_context={
                "zabbix": {
                    "templates": ["ContextTemplate"],
                }
            },
        )

        netbox_mock(mock_api, self.netbox, devices=[device])

        mock_zabbix = zabbix_mock(mock_zabbix_api)
        # Both templates exist in Zabbix
        mock_zabbix.template.get.return_value = [
            {"templateid": "1", "name": "TestTemplate"},
            {"templateid": "2", "name": "ContextTemplate"},
        ]

        syncer = Sync({"templates_config_context_overrule": True})
        connect(syncer)
        syncer.start()

        # Config context overrides the custom field - only "ContextTemplate" should be used
        mock_zabbix.host.create.assert_called_once()
        create_call_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_call_kwargs["templates"], [{"templateid": "2"}])
        # Verify the custom field template was NOT used
        self.assertNotIn({"templateid": "1"}, create_call_kwargs["templates"])


class TestDeviceStatusHandling(unittest.TestCase):
    """
    Tests device status handling during NetBox to Zabbix synchronization.

    Validates the correct sync behavior for various combinations of NetBox device
    status, Zabbix host state, and the 'zabbix_device_removal' / 'zabbix_device_disable'
    configuration settings.

    Scenarios:
      1. Active, not in Zabbix          → created enabled
      2. Active, already in Zabbix      → consistency check passes, no update
      3. Staged, not in Zabbix          → created disabled
      4. Staged, already in Zabbix      → consistency check passes, no update
      5. Decommissioning, not in Zabbix → skipped entirely
      6. Decommissioning, in Zabbix     → host deleted from Zabbix (cleanup)
      7. Active, in Zabbix but disabled → host re-enabled via consistency check
      8. Failed, in Zabbix but enabled  → host disabled via consistency check
    """

    def setUp(self):
        self.netbox = FakeNetBox()

    # Hostgroup produced by the default "site/manufacturer/role" format
    # for the FakeNetBox device defaults.
    EXPECTED_HOSTGROUP = "TestSite/TestManufacturer/Switch"

    # ------------------------------------------------------------------
    # Scenario 1: Active device, not yet in Zabbix → created enabled (status=0)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_device_not_in_zabbix_is_created(self, mock_api, mock_zabbix_api):
        """Active device not yet synced to Zabbix should be created with status enabled (0)."""
        device = self.netbox.device(name="test-device", status="Active")
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_called_once()
        create_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_kwargs["host"], "test-device")
        self.assertEqual(create_kwargs["status"], 0)

    # ------------------------------------------------------------------
    # Scenario 2: Active device, already in Zabbix → consistency check,
    #             Zabbix status matches → no updates
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_device_in_zabbix_is_consistent(self, mock_api, mock_zabbix_api):
        """Active device already in Zabbix with matching status should require no updates."""
        device = self.netbox.device(
            name="test-device", status="Active", custom_fields={"zabbix_hostid": 42}
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = zabbix_host(status="0")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.update.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 3: Staged device, not yet in Zabbix → created disabled (status=1)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_staged_device_not_in_zabbix_is_created_disabled(
        self, mock_api, mock_zabbix_api
    ):
        """Staged device not yet in Zabbix should be created with status disabled (1)."""
        device = self.netbox.device(name="test-device", status="Staged")
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_called_once()
        create_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_kwargs["status"], 1)

    # ------------------------------------------------------------------
    # Scenario 4: Staged device, already in Zabbix as disabled → no update needed
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_staged_device_in_zabbix_is_consistent(self, mock_api, mock_zabbix_api):
        """Staged device already in Zabbix as disabled should pass consistency check with no updates."""
        device = self.netbox.device(
            name="test-device", status="Staged", custom_fields={"zabbix_hostid": 42}
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = zabbix_host(status="1")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.update.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 5: Decommissioning device, not in Zabbix → skipped (no create, no delete)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_decommissioning_device_not_in_zabbix_is_skipped(
        self, mock_api, mock_zabbix_api
    ):
        """Decommissioning device with no Zabbix ID should be skipped entirely."""
        device = self.netbox.device(name="test-device", status="Decommissioning")
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.delete.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 6: Decommissioning device, already in Zabbix → cleanup (host deleted)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_decommissioning_device_in_zabbix_is_deleted(
        self, mock_api, mock_zabbix_api
    ):
        """Decommissioning device with a Zabbix ID should be deleted from Zabbix."""
        device = self.netbox.device(
            name="test-device",
            status="Decommissioning",
            custom_fields={"zabbix_hostid": 42},
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        # Zabbix still has the host → it should be deleted
        mock_zabbix.host.get.return_value = [{"hostid": "42"}]

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_called_once_with(42)

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_decommissioning_device_without_primary_ip_is_deleted(
        self, mock_api, mock_zabbix_api
    ):
        """Removal does not require a primary IP (#155)."""
        device = self.netbox.device(
            name="test-device",
            status="Decommissioning",
            custom_fields={"zabbix_hostid": 42},
            primary_ip=None,
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api)
        mock_zabbix.host.get.return_value = [{"hostid": "42"}]

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_called_once_with(42)

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_device_without_primary_ip_is_still_skipped(
        self, mock_api, mock_zabbix_api
    ):
        """Without a removal status a missing IP still skips the host."""
        device = self.netbox.device(
            name="test-device",
            status="Active",
            custom_fields={"zabbix_hostid": 42},
            primary_ip=None,
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api)
        mock_zabbix.host.get.return_value = zabbix_host()

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_not_called()
        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.update.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 7: Active device, Zabbix host is disabled → re-enable via consistency check
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_device_disabled_in_zabbix_is_enabled(
        self, mock_api, mock_zabbix_api
    ):
        """Active device whose Zabbix host is disabled should be re-enabled by consistency check."""
        device = self.netbox.device(
            name="test-device", status="Active", custom_fields={"zabbix_hostid": 42}
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        # Zabbix host currently disabled; device is Active → status out-of-sync
        mock_zabbix.host.get.return_value = zabbix_host(status="1")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.update.assert_called_once_with(hostid=42, status="0")

    # ------------------------------------------------------------------
    # Scenario 8: Failed device, Zabbix host is enabled → disable via consistency check
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_failed_device_enabled_in_zabbix_is_disabled(
        self, mock_api, mock_zabbix_api
    ):
        """Failed device whose Zabbix host is enabled should be disabled by consistency check."""
        device = self.netbox.device(
            name="test-device", status="Failed", custom_fields={"zabbix_hostid": 42}
        )
        netbox_mock(mock_api, self.netbox, devices=[device])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        # Zabbix host currently enabled; device is Failed → status out-of-sync
        mock_zabbix.host.get.return_value = zabbix_host(status="0")

        syncer = Sync()
        connect(syncer)
        syncer.start()

        mock_zabbix.host.update.assert_called_once_with(hostid=42, status="1")


class TestVMStatusHandling(unittest.TestCase):
    """
    Mirrors TestDeviceStatusHandling for VirtualMachine objects.

    Validates the VM sync loop in core.py using real VirtualMachine instances
    (not mocked) for the same 8 status scenarios.
    """

    def setUp(self):
        self.netbox = FakeNetBox()

    # Hostgroup produced by vm_hostgroup_format "site/role" with the FakeNetBox VM defaults.
    EXPECTED_HOSTGROUP = "TestSite/Server"

    # Simple Sync config that enables VM sync with a flat hostgroup format
    _SYNC_CFG: ClassVar[dict] = {"sync_vms": True, "vm_hostgroup_format": "site/role"}

    # ------------------------------------------------------------------
    # Scenario 1: Active VM, not yet in Zabbix → created enabled (status=0)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_vm_not_in_zabbix_is_created(self, mock_api, mock_zabbix_api):
        """Active VM not yet synced to Zabbix should be created with status enabled (0)."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT, name="test-vm", status="Active"
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_called_once()
        create_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_kwargs["host"], "test-vm")
        self.assertEqual(create_kwargs["status"], 0)

    # ------------------------------------------------------------------
    # Scenario 2: Active VM, already in Zabbix → consistency check, no update
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_vm_in_zabbix_is_consistent(self, mock_api, mock_zabbix_api):
        """Active VM already in Zabbix with matching status should require no updates."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Active",
            custom_fields={"zabbix_hostid": 42},
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = zabbix_host("test-vm", status="0")

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.update.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 3: Staged VM, not yet in Zabbix → created disabled (status=1)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_staged_vm_not_in_zabbix_is_created_disabled(
        self, mock_api, mock_zabbix_api
    ):
        """Staged VM not yet in Zabbix should be created with status disabled (1)."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT, name="test-vm", status="Staged"
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_called_once()
        create_kwargs = mock_zabbix.host.create.call_args.kwargs
        self.assertEqual(create_kwargs["status"], 1)

    # ------------------------------------------------------------------
    # Scenario 4: Staged VM, already in Zabbix as disabled → no update needed
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_staged_vm_in_zabbix_is_consistent(self, mock_api, mock_zabbix_api):
        """Staged VM already in Zabbix as disabled should pass consistency check with no updates."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Staged",
            custom_fields={"zabbix_hostid": 42},
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = zabbix_host("test-vm", status="1")

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.update.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 5: Decommissioning VM, not in Zabbix → skipped (no create, no delete)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_decommissioning_vm_not_in_zabbix_is_skipped(
        self, mock_api, mock_zabbix_api
    ):
        """Decommissioning VM with no Zabbix ID should be skipped entirely."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT, name="test-vm", status="Decommissioning"
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.delete.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 6: Decommissioning VM, already in Zabbix → cleanup (host deleted)
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_decommissioning_vm_in_zabbix_is_deleted(self, mock_api, mock_zabbix_api):
        """Decommissioning VM with a Zabbix ID should be deleted from Zabbix."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Decommissioning",
            custom_fields={"zabbix_hostid": 42},
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = [{"hostid": "42"}]

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_called_once_with(42)

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_decommissioning_vm_without_primary_ip_is_deleted(
        self, mock_api, mock_zabbix_api
    ):
        """Removal does not require a primary IP (#155)."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Decommissioning",
            custom_fields={"zabbix_hostid": 42},
            primary_ip=None,
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api)
        mock_zabbix.host.get.return_value = [{"hostid": "42"}]

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_called_once_with(42)

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_vm_without_primary_ip_is_still_skipped(
        self, mock_api, mock_zabbix_api
    ):
        """Without a removal status a missing IP still skips the host."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Active",
            custom_fields={"zabbix_hostid": 42},
            primary_ip=None,
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api)
        mock_zabbix.host.get.return_value = zabbix_host("test-vm")

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.delete.assert_not_called()
        mock_zabbix.host.create.assert_not_called()
        mock_zabbix.host.update.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 7: Active VM, Zabbix host is disabled → re-enable via consistency check
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_active_vm_disabled_in_zabbix_is_enabled(self, mock_api, mock_zabbix_api):
        """Active VM whose Zabbix host is disabled should be re-enabled by consistency check."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Active",
            custom_fields={"zabbix_hostid": 42},
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = zabbix_host("test-vm", status="1")

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.update.assert_called_once_with(hostid=42, status="0")

    # ------------------------------------------------------------------
    # Scenario 8: Failed VM, Zabbix host is enabled → disable via consistency check
    # ------------------------------------------------------------------
    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_failed_vm_enabled_in_zabbix_is_disabled(self, mock_api, mock_zabbix_api):
        """Failed VM whose Zabbix host is enabled should be disabled by consistency check."""
        vm = self.netbox.virtual_machine(
            config_context=VM_CONTEXT,
            name="test-vm",
            status="Failed",
            custom_fields={"zabbix_hostid": 42},
        )
        netbox_mock(mock_api, self.netbox, vms=[vm])
        mock_zabbix = zabbix_mock(mock_zabbix_api, hostgroup=self.EXPECTED_HOSTGROUP)
        mock_zabbix.host.get.return_value = zabbix_host("test-vm", status="0")

        syncer = Sync(self._SYNC_CFG)
        connect(syncer)
        syncer.start()

        mock_zabbix.host.update.assert_called_once_with(hostid=42, status="1")


class TestCombineFilters(unittest.TestCase):
    """Test the _combine_filters method and filter override behavior in start()."""

    def setUp(self):
        self.netbox = FakeNetBox()

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_filter_override_with_name_parameter(self, mock_api, mock_zabbix_api):
        """Test that method filter parameter overrides config filter for name.

        Scenario:
        - Config has nb_device_filter with name="SW01N0"
        - start() is called with device_filter {"name": "Testdev02"}
        - Only the device matching "Testdev02" should be processed
        """
        # Create two mock devices
        device_matching_method_filter = self.netbox.device(
            id=1, name="Testdev02", status="Active"
        )
        device_matching_config_filter = self.netbox.device(
            id=2, name="SW01N0", status="Active"
        )

        # Setup mocks - the filter should be called with the combined/overridden filter
        netbox_mock(
            mock_api,
            self.netbox,
            devices=[
                device_matching_method_filter,
                device_matching_config_filter,
            ],
        )
        zabbix_mock(mock_zabbix_api)

        # Create sync with config filter specifying one name
        syncer = Sync({"nb_device_filter": {"name": "SW01N0"}})
        connect(syncer)

        # Call start with method filter specifying a different name
        # The method filter should override the config filter
        syncer.start(device_filter={"name": "Testdev02"})

        # Verify that nbapi.dcim.devices.filter was called with the override filter
        mock_netbox = mock_api.return_value
        filter_call_kwargs = mock_netbox.dcim.devices.filter.call_args[1]
        self.assertEqual(filter_call_kwargs.get("name"), "Testdev02")

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_filter_override_site_parameter(self, mock_api, mock_zabbix_api):
        """Test that site filter override works correctly.

        Scenario:
        - Config has no device filter set
        - start() is called with device_filter {"site": "fra01"}
        - Only devices in fra01 site should be processed
        """
        site_fra01 = self.netbox.site("fra01")
        site_ams01 = self.netbox.site("ams01")

        # Create devices in different sites
        device_fra01 = self.netbox.device(
            id=1, name="device-fra01", status="Active", site=site_fra01
        )
        device_ams01 = self.netbox.device(
            id=2, name="device-ams01", status="Active", site=site_ams01
        )

        netbox_mock(mock_api, self.netbox, devices=[device_fra01, device_ams01])
        zabbix_mock(mock_zabbix_api)

        syncer = Sync()
        connect(syncer)

        # Call start with site filter for fra01
        syncer.start(device_filter={"site": "fra01"})

        # Verify that nbapi.dcim.devices.filter was called with the site filter
        mock_netbox = mock_api.return_value
        filter_call_kwargs = mock_netbox.dcim.devices.filter.call_args[1]
        self.assertEqual(filter_call_kwargs.get("site"), "fra01")

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_config_filter_overridden_by_start_parameter(
        self, mock_api, mock_zabbix_api
    ):
        """Test that start() method filter overrides config filter.

        Scenario:
        - Config specifies nb_device_filter with {"name": "SW01N0", "site": "ams01"}
        - start() is called with {"name": "Testdev02"} (only overriding name)
        - The final filter should be {"name": "Testdev02", "site": "ams01"}
        - Both name and site filters should be applied with the override
        """
        device_matching_all = self.netbox.device(
            id=1, name="Testdev02", status="Active"
        )

        netbox_mock(mock_api, self.netbox, devices=[device_matching_all])
        zabbix_mock(mock_zabbix_api)

        # Create sync with config filter having multiple parameters
        syncer = Sync({"nb_device_filter": {"name": "SW01N0", "site": "ams01"}})
        connect(syncer)

        # Call start with method filter that overrides only the name
        syncer.start(device_filter={"name": "Testdev02"})

        # Verify that nbapi.dcim.devices.filter was called with combined filter
        # (site from config + name from method parameter)
        mock_netbox = mock_api.return_value
        filter_call_kwargs = mock_netbox.dcim.devices.filter.call_args[1]
        self.assertEqual(filter_call_kwargs.get("name"), "Testdev02")
        self.assertEqual(filter_call_kwargs.get("site"), "ams01")

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_vm_filter_override_with_method_parameter(self, mock_api, mock_zabbix_api):
        """Test that VM filter override works correctly.

        Scenario:
        - Config enables VM sync with nb_vm_filter {"name": "vm-prod"}
        - start() is called with vm_filter {"name": "vm-test"}
        - Only VMs matching "vm-test" should be processed
        """
        # Create two mock VMs
        vm_matching_method_filter = self.netbox.virtual_machine(
            id=1, name="vm-test", status="Active"
        )
        vm_matching_config_filter = self.netbox.virtual_machine(
            id=2, name="vm-prod", status="Active"
        )

        netbox_mock(
            mock_api,
            self.netbox,
            vms=[vm_matching_method_filter, vm_matching_config_filter],
        )
        zabbix_mock(mock_zabbix_api)

        # Create sync with config filter for VMs
        syncer = Sync(
            {
                "sync_vms": True,
                "nb_vm_filter": {"name": "vm-prod"},
            }
        )
        connect(syncer)

        # Call start with method filter that overrides the VM name filter
        syncer.start(vm_filter={"name": "vm-test"})

        # Verify that nbapi.virtualization.virtual_machines.filter was called with override
        mock_netbox = mock_api.return_value
        filter_call_kwargs = (
            mock_netbox.virtualization.virtual_machines.filter.call_args[1]
        )
        self.assertEqual(filter_call_kwargs.get("name"), "vm-test")

    @patch("netbox_zabbix_sync.modules.core.ZabbixAPI")
    @patch("netbox_zabbix_sync.modules.core.nbapi")
    def test_multiple_filter_parameters_combined(self, mock_api, mock_zabbix_api):
        """Test that multiple filter parameters are correctly combined.

        Scenario:
        - Config has nb_device_filter with {"site": "fra01", "status": "active"}
        - start() is called with {"name": "router*"}
        - The final filter should have all three parameters
        """
        device = self.netbox.device(id=1, name="router01", status="Active")

        netbox_mock(mock_api, self.netbox, devices=[device])
        zabbix_mock(mock_zabbix_api)

        syncer = Sync({"nb_device_filter": {"site": "fra01", "status": "active"}})
        connect(syncer)

        syncer.start(device_filter={"name": "router*"})

        mock_netbox = mock_api.return_value
        filter_call_kwargs = mock_netbox.dcim.devices.filter.call_args[1]

        # All three parameters should be present
        self.assertEqual(filter_call_kwargs.get("site"), "fra01")
        self.assertEqual(filter_call_kwargs.get("status"), "active")
        self.assertEqual(filter_call_kwargs.get("name"), "router*")
