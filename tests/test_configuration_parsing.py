"""Tests for configuration parsing in the modules.config module."""

import os
from unittest.mock import MagicMock, patch

from modules.config import (
    DEFAULT_CONFIG,
    load_config,
    load_config_file,
    load_env_variable,
)


def test_load_config_defaults():
    """Test that load_config returns default values when no config file or env vars are present"""
    with (
        patch("modules.config.load_config_file", return_value=DEFAULT_CONFIG.copy()),
        patch("modules.config.load_env_variable", return_value=None),
    ):
        config = load_config()
        assert config == DEFAULT_CONFIG
        assert config["templates_config_context"] is False
        assert config["create_hostgroups"] is True


def test_load_config_file():
    """Test that load_config properly loads values from config file"""
    mock_config = DEFAULT_CONFIG.copy()
    mock_config["templates_config_context"] = True
    mock_config["sync_vms"] = True

    with (
        patch("modules.config.load_config_file", return_value=mock_config),
        patch("modules.config.load_env_variable", return_value=None),
    ):
        config = load_config()
        assert config["templates_config_context"] is True
        assert config["sync_vms"] is True
        # Unchanged values should remain as defaults
        assert config["create_journal"] is False


def test_load_env_variables():
    """Test that load_config properly loads values from environment variables"""

    # Mock env variable loading to return values for specific keys
    def mock_load_env(key):
        if key == "sync_vms":
            return True
        if key == "create_journal":
            return True
        return None

    with (
        patch("modules.config.load_config_file", return_value=DEFAULT_CONFIG.copy()),
        patch("modules.config.load_env_variable", side_effect=mock_load_env),
    ):
        config = load_config()
        assert config["sync_vms"] is True
        assert config["create_journal"] is True
        # Unchanged values should remain as defaults
        assert config["templates_config_context"] is False


def test_env_vars_override_config_file():
    """Test that environment variables override values from config file"""
    mock_config = DEFAULT_CONFIG.copy()
    mock_config["templates_config_context"] = True
    mock_config["sync_vms"] = False

    # Mock env variable that will override the config file value
    def mock_load_env(key):
        if key == "sync_vms":
            return True
        return None

    with (
        patch("modules.config.load_config_file", return_value=mock_config),
        patch("modules.config.load_env_variable", side_effect=mock_load_env),
    ):
        config = load_config()
        # This should be overridden by the env var
        assert config["sync_vms"] is True
        # This should remain from the config file
        assert config["templates_config_context"] is True


def test_load_config_file_function():
    """Test the load_config_file function directly"""
    # Test when the file exists
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("importlib.util.spec_from_file_location") as mock_spec,
    ):
        # Setup the mock module with attributes
        mock_module = MagicMock()
        mock_module.templates_config_context = True
        mock_module.sync_vms = True

        # Setup the mock spec
        mock_spec_instance = MagicMock()
        mock_spec.return_value = mock_spec_instance
        mock_spec_instance.loader.exec_module = lambda x: None

        # Patch module_from_spec to return our mock module
        with patch("importlib.util.module_from_spec", return_value=mock_module):
            config = load_config_file(DEFAULT_CONFIG.copy())
            assert config["templates_config_context"] is True
            assert config["sync_vms"] is True


def test_load_config_file_not_found():
    """Test load_config_file when the config file doesn't exist"""
    with patch("pathlib.Path.exists", return_value=False):
        result = load_config_file(DEFAULT_CONFIG.copy())
        # Should return a dict equal to DEFAULT_CONFIG, not a new object
        assert result == DEFAULT_CONFIG


def test_load_env_variable_function():
    """Test the load_env_variable function directly"""
    # Create a real environment variable for testing with correct prefix and uppercase
    test_var = "NBZX_TEMPLATES_CONFIG_CONTEXT"
    original_env = os.environ.get(test_var, None)
    try:
        # Set the environment variable with the proper prefix and case
        os.environ[test_var] = "True"

        # Test that it's properly read (using lowercase in the function call)
        value = load_env_variable("templates_config_context")
        assert value == "True"

        # Test when the environment variable doesn't exist
        value = load_env_variable("nonexistent_variable")
        assert value is None
    finally:
        # Clean up - restore original environment
        if original_env is not None:
            os.environ[test_var] = original_env
        else:
            os.environ.pop(test_var, None)
