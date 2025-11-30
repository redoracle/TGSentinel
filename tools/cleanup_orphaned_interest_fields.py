#!/usr/bin/env python3
"""
Clean up orphaned and legacy duplicate fields from Interest profile YAMLs.

Removes:
- notify_always (orphaned - saved but not consumed)
- include_digest (orphaned - saved but not consumed)
- digest_mode (legacy duplicate of digest.mode)
- digest_target_channel (legacy duplicate of digest.target_channel)
- digest_schedules (legacy duplicate of digest.schedules)
"""

from pathlib import Path

import yaml


def cleanup_interest_profiles():
    """Remove orphaned and legacy fields from interest profiles YAML."""

    # Paths
    sentinel_config = Path("config/profiles_interest.yml")

    if not sentinel_config.exists():
        print(f"❌ Interest profiles file not found: {sentinel_config}")
        return

    # Load profiles
    with open(sentinel_config, "r") as f:
        profiles = yaml.safe_load(f) or {}

    if not profiles:
        print("ℹ️  No interest profiles to clean")
        return

    # Track changes
    orphaned_removed = 0
    legacy_removed = 0

    # Clean each profile
    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue

        # Remove orphaned fields
        if "notify_always" in profile:
            del profile["notify_always"]
            orphaned_removed += 1
            print(f"  ✓ Removed 'notify_always' from profile {profile_id}")

        if "include_digest" in profile:
            del profile["include_digest"]
            orphaned_removed += 1
            print(f"  ✓ Removed 'include_digest' from profile {profile_id}")

        # Remove legacy duplicate fields (only if nested digest exists)
        if "digest" in profile:
            if "digest_mode" in profile:
                del profile["digest_mode"]
                legacy_removed += 1
                print(f"  ✓ Removed legacy 'digest_mode' from profile {profile_id}")

            if "digest_target_channel" in profile:
                del profile["digest_target_channel"]
                legacy_removed += 1
                print(
                    f"  ✓ Removed legacy 'digest_target_channel' from profile {profile_id}"
                )

            if "digest_schedules" in profile:
                del profile["digest_schedules"]
                legacy_removed += 1
                print(
                    f"  ✓ Removed legacy 'digest_schedules' from profile {profile_id}"
                )

    # Save back
    if orphaned_removed > 0 or legacy_removed > 0:
        with open(sentinel_config, "w") as f:
            yaml.safe_dump(
                profiles,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        print("\n✅ Cleanup complete:")
        print(f"   - Removed {orphaned_removed} orphaned field instances")
        print(f"   - Removed {legacy_removed} legacy duplicate field instances")
        print(f"   - Saved to: {sentinel_config}")
    else:
        print("\n✅ No cleanup needed - profiles already clean")


if __name__ == "__main__":
    print("=" * 60)
    print("Interest Profile Field Cleanup")
    print("=" * 60)
    print()
    cleanup_interest_profiles()
    print()
