"""Unit tests for config module."""

import pytest

from tgsentinel.config import (
    AlertsCfg,
    AppCfg,
    ChannelRule,
    DigestCfg,
    load_config,
)


@pytest.mark.unit
class TestLoadConfig:
    """Test configuration loading."""

    def test_load_config_basic(self, temp_config_file, test_env_vars):
        """Test loading basic configuration."""
        cfg = load_config(temp_config_file)

        assert isinstance(cfg, AppCfg)
        assert cfg.api_id == 123456
        assert cfg.api_hash == "test_hash_123"
        assert cfg.telegram_session == "data/test.session"
        assert cfg.system.database_uri == "sqlite:///:memory:"
        assert cfg.similarity_threshold == 0.42

    def test_load_config_channels(self, temp_config_file, test_env_vars):
        """Test loading channel configuration."""
        cfg = load_config(temp_config_file)

        assert len(cfg.channels) == 1
        channel = cfg.channels[0]
        assert channel.id == -100123456789
        assert channel.name == "Test Channel"
        assert channel.vip_senders == [11111, 22222]
        assert channel.keywords == ["test", "important"]
        assert channel.reaction_threshold == 5
        assert channel.reply_threshold == 3
        assert channel.rate_limit_per_hour == 10

    def test_load_config_alerts(self, temp_config_file, test_env_vars, monkeypatch):
        """Test loading alerts configuration."""
        # Clear env overrides to test defaults from YAML
        monkeypatch.delenv("NOTIFICATION_MODE", raising=False)
        monkeypatch.delenv("NOTIFICATION_CHANNEL", raising=False)
        monkeypatch.delenv("HOURLY_DIGEST", raising=False)
        monkeypatch.delenv("DAILY_DIGEST", raising=False)

        cfg = load_config(temp_config_file)

        assert cfg.alerts.mode == "dm"
        assert cfg.alerts.target_channel == ""
        assert cfg.alerts.digest.hourly is True
        assert cfg.alerts.digest.daily is True
        assert cfg.alerts.digest.top_n == 10

    def test_load_config_alert_env_overrides(
        self, temp_config_file, test_env_vars, monkeypatch
    ):
        """Environment variables should override alert digest settings."""
        monkeypatch.setenv("NOTIFICATION_MODE", "channel")
        monkeypatch.setenv("NOTIFICATION_CHANNEL", "@your_notification_bot")
        monkeypatch.setenv("HOURLY_DIGEST", "false")
        monkeypatch.setenv("DAILY_DIGEST", "TRUE")
        monkeypatch.setenv("DIGEST_TOP_N", "7")

        cfg = load_config(temp_config_file)

        assert cfg.alerts.mode == "channel"
        assert cfg.alerts.target_channel == "@your_notification_bot"
        assert cfg.alerts.digest.hourly is False
        assert cfg.alerts.digest.daily is True
        assert cfg.alerts.digest.top_n == 7

    def test_load_config_interests(self, temp_config_file, test_env_vars):
        """Test loading interests configuration."""
        cfg = load_config(temp_config_file)

        assert len(cfg.interests) == 2
        assert "test topic" in cfg.interests
        assert "important subject" in cfg.interests

    def test_load_config_missing_api_id(self, temp_config_file, monkeypatch):
        """Test that missing API ID raises error."""
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.setenv("TG_API_HASH", "test_hash")

        with pytest.raises(ValueError, match="TG_API_ID"):
            load_config(temp_config_file)

    def test_load_config_missing_api_hash(self, temp_config_file, monkeypatch):
        """Test that missing API hash raises error."""
        monkeypatch.setenv("TG_API_ID", "123456")
        monkeypatch.delenv("TG_API_HASH", raising=False)

        with pytest.raises(ValueError, match="TG_API_HASH"):
            load_config(temp_config_file)

    def test_load_config_redis_defaults(
        self, temp_config_file, test_env_vars, monkeypatch
    ):
        """Test Redis configuration defaults."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)

        cfg = load_config(temp_config_file)

        # Access via system.redis dataclass (legacy dict property removed)
        assert cfg.system.redis.host == "redis"
        assert cfg.system.redis.port == 6379
        assert cfg.system.redis.stream == "tgsentinel:messages"
        assert cfg.system.redis.group == "workers"
        assert cfg.system.redis.consumer == "worker-1"

    def test_load_config_custom_redis(
        self, temp_config_file, test_env_vars, monkeypatch
    ):
        """Test custom Redis configuration."""
        monkeypatch.setenv("REDIS_HOST", "redis.example.com")
        monkeypatch.setenv("REDIS_PORT", "6380")
        monkeypatch.setenv("REDIS_STREAM", "custom:stream")

        cfg = load_config(temp_config_file)

        # Access via system.redis dataclass (legacy dict property removed)
        assert cfg.system.redis.host == "redis.example.com"
        assert cfg.system.redis.port == 6380
        assert cfg.system.redis.stream == "custom:stream"

    def test_load_config_embeddings_disabled(
        self, temp_config_file, test_env_vars, monkeypatch
    ):
        """Test that embeddings can be disabled."""
        monkeypatch.setenv("EMBEDDINGS_MODEL", "")

        cfg = load_config(temp_config_file)

        assert cfg.embeddings_model is None

    def test_load_config_embeddings_enabled(
        self, temp_config_file, test_env_vars, monkeypatch
    ):
        """Test that embeddings model can be specified."""
        monkeypatch.setenv("EMBEDDINGS_MODEL", "all-MiniLM-L6-v2")

        cfg = load_config(temp_config_file)

        assert cfg.embeddings_model == "all-MiniLM-L6-v2"


class TestChannelRule:
    """Test ChannelRule dataclass."""

    def test_channel_rule_defaults(self):
        """Test ChannelRule default values."""
        rule = ChannelRule(-100123456789)

        assert rule.id == -100123456789
        assert rule.name == ""
        assert rule.vip_senders == []
        assert rule.keywords == []
        assert rule.reaction_threshold == 0
        assert rule.reply_threshold == 0
        assert rule.rate_limit_per_hour == 10

    def test_channel_rule_custom_values(self):
        """Test ChannelRule with custom values."""
        rule = ChannelRule(
            id=-100123456789,
            name="Test Channel",
            vip_senders=[111, 222],
            keywords=["test", "important"],
            reaction_threshold=5,
            reply_threshold=3,
            rate_limit_per_hour=20,
        )

        assert rule.id == -100123456789
        assert rule.name == "Test Channel"
        assert rule.vip_senders == [111, 222]
        assert rule.keywords == ["test", "important"]
        assert rule.reaction_threshold == 5
        assert rule.reply_threshold == 3
        assert rule.rate_limit_per_hour == 20


class TestDigestCfg:
    """Test DigestCfg dataclass."""

    def test_digest_cfg_defaults(self):
        """Test DigestCfg default values."""
        digest = DigestCfg()

        assert digest.hourly is True
        assert (
            digest.daily is False
        )  # Changed default to False (hourly only by default)
        assert digest.top_n == 10

    def test_digest_cfg_custom_values(self):
        """Test DigestCfg with custom values."""
        digest = DigestCfg(hourly=False, daily=True, top_n=20)

        assert digest.hourly is False
        assert digest.daily is True
        assert digest.top_n == 20


class TestAlertsCfg:
    """Test AlertsCfg dataclass."""

    def test_alerts_cfg_defaults(self):
        """Test AlertsCfg default values."""
        alerts = AlertsCfg()

        assert alerts.mode == "dm"
        assert alerts.target_channel == ""
        assert isinstance(alerts.digest, DigestCfg)

    def test_alerts_cfg_custom_values(self):
        """Test AlertsCfg with custom values."""
        digest = DigestCfg(hourly=False, daily=True, top_n=5)
        alerts = AlertsCfg(
            mode="channel",
            target_channel="@mychannel",
            digest=digest,
        )

        assert alerts.mode == "channel"
        assert alerts.target_channel == "@mychannel"
        assert alerts.digest.hourly is False
        assert alerts.digest.top_n == 5
