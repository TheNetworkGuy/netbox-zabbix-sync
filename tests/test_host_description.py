"""Tests for the Description class in the host_description module."""

import unittest
from unittest.mock import MagicMock, patch

from netbox_zabbix_sync.modules.host_description import Description


class TestDescription(unittest.TestCase):
    """Test class for Description functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock NetBox object
        self.mock_nb_object = MagicMock()
        self.mock_nb_object.name = "test-host"
        self.mock_nb_object.owner = "admin"
        self.mock_nb_object.config_context = {}

        # Create logger mock
        self.mock_logger = MagicMock()

        # Base configuration
        self.base_config = {}

    # Test 1: Config context description override
    @patch("netbox_zabbix_sync.modules.host_description.datetime")
    def test_1_config_context_override_value(self, mock_datetime):
        """Test 1: User that provides a config context description value should get this override value back."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-02-25 10:30:00"
        mock_datetime.now.return_value = mock_now

        # Set config context with description
        self.mock_nb_object.config_context = {
            "zabbix": {"description": "Custom override for {owner}"}
        }

        config = {"description": "static"}
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        # Should use config context, not config
        self.assertEqual(result, "Custom override for admin")

    # Test 2: Static description
    def test_2_static_description(
        self,
    ):
        """Test 2: User that provides static as description should get the default static value."""
        config = {"description": "static"}
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        self.assertEqual(result, "Host added by NetBox sync script.")

    # Test 3: Dynamic description
    @patch("netbox_zabbix_sync.modules.host_description.datetime")
    def test_3_dynamic_description(self, mock_datetime):
        """Test 3: User that provides 'dynamic' should get the resolved description string back."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-02-25 10:30:00"
        mock_datetime.now.return_value = mock_now

        config = {"description": "dynamic"}
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        expected = (
            "Host by owner admin added by NetBox sync script on 2026-02-25 10:30:00."
        )
        self.assertEqual(result, expected)

    # Test 4: Invalid macro fallback
    def test_4_invalid_macro_fallback_to_static(self):
        """Test 4: Users who provide invalid macros should fallback to the static variant."""
        config = {"description": "Host {owner} with {invalid_macro}"}
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        # Should fall back to static default
        self.assertEqual(result, "Host added by NetBox sync script.")
        # Verify warning was logged
        self.mock_logger.warning.assert_called_once()

    # Test 5: Custom time format
    @patch("netbox_zabbix_sync.modules.host_description.datetime")
    def test_5_custom_datetime_format(self, mock_datetime):
        """Test 5: Users who change the time format."""
        mock_now = MagicMock()
        # Will be called twice: once with custom format, once for string
        mock_now.strftime.side_effect = ["25/02/2026", "25/02/2026"]
        mock_datetime.now.return_value = mock_now

        config = {
            "description": "Updated on {datetime}",
            "description_dt_format": "%d/%m/%Y",
        }
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        self.assertEqual(result, "Updated on 25/02/2026")

    # Test 6: Custom description format in config
    @patch("netbox_zabbix_sync.modules.host_description.datetime")
    def test_6_custom_description_format(self, mock_datetime):
        """Test 6: Users who provide a custom description format in the config."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-02-25 10:30:00"
        mock_datetime.now.return_value = mock_now

        config = {"description": "Server {owner} managed at {datetime}"}
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        self.assertEqual(result, "Server admin managed at 2026-02-25 10:30:00")

    # Test 7: Owner on lower NetBox version
    @patch("netbox_zabbix_sync.modules.host_description.datetime")
    def test_7_owner_on_lower_netbox_version(self, mock_datetime):
        """Test 7: Users who try to resolve the owner property on a lower NetBox version (3.2)."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-02-25 10:30:00"
        mock_datetime.now.return_value = mock_now

        config = {"description": "Device owned by {owner}"}
        desc = Description(
            self.mock_nb_object,
            config,
            "3.2",  # Lower NetBox version
            logger=self.mock_logger,
        )

        result = desc.generate()
        # Owner should be empty string on version < 4.5
        self.assertEqual(result, "Device owned by ")

    # Test 8: Missing or False description returns static
    def test_8a_missing_description_returns_static(self):
        """Test 8a: When description option is not found, script should return the static variant."""
        config = {}  # No description key
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        self.assertEqual(result, "Host added by NetBox sync script.")

    def test_8b_false_description_returns_empty(self):
        """Test 8b: When description is set to False, script should return empty string."""
        config = {"description": False}
        desc = Description(self.mock_nb_object, config, "4.5", logger=self.mock_logger)

        result = desc.generate()
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
