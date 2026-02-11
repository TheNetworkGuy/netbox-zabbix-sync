"""Tests for device deletion functionality in the PhysicalDevice class."""
import unittest
from unittest.mock import MagicMock, patch

from zabbix_utils import APIRequestError

from modules.device import PhysicalDevice
from modules.exceptions import SyncExternalError


class TestDeviceDeletion(unittest.TestCase):
    """Test class for device deletion functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock NetBox device
        self.mock_nb_device = MagicMock()
        self.mock_nb_device.id = 123
        self.mock_nb_device.name = "test-device"
        self.mock_nb_device.status.label = "Decommissioning"
        self.mock_nb_device.custom_fields = {"zabbix_hostid": "456"}
        self.mock_nb_device.config_context = {}

        # Set up a primary IP
        primary_ip = MagicMock()
        primary_ip.address = "192.168.1.1/24"
        self.mock_nb_device.primary_ip = primary_ip

        # Create mock Zabbix API
        self.mock_zabbix = MagicMock()
        self.mock_zabbix.version = "6.0"

        # Set up mock host.get response
        self.mock_zabbix.host.get.return_value = [{"hostid": "456"}]

        # Mock NetBox journal class
        self.mock_nb_journal = MagicMock()

        # Create logger mock
        self.mock_logger = MagicMock()

        # Create PhysicalDevice instance with mocks
        with patch('modules.device.config', {"device_cf": "zabbix_hostid"}):
            self.device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                journal=True,
                logger=self.mock_logger
            )

    def test_cleanup_successful_deletion(self):
        """Test successful device deletion from Zabbix."""
        # Setup
        self.mock_zabbix.host.get.return_value = [{"hostid": "456"}]
        self.mock_zabbix.host.delete.return_value = {"hostids": ["456"]}

        # Execute
        self.device.cleanup()

        # Verify
        self.mock_zabbix.host.get.assert_called_once_with(filter={'hostid': '456'}, output=[])
        self.mock_zabbix.host.delete.assert_called_once_with('456')
        self.mock_nb_device.save.assert_called_once()
        self.assertIsNone(self.mock_nb_device.custom_fields["zabbix_hostid"])
        self.mock_logger.info.assert_called_with(f"Host {self.device.name}: "
                                                  "Deleted host from Zabbix.")

    def test_cleanup_device_already_deleted(self):
        """Test cleanup when device is already deleted from Zabbix."""
        # Setup
        self.mock_zabbix.host.get.return_value = []  # Empty list means host not found

        # Execute
        self.device.cleanup()

        # Verify
        self.mock_zabbix.host.get.assert_called_once_with(filter={'hostid': '456'}, output=[])
        self.mock_zabbix.host.delete.assert_not_called()
        self.mock_nb_device.save.assert_called_once()
        self.assertIsNone(self.mock_nb_device.custom_fields["zabbix_hostid"])
        self.mock_logger.info.assert_called_with(
            f"Host {self.device.name}: was already deleted from Zabbix. Removed link in NetBox.")

    def test_cleanup_api_error(self):
        """Test cleanup when Zabbix API returns an error."""
        # Setup
        self.mock_zabbix.host.get.return_value = [{"hostid": "456"}]
        self.mock_zabbix.host.delete.side_effect = APIRequestError("API Error")

        # Execute and verify
        with self.assertRaises(SyncExternalError):
            self.device.cleanup()

        # Verify correct calls were made
        self.mock_zabbix.host.get.assert_called_once_with(filter={'hostid': '456'}, output=[])
        self.mock_zabbix.host.delete.assert_called_once_with('456')
        self.mock_nb_device.save.assert_not_called()
        self.mock_logger.error.assert_called()

    def test_zeroize_cf(self):
        """Test _zeroize_cf method that clears the custom field."""
        # Execute
        self.device._zeroize_cf() #  pylint: disable=protected-access

        # Verify
        self.assertIsNone(self.mock_nb_device.custom_fields["zabbix_hostid"])
        self.mock_nb_device.save.assert_called_once()

    def test_create_journal_entry(self):
        """Test create_journal_entry method."""
        # Setup
        test_message = "Test journal entry"

        # Execute
        result = self.device.create_journal_entry("info", test_message)

        # Verify
        self.assertTrue(result)
        self.mock_nb_journal.create.assert_called_once()
        journal_entry = self.mock_nb_journal.create.call_args[0][0]
        self.assertEqual(journal_entry["assigned_object_type"], "dcim.device")
        self.assertEqual(journal_entry["assigned_object_id"], 123)
        self.assertEqual(journal_entry["kind"], "info")
        self.assertEqual(journal_entry["comments"], test_message)

    def test_create_journal_entry_invalid_severity(self):
        """Test create_journal_entry with invalid severity."""
        # Execute
        result = self.device.create_journal_entry("invalid", "Test message")

        # Verify
        self.assertFalse(result)
        self.mock_nb_journal.create.assert_not_called()
        self.mock_logger.warning.assert_called()

    def test_create_journal_entry_when_disabled(self):
        """Test create_journal_entry when journaling is disabled."""
        # Setup - create device with journal=False
        with patch('modules.device.config', {"device_cf": "zabbix_hostid"}):
            device = PhysicalDevice(
                self.mock_nb_device,
                self.mock_zabbix,
                self.mock_nb_journal,
                "3.0",
                journal=False,  # Disable journaling
                logger=self.mock_logger
            )

        # Execute
        result = device.create_journal_entry("info", "Test message")

        # Verify
        self.assertFalse(result)
        self.mock_nb_journal.create.assert_not_called()

    def test_cleanup_updates_journal(self):
        """Test that cleanup method creates a journal entry."""
        # Setup
        self.mock_zabbix.host.get.return_value = [{"hostid": "456"}]

        # Execute
        with patch.object(self.device, 'create_journal_entry') as mock_journal_entry:
            self.device.cleanup()

        # Verify
        mock_journal_entry.assert_called_once_with("warning", "Deleted host from Zabbix")
