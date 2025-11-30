#!/usr/bin/env python3
"""
Migrate profiles to Phase 2 schema.

Adds feedback_*_samples and pending_*_samples fields to both
interest and alert profiles. Also adds auto_tuning configuration.
"""

import os
import sys
import tempfile
from pathlib import Path

import yaml

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def migrate_profile_file(profile_path: Path, profile_type: str = "interest"):
    """
    Add Phase 2 fields to profile YAML.

    Args:
        profile_path: Path to profiles YAML file
        profile_type: Type of profile ('interest' or 'alert')
    """
    if not profile_path.exists():
        print(f"⚠ {profile_path} not found, skipping")
        return

    print(f"📄 Processing {profile_path}")

    with open(profile_path, "r", encoding="utf-8") as f:
        profiles = yaml.safe_load(f) or {}

    if not profiles:
        print(f"⚠ {profile_path} is empty, skipping")
        return

    migrated = False
    for profile_id, profile in profiles.items():
        # Skip if already migrated (check for feedback_positive_samples)
        if "feedback_positive_samples" in profile:
            continue

        # Add Phase 2 fields
        profile["feedback_positive_samples"] = []
        profile["feedback_negative_samples"] = []
        profile["pending_positive_samples"] = []
        profile["pending_negative_samples"] = []

        # Add auto_tuning configuration if not present
        if "auto_tuning" not in profile:
            profile["auto_tuning"] = {
                "enabled": True,
                "max_feedback_samples": 20,
                "max_threshold_delta": 0.25,
                "max_delta_positive_weight": 0.2,
                "max_delta_negative_weight": 0.1,
            }

        migrated = True
        print(f"  ✓ Migrated profile {profile_id}")

    if migrated:
        # Atomic save using temp file
        temp_fd, temp_path = tempfile.mkstemp(
            dir=profile_path.parent, prefix=".profiles_", suffix=".yml"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    profiles, f, default_flow_style=False, allow_unicode=True
                )

            # Atomic replace
            os.replace(temp_path, profile_path)
            print(f"✅ Migrated {profile_path}")
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise e
    else:
        print(f"✅ {profile_path} already migrated")


def main():
    """Run Phase 2 migration on all profile files."""
    # Determine config directory
    config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
    if not config_dir.exists():
        # Try relative path for local development
        config_dir = Path(__file__).parent.parent / "config"

    if not config_dir.exists():
        print(f"❌ Config directory not found: {config_dir}")
        sys.exit(1)

    print(f"🔧 Running Phase 2 migration on config dir: {config_dir}\n")

    # Migrate interest profiles
    migrate_profile_file(config_dir / "profiles_interest.yml", "interest")

    # Migrate alert profiles
    migrate_profile_file(config_dir / "profiles_alert.yml", "alert")

    print("\n✅ Phase 2 migration complete!")
    print("\n📝 Next steps:")
    print("   1. Review migrated profiles in config/profiles_*.yml")
    print("   2. Rebuild and restart sentinel service")
    print("   3. Verify with: docker compose logs sentinel | grep 'Phase 2'")


if __name__ == "__main__":
    main()
