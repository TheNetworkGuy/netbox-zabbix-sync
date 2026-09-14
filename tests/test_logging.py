"""Tests for the logging module and log configuration options."""

import argparse
from logging import getLogger
from logging.handlers import RotatingFileHandler
from os import path
from unittest.mock import MagicMock, patch

import pytest

from netbox_zabbix_sync.modules.cli import main
from netbox_zabbix_sync.modules.core import Sync
from netbox_zabbix_sync.modules.logging import (
    DEFAULT_LOG_FILE,
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    get_log_file_path,
    get_logger,
    setup_logger,
)
from netbox_zabbix_sync.modules.settings import DEFAULT_CONFIG


@pytest.fixture
def basic_config():
    """Capture basicConfig calls and close the file handlers created for them."""
    with patch("netbox_zabbix_sync.modules.logging.basicConfig") as mock:
        yield mock
        for call in mock.call_args_list:
            for handler in call.kwargs["handlers"]:
                handler.close()


def _file_handler(basic_config):
    handlers = basic_config.call_args.kwargs["handlers"]
    return next(h for h in handlers if isinstance(h, RotatingFileHandler))


class TestGetLogFilePath:
    def test_defaults_to_sync_log_in_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        expected = path.join(path.realpath(tmp_path), DEFAULT_LOG_FILE)
        assert get_log_file_path() == expected
        assert get_log_file_path("") == expected

    def test_uses_given_file_path(self, tmp_path):
        log_file = tmp_path / "logs" / "custom.log"
        assert get_log_file_path(str(log_file)) == path.realpath(log_file)

    def test_relative_path_resolves_against_working_directory(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        assert get_log_file_path("logs/custom.log") == path.join(
            path.realpath(tmp_path), "logs", "custom.log"
        )

    def test_expands_home_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert get_log_file_path("~/custom.log") == path.join(
            path.realpath(tmp_path), "custom.log"
        )

    def test_existing_directory_gets_default_file_name(self, tmp_path):
        assert get_log_file_path(str(tmp_path)) == path.join(
            path.realpath(tmp_path), DEFAULT_LOG_FILE
        )

    def test_trailing_separator_gets_default_file_name(self, tmp_path):
        log_dir = tmp_path / "not-yet-created"
        assert get_log_file_path(f"{log_dir}/") == path.join(
            path.realpath(log_dir), DEFAULT_LOG_FILE
        )


class TestSetupLogger:
    def test_default_log_file_in_working_directory(
        self, tmp_path, monkeypatch, basic_config
    ):
        monkeypatch.chdir(tmp_path)
        logfile_path = setup_logger()
        assert logfile_path == path.join(path.realpath(tmp_path), DEFAULT_LOG_FILE)
        assert _file_handler(basic_config).baseFilename == logfile_path

    def test_custom_log_file_creates_parent_directories(self, tmp_path, basic_config):
        log_file = tmp_path / "nested" / "dir" / "custom.log"
        logfile_path = setup_logger(log_file=str(log_file))
        assert log_file.parent.is_dir()
        assert _file_handler(basic_config).baseFilename == logfile_path

    def test_file_handler_rotates(self, tmp_path, basic_config):
        setup_logger(log_file=str(tmp_path / "custom.log"))
        handler = _file_handler(basic_config)
        assert handler.maxBytes == LOG_MAX_BYTES
        assert handler.backupCount == LOG_BACKUP_COUNT


class TestSyncLogger:
    def test_defaults_to_package_logger(self):
        assert Sync().logger is get_logger()

    def test_uses_custom_logger(self):
        custom_logger = getLogger("custom-test-logger")
        assert Sync(logger=custom_logger).logger is custom_logger

    def test_custom_logger_receives_messages(self):
        custom_logger = MagicMock()
        syncer = Sync(logger=custom_logger)
        assert syncer.start() is False
        custom_logger.error.assert_called_once()


class TestCliLogFile:
    def test_log_file_defaults_to_none(self):
        assert DEFAULT_CONFIG["log_file"] is None

    @pytest.mark.parametrize(
        ("config_value", "cli_value", "expected"),
        [
            (None, None, None),
            ("/from/config.log", None, "/from/config.log"),
            ("/from/config.log", "/from/cli.log", "/from/cli.log"),
        ],
    )
    def test_main_passes_log_file_to_setup_logger(
        self, monkeypatch, config_value, cli_value, expected
    ):
        monkeypatch.setenv("ZABBIX_HOST", "https://zabbix.local")
        monkeypatch.setenv("NETBOX_HOST", "https://netbox.local")
        monkeypatch.setenv("NETBOX_TOKEN", "token")
        monkeypatch.setenv("ZABBIX_TOKEN", "token")
        config = {**DEFAULT_CONFIG, "log_file": config_value}
        arguments = argparse.Namespace(
            verbose=False,
            debug=False,
            debug_all=False,
            quiet=False,
            config=None,
            log_file=cli_value,
        )
        with (
            patch("netbox_zabbix_sync.modules.cli.load_config", return_value=config),
            patch("netbox_zabbix_sync.modules.cli.setup_logger") as mock_setup,
            patch("netbox_zabbix_sync.modules.cli.Sync"),
        ):
            main(arguments)
        mock_setup.assert_called_once_with(log_file=expected)
