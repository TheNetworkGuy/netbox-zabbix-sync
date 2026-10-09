"""Tests for device deletion functionality in the PhysicalDevice class."""

import unittest
from unittest.mock import patch

from zabbix_utils import APIRequestError

from netbox_zabbix_sync.modules.exceptions import SyncExternalError
from tests.fakes import FakeNetBox, physical_device, zabbix_api


class TestDeviceDeletion(unittest.TestCase):
    """Test class for device deletion functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.netbox = FakeNetBox()
        self.nb = self.netbox.device(
            "test-device",
            id=123,
            status="Decommissioning",
            custom_fields={"zabbix_hostid": "456"},
        )
        self.zabbix = zabbix_api(version="6.0")
        self.zabbix.host.get.return_value = [{"hostid": "456"}]
        self.device = physical_device(self.nb, zabbix=self.zabbix, journal_enabled=True)
        self.logger = self.device.logger
        self.journal = self.netbox.journal_entries
        # What clearing the link sends to NetBox
        self.cleared = [(self.nb.url, {"custom_fields": {"zabbix_hostid": None}})]

    def test_cleanup_successful_deletion(self):
        """Test successful device deletion from Zabbix."""
        self.zabbix.host.delete.return_value = {"hostids": ["456"]}

        self.device.cleanup()

        self.zabbix.host.get.assert_called_once_with(
            filter={"hostid": "456"}, output=[]
        )
        self.zabbix.host.delete.assert_called_once_with("456")
        self.assertEqual(self.netbox.patches, self.cleared)
        self.logger.info.assert_called_with(
            f"Host {self.device.name}: Deleted host from Zabbix."
        )

    def test_cleanup_device_already_deleted(self):
        """Test cleanup when device is already deleted from Zabbix."""
        self.zabbix.host.get.return_value = []  # Empty list means host not found

        self.device.cleanup()

        self.zabbix.host.get.assert_called_once_with(
            filter={"hostid": "456"}, output=[]
        )
        self.zabbix.host.delete.assert_not_called()
        self.assertEqual(self.netbox.patches, self.cleared)
        self.logger.info.assert_called_with(
            f"Host {self.device.name}: was already deleted from Zabbix. Removed link in NetBox."
        )

    def test_cleanup_api_error(self):
        """Test cleanup when Zabbix API returns an error."""
        self.zabbix.host.delete.side_effect = APIRequestError("API Error")

        with self.assertRaises(SyncExternalError):
            self.device.cleanup()

        self.zabbix.host.get.assert_called_once_with(
            filter={"hostid": "456"}, output=[]
        )
        self.zabbix.host.delete.assert_called_once_with("456")
        # The link in NetBox is kept, so the next run can retry
        self.assertEqual(self.netbox.patches, [])
        self.logger.error.assert_called()

    def test_zeroize_cf(self):
        """Test _zeroize_cf method that clears the custom field."""
        self.device._zeroize_cf()

        self.assertIsNone(self.nb.custom_fields["zabbix_hostid"])
        self.assertEqual(self.netbox.patches, self.cleared)

    def test_create_journal_entry(self):
        """Test create_journal_entry method."""
        test_message = "Test journal entry"

        result = self.device.create_journal_entry("info", test_message)

        self.assertTrue(result)
        self.journal.create.assert_called_once()
        journal_entry = self.journal.create.call_args[0][0]
        self.assertEqual(journal_entry["assigned_object_type"], "dcim.device")
        self.assertEqual(journal_entry["assigned_object_id"], 123)
        self.assertEqual(journal_entry["kind"], "info")
        self.assertEqual(journal_entry["comments"], test_message)

    def test_create_journal_entry_invalid_severity(self):
        """Test create_journal_entry with invalid severity."""
        result = self.device.create_journal_entry("invalid", "Test message")

        self.assertFalse(result)
        self.journal.create.assert_not_called()
        self.logger.warning.assert_called()

    def test_create_journal_entry_when_disabled(self):
        """Test create_journal_entry when journaling is disabled."""
        device = physical_device(self.nb, zabbix=self.zabbix, journal_enabled=False)

        result = device.create_journal_entry("info", "Test message")

        self.assertFalse(result)
        self.journal.create.assert_not_called()

    def test_cleanup_updates_journal(self):
        """Test that cleanup method creates a journal entry."""
        with patch.object(self.device, "create_journal_entry") as mock_journal_entry:
            self.device.cleanup()

        mock_journal_entry.assert_called_once_with(
            "warning", "Deleted host from Zabbix"
        )
