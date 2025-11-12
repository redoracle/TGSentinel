"""
Test configuration priority: Environment variables should override YAML values.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tgsentinel.config import AppCfg, load_config


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    config_data = {
        "telegram": {"session": "test.session"},
        "alerts": {
            "mode": "dm",
            "target_channel": "",
            "digest": {"hourly": False, "daily": False, "top_n": 5},
        },
        "channels": [],
        "interests": ["test interest"],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    Path(config_path).unlink(missing_ok=True)


def test_env_vars_override_yaml_alerts(temp_config_file):
    """Test that ALERT_MODE and ALERT_CHANNEL env vars override YAML."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"
    os.environ["ALERT_MODE"] = "both"
    os.environ["ALERT_CHANNEL"] = "@test_bot"

    try:
        config = load_config(temp_config_file)
        assert config.alerts.mode == "both"
        assert config.alerts.target_channel == "@test_bot"
    finally:
        for key in ["TG_API_ID", "TG_API_HASH", "ALERT_MODE", "ALERT_CHANNEL"]:
            os.environ.pop(key, None)


def test_env_vars_override_yaml_digest(temp_config_file):
    """Test that digest env vars override YAML."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"
    os.environ["HOURLY_DIGEST"] = "true"
    os.environ["DAILY_DIGEST"] = "true"
    os.environ["DIGEST_TOP_N"] = "20"

    try:
        config = load_config(temp_config_file)
        assert config.alerts.digest.hourly is True
        assert config.alerts.digest.daily is True
        assert config.alerts.digest.top_n == 20
    finally:
        for key in [
            "TG_API_ID",
            "TG_API_HASH",
            "HOURLY_DIGEST",
            "DAILY_DIGEST",
            "DIGEST_TOP_N",
        ]:
            os.environ.pop(key, None)


def test_env_vars_override_yaml_redis(temp_config_file):
    """Test that Redis env vars override defaults."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"
    os.environ["REDIS_HOST"] = "custom-redis"
    os.environ["REDIS_PORT"] = "6380"
    os.environ["REDIS_STREAM"] = "custom:stream"

    try:
        config = load_config(temp_config_file)
        assert config.redis["host"] == "custom-redis"
        assert config.redis["port"] == 6380
        assert config.redis["stream"] == "custom:stream"
    finally:
        for key in [
            "TG_API_ID",
            "TG_API_HASH",
            "REDIS_HOST",
            "REDIS_PORT",
            "REDIS_STREAM",
        ]:
            os.environ.pop(key, None)


def test_env_vars_override_yaml_semantic(temp_config_file):
    """Test that semantic scoring env vars override defaults."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"
    os.environ["EMBEDDINGS_MODEL"] = "custom-model"
    os.environ["SIMILARITY_THRESHOLD"] = "0.75"

    try:
        config = load_config(temp_config_file)
        assert config.embeddings_model == "custom-model"
        assert config.similarity_threshold == 0.75
    finally:
        for key in [
            "TG_API_ID",
            "TG_API_HASH",
            "EMBEDDINGS_MODEL",
            "SIMILARITY_THRESHOLD",
        ]:
            os.environ.pop(key, None)


def test_yaml_defaults_when_no_env_vars(temp_config_file):
    """Test that YAML values are used when env vars are not set."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"

    # Ensure no override env vars are set
    for key in ["ALERT_MODE", "ALERT_CHANNEL", "HOURLY_DIGEST"]:
        os.environ.pop(key, None)

    try:
        config = load_config(temp_config_file)
        # Should use YAML values
        assert config.alerts.mode == "dm"
        assert config.alerts.target_channel == ""
        assert config.alerts.digest.hourly is False
        assert config.alerts.digest.top_n == 5
    finally:
        os.environ.pop("TG_API_ID", None)
        os.environ.pop("TG_API_HASH", None)


def test_required_env_vars_missing():
    """Test that missing required env vars raise errors."""
    os.environ.pop("TG_API_ID", None)
    os.environ.pop("TG_API_HASH", None)

    config_data = {
        "telegram": {"session": "test.session"},
        "alerts": {},
        "channels": [],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    try:
        with pytest.raises(ValueError, match="TG_API_ID"):
            load_config(config_path)
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_invalid_env_var_types(temp_config_file):
    """Test that invalid env var types are handled."""
    os.environ["TG_API_ID"] = "not_a_number"
    os.environ["TG_API_HASH"] = "test_hash"

    try:
        with pytest.raises(ValueError):
            load_config(temp_config_file)
    finally:
        os.environ.pop("TG_API_ID", None)
        os.environ.pop("TG_API_HASH", None)


def test_env_bool_parsing(temp_config_file):
    """Test that boolean env vars are parsed correctly."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"

    test_cases = [
        ("1", True),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
    ]

    for value, expected in test_cases:
        os.environ["HOURLY_DIGEST"] = value
        try:
            config = load_config(temp_config_file)
            assert config.alerts.digest.hourly is expected, f"Failed for value: {value}"
        finally:
            os.environ.pop("HOURLY_DIGEST", None)

    os.environ.pop("TG_API_ID", None)
    os.environ.pop("TG_API_HASH", None)


def test_config_instance_type(temp_config_file):
    """Test that load_config returns correct type."""
    os.environ["TG_API_ID"] = "12345"
    os.environ["TG_API_HASH"] = "test_hash"

    try:
        config = load_config(temp_config_file)
        assert isinstance(config, AppCfg)
        assert hasattr(config, "api_id")
        assert hasattr(config, "api_hash")
        assert hasattr(config, "alerts")
        assert hasattr(config, "redis")
        assert hasattr(config, "db_uri")
    finally:
        os.environ.pop("TG_API_ID", None)
        os.environ.pop("TG_API_HASH", None)
