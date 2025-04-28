"""Testing device creation"""
from unittest.mock import MagicMock, patch, call
from modules.device import PhysicalDevice
from modules.config import load_config

config = load_config()


def mock_nb_device():
    """Function to mock Netbox device"""
    mock = MagicMock()
    mock.id = 1
    mock.url = "http://netbox:8000/api/dcim/devices/1/"
    mock.display_url = "http://netbox:8000/dcim/devices/1/"
    mock.display = "SW01"
    mock.name = "SW01"

    mock.device_type = MagicMock()
    mock.device_type.display = "Catalyst 3750G-48TS-S"
    mock.device_type.manufacturer = MagicMock()
    mock.device_type.manufacturer.display = "Cisco"
    mock.device_type.manufacturer.name = "Cisco"
    mock.device_type.manufacturer.slug = "cisco"
    mock.device_type.manufacturer.description = ""
    mock.device_type.model = "Catalyst 3750G-48TS-S"
    mock.device_type.slug = "cisco-ws-c3750g-48ts-s"

    mock.role = MagicMock()
    mock.role.id = 1
    mock.role.display = "Switch"
    mock.role.name = "Switch"
    mock.role.slug = "switch"

    mock.tenant = None
    mock.platform = None
    mock.serial = "0031876"
    mock.asset_tag = None

    mock.site = MagicMock()
    mock.site.display = "AMS01"
    mock.site.name = "AMS01"
    mock.site.slug = "ams01"

    mock.location = None
    mock.rack = None
    mock.position = None
    mock.face = None
    mock.parent_device = None

    mock.status = MagicMock()
    mock.status.value = "decommissioning"
    mock.status.label = "Decommissioning"

    mock.cluster = None
    mock.virtual_chassis = None
    mock.vc_position = None
    mock.vc_priority = None
    mock.description = ""
    mock.comments = ""
    mock.config_template = None
    mock.config_context = {}
    mock.local_context_data = None

    mock.custom_fields = {"zabbix_hostid": 1956}
    return mock


def mock_zabbix():
    """Function to mock Zabbix"""
    mock = MagicMock()
    mock.host.get.return_value = [{}]
    mock.host.delete.return_value = True
    return mock


netbox_journals = MagicMock()
NB_VERSION = '4.2'
create_journal = MagicMock()
logger = MagicMock()


def test_check_cluster_status():
    """Checks if the isCluster function is functioning properly"""
    nb_device = mock_nb_device()
    zabbix = mock_zabbix()
    device = PhysicalDevice(nb_device, zabbix, None, None,
                            None, logger)
    assert device.isCluster() is False


def test_device_deletion_host_exists():
    """Checks device deletion process"""
    nb_device = mock_nb_device()
    zabbix = mock_zabbix()
    with patch.object(PhysicalDevice, 'create_journal_entry') as mock_journal:
        # Create device
        device = PhysicalDevice(nb_device, zabbix, netbox_journals, NB_VERSION,
                                create_journal, logger)
        device.cleanup()
        # Check if Zabbix HostID is empty
        assert device.nb.custom_fields[config["device_cf"]] is None
        # Check if API calls are executed
        device.zabbix.host.get.assert_called_once_with(filter={'hostid': 1956},
                                                       output=[])
        device.zabbix.host.delete.assert_called_once_with(1956)
        # check logger
        mock_journal.assert_called_once_with("warning",
                                             "Deleted host from Zabbix")
        device.logger.info.assert_called_once_with("Host SW01: Deleted "
                                                   "host from Zabbix.")


def test_device_deletion_host_not_exists():
    """
    Test if device in Netbox gets unlinked
    when host is not present in Zabbix
    """
    nb_device = mock_nb_device()
    zabbix = mock_zabbix()
    zabbix.host.get.return_value = None

    with patch.object(PhysicalDevice, 'create_journal_entry') as mock_journal:
        # Create new device
        device = PhysicalDevice(nb_device, zabbix, netbox_journals, NB_VERSION,
                                create_journal, logger)
        # Try to clean the device up in Zabbix
        device.cleanup()
        # Confirm that a call was issued to Zabbix to check if the host exists
        device.zabbix.host.get.assert_called_once_with(filter={'hostid': 1956},
                                                       output=[])
        # Confirm that no device was deleted in Zabbix
        device.zabbix.host.delete.assert_not_called()
        # Test logging
        log_calls = [
            call('Host SW01: Deleted host from Zabbix.'),
            call('Host SW01: was already deleted from Zabbix. '
                 'Removed link in NetBox.')
        ]
        logger.info.assert_has_calls(log_calls)
        assert logger.info.call_count == 2
        mock_journal.assert_called_once_with("warning",
                                             "Deleted host from Zabbix")
