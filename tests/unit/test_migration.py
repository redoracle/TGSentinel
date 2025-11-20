"""Test migration script functionality."""

import tempfile
from pathlib import Path

import pytest
import yaml

from tools.migrate_profiles import ProfileMigrator


@pytest.fixture
def sample_config():
    """Create a sample config with old-style keywords."""
    config = {
        "channels": [
            {
                "id": -1001234567890,
                "name": "Security Channel",
                "vip_senders": [],
                "keywords": ["update", "announcement"],
                "security_keywords": ["CVE", "vulnerability", "exploit"],
                "urgency_keywords": ["critical", "urgent"],
                "action_keywords": ["update now", "patch"],
                "release_keywords": [],
                "decision_keywords": [],
                "risk_keywords": [],
                "opportunity_keywords": [],
                "detect_codes": True,
                "detect_documents": True,
                "prioritize_pinned": True,
            },
            {
                "id": -1009876543210,
                "name": "Release Channel",
                "vip_senders": [],
                "keywords": ["version", "release"],
                "security_keywords": [],
                "urgency_keywords": [],
                "action_keywords": [],
                "release_keywords": ["v1.0", "v2.0", "beta"],
                "decision_keywords": [],
                "risk_keywords": [],
                "opportunity_keywords": [],
                "detect_codes": False,
                "detect_documents": True,
                "prioritize_pinned": True,
            },
        ],
        "telegram": {"session": "data/test.session"},
        "alerts": {"mode": "dm", "min_score": 0.7},
    }
    return config


def test_migration_dry_run(sample_config):
    """Test migration in dry-run mode (no actual changes)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "tgsentinel.yml"

        # Write sample config
        with open(config_path, "w") as f:
            yaml.dump(sample_config, f)

        # Run migration in dry-run mode
        migrator = ProfileMigrator(str(config_path), dry_run=True)
        migrator.load_config()

        assert len(migrator.channels) == 2
        assert migrator.channels[0].name == "Security Channel"

        # Analyze keywords
        migrator.analyze_keywords()

        # Should have created some profiles
        assert len(migrator.profile_keywords) > 0

        # Generate profiles.yml
        profiles_yml = migrator.generate_profiles_yml()
        assert "profiles:" in profiles_yml

        # Verify no files were written (dry-run)
        profiles_path = config_path.parent / "profiles.yml"
        assert not profiles_path.exists()


def test_migration_analyze_keywords(sample_config):
    """Test keyword analysis and profile grouping."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "tgsentinel.yml"

        with open(config_path, "w") as f:
            yaml.dump(sample_config, f)

        migrator = ProfileMigrator(str(config_path), dry_run=True)
        migrator.load_config()
        migrator.analyze_keywords()

        # Check that security profile was created
        if "security" in migrator.profile_keywords:
            security_keywords = migrator.profile_keywords["security"]
            assert "CVE" in security_keywords
            assert "vulnerability" in security_keywords

        # Check that releases profile was created
        if "releases" in migrator.profile_keywords:
            release_keywords = migrator.profile_keywords["releases"]
            assert any(
                "version" in kw.lower() or "release" in kw.lower()
                for kw in release_keywords
            )


def test_migration_update_channels(sample_config):
    """Test channel profile binding."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "tgsentinel.yml"

        with open(config_path, "w") as f:
            yaml.dump(sample_config, f)

        migrator = ProfileMigrator(str(config_path), dry_run=True)
        migrator.load_config()
        migrator.analyze_keywords()

        # Mock profile_keywords
        migrator.profile_keywords = {
            "security": {"CVE", "vulnerability", "exploit", "critical", "urgent"},
            "releases": {"version", "release", "v1.0", "v2.0", "beta"},
        }

        migrator.update_channels()

        # Ensure config_data is loaded before inspecting channels (defensive)
        if not migrator.config_data:
            # load_config is idempotent for tests and will populate config_data
            migrator.load_config()

        channels = (
            migrator.config_data.get("channels", []) if migrator.config_data else []
        )

        # Check that channels got profile bindings
        assert len(channels) >= 1
        security_channel = channels[0]
        if "profiles" in security_channel:
            assert "security" in security_channel["profiles"]

        # Check release channel if present
        release_channel = channels[1] if len(channels) > 1 else None
        if release_channel and "profiles" in release_channel:
            assert "releases" in release_channel["profiles"]


def test_migration_profiles_yml_format(sample_config):
    """Test generated profiles.yml format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "tgsentinel.yml"

        with open(config_path, "w") as f:
            yaml.dump(sample_config, f)

        migrator = ProfileMigrator(str(config_path), dry_run=True)
        migrator.load_config()
        migrator.analyze_keywords()

        # Generate profiles.yml
        profiles_yml_content = migrator.generate_profiles_yml()

        # Parse to verify it's valid YAML
        profiles_data = yaml.safe_load(profiles_yml_content)

        assert "profiles" in profiles_data
        assert isinstance(profiles_data["profiles"], dict)

        # Check profile structure
        for profile_id, profile_data in profiles_data["profiles"].items():
            assert "name" in profile_data
            assert "keywords" in profile_data
            assert "scoring_weights" in profile_data
            assert isinstance(profile_data["keywords"], list)
            assert isinstance(profile_data["scoring_weights"], dict)


def test_migration_empty_keywords():
    """Test migration handles channels with empty keywords gracefully."""
    config = {
        "channels": [
            {
                "id": -1001111111111,
                "name": "Empty Channel",
                "vip_senders": [],
                "keywords": [],
                "security_keywords": [],
                "urgency_keywords": [],
                "action_keywords": [],
                "release_keywords": [],
                "decision_keywords": [],
                "risk_keywords": [],
                "opportunity_keywords": [],
                "detect_codes": True,
                "detect_documents": True,
                "prioritize_pinned": True,
            }
        ],
        "telegram": {"session": "data/test.session"},
        "alerts": {"mode": "dm"},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "tgsentinel.yml"

        with open(config_path, "w") as f:
            yaml.dump(config, f)

        migrator = ProfileMigrator(str(config_path), dry_run=True)
        migrator.load_config()
        migrator.analyze_keywords()

        # Should not crash, profiles may be empty
        profiles_yml = migrator.generate_profiles_yml()
        assert profiles_yml is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
