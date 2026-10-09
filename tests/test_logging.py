"""Tests for the logging module and log configuration options."""

import argparse
from logging import FileHandler, Handler, NullHandler, StreamHandler, getLogger
from logging.handlers import RotatingFileHandler
from os import path
from unittest.mock import MagicMock, patch

import pytest

from netbox_zabbix_sync.modules.cli import main, parse_cli
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
        mock_setup.assert_called_once_with(
            log_file=expected,
            log_rotation=True,
            log_console=True,
            log_handlers=None,
        )

    def test_main_passes_logging_settings_to_setup_logger(self, monkeypatch):
        monkeypatch.setenv("ZABBIX_HOST", "https://zabbix.local")
        monkeypatch.setenv("NETBOX_HOST", "https://netbox.local")
        monkeypatch.setenv("NETBOX_TOKEN", "token")
        monkeypatch.setenv("ZABBIX_TOKEN", "token")
        custom_handler = NullHandler()
        config = {
            **DEFAULT_CONFIG,
            "log_rotation": True,
            "log_console": True,
            "log_handlers": [custom_handler],
        }
        arguments = argparse.Namespace(
            verbose=False,
            debug=False,
            debug_all=False,
            quiet=False,
            config=None,
            log_file=False,
            log_rotation=False,
            log_console=False,
        )
        with (
            patch("netbox_zabbix_sync.modules.cli.load_config", return_value=config),
            patch("netbox_zabbix_sync.modules.cli.setup_logger") as mock_setup,
            patch("netbox_zabbix_sync.modules.cli.Sync"),
        ):
            main(arguments)
        mock_setup.assert_called_once_with(
            log_file=False,
            log_rotation=False,
            log_console=False,
            log_handlers=[custom_handler],
        )

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            ([], None),
            (["--log-file", "/tmp/custom.log"], "/tmp/custom.log"),  # noqa: S108
            (["--no-log-file"], False),
        ],
    )
    def test_log_file_flags(self, monkeypatch, argv, expected):
        monkeypatch.setattr("sys.argv", ["netbox_zabbix_sync.py", *argv])
        with patch("netbox_zabbix_sync.modules.cli.main") as mock_main:
            parse_cli()
        assert mock_main.call_args.args[0].log_file == expected


class TestLoggingToggles:
    def test_disabled_log_file(self):
        assert get_log_file_path(False) is None

    def test_none_and_true_use_default_location(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        expected = path.join(path.realpath(tmp_path), DEFAULT_LOG_FILE)
        assert get_log_file_path(None) == expected
        assert get_log_file_path(True) == expected

    def test_path_objects_are_accepted(self, tmp_path):
        log_file = tmp_path / "custom.log"
        assert get_log_file_path(log_file) == path.realpath(log_file)

    @pytest.mark.parametrize("log_file", [0, 1, ["sync.log"]])
    def test_invalid_log_file_type_raises(self, log_file):
        with pytest.raises(TypeError, match="log_file"):
            get_log_file_path(log_file)

    def test_no_log_file_keeps_console(self, tmp_path, monkeypatch, basic_config):
        monkeypatch.chdir(tmp_path)
        assert setup_logger(log_file=False) is None
        handlers = basic_config.call_args.kwargs["handlers"]
        assert [type(h) for h in handlers] == [StreamHandler]
        assert not (tmp_path / DEFAULT_LOG_FILE).exists()

    def test_plain_file_handler_without_rotation(self, tmp_path, basic_config):
        logfile_path = setup_logger(
            log_file=str(tmp_path / "custom.log"), log_rotation=False
        )
        handlers = basic_config.call_args.kwargs["handlers"]
        file_handlers = [h for h in handlers if isinstance(h, FileHandler)]
        assert len(file_handlers) == 1
        assert type(file_handlers[0]) is FileHandler
        assert file_handlers[0].baseFilename == logfile_path

    def test_disable_console(self, tmp_path, basic_config):
        setup_logger(log_file=str(tmp_path / "custom.log"), log_console=False)
        handlers = basic_config.call_args.kwargs["handlers"]
        assert not any(type(h) is StreamHandler for h in handlers)
        assert _file_handler(basic_config)

    def test_all_logging_disabled_uses_null_handler(self, basic_config):
        setup_logger(log_file=False, log_console=False)
        handlers = basic_config.call_args.kwargs["handlers"]
        assert [type(h) for h in handlers] == [NullHandler]


class TestCustomHandlers:
    def test_custom_handlers_are_added(self, tmp_path, basic_config):
        first, second = NullHandler(), NullHandler()
        setup_logger(
            log_file=str(tmp_path / "custom.log"), log_handlers=[first, second]
        )
        handlers = basic_config.call_args.kwargs["handlers"]
        assert handlers[-2:] == [first, second]
        assert type(handlers[0]) is StreamHandler
        assert _file_handler(basic_config)

    def test_single_custom_handler(self, basic_config):
        custom_handler = NullHandler()
        setup_logger(log_file=False, log_console=False, log_handlers=custom_handler)
        assert basic_config.call_args.kwargs["handlers"] == [custom_handler]

    @pytest.mark.parametrize("log_handlers", ["syslog", [NullHandler(), "syslog"]])
    def test_invalid_custom_handlers_raise(self, basic_config, log_handlers):
        with pytest.raises(TypeError, match="log_handlers"):
            setup_logger(log_file=False, log_handlers=log_handlers)

    def test_custom_handler_receives_records(self):
        """Run the real basicConfig on a clean root logger."""
        records = []

        class ListHandler(Handler):
            def emit(self, record):
                records.append(record)

        root = getLogger()
        saved_handlers = root.handlers[:]
        root.handlers = []
        try:
            setup_logger(log_file=False, log_console=False, log_handlers=ListHandler())
            get_logger().warning("sent to custom handler")
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = saved_handlers
        assert [r.getMessage() for r in records] == ["sent to custom handler"]
        assert root.handlers == saved_handlers
