#!/usr/bin/env python3
"""Migration script to convert old-style keywords to two-layer profiles.

Usage:
    python tools/migrate_profiles.py --config config/tgsentinel.yml --dry-run
    python tools/migrate_profiles.py --config config/tgsentinel.yml --apply

This script:
1. Analyzes existing keywords in all channels/users
2. Groups them into logical profiles (security, releases, opportunities, etc.)
3. Generates profiles.yml with global profiles
4. Updates tgsentinel.yml to bind profiles instead of duplicating keywords
5. Creates backups before making changes
"""

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tgsentinel.config import ChannelRule, MonitoredUser


class ProfileMigrator:
    """Migrates old-style keywords to two-layer profiles architecture."""

    def __init__(self, config_path: str, dry_run: bool = True):
        self.config_path = config_path
        self.dry_run = dry_run
        self.config_data = None
        self.channels = []
        self.users = []
        self.profile_keywords: Dict[str, Set[str]] = defaultdict(set)

    def load_config(self):
        """Load existing configuration."""
        print(f"📖 Loading config from {self.config_path}")
        with open(self.config_path, "r") as f:
            self.config_data = yaml.safe_load(f)

        self.channels = [ChannelRule(**c) for c in self.config_data.get("channels", [])]
        self.users = [
            MonitoredUser(**u) for c in self.config_data.get("monitored_users", [])
        ]

        print(f"   Found {len(self.channels)} channels, {len(self.users)} users")

    def analyze_keywords(self):
        """Analyze all keywords across channels and group them."""
        print("\n🔍 Analyzing keywords across all entities...")

        categories = [
            "keywords",
            "action_keywords",
            "decision_keywords",
            "urgency_keywords",
            "importance_keywords",
            "release_keywords",
            "security_keywords",
            "risk_keywords",
            "opportunity_keywords",
        ]

        # Collect all keywords by category
        category_keywords: Dict[str, Set[str]] = defaultdict(set)
        for channel in self.channels:
            for category in categories:
                keywords = getattr(channel, category, [])
                if keywords:
                    category_keywords[category].update(keywords)

        # Build profiles based on keyword patterns
        self._build_security_profile(category_keywords)
        self._build_releases_profile(category_keywords)
        self._build_opportunities_profile(category_keywords)
        self._build_governance_profile(category_keywords)
        self._build_technical_profile(category_keywords)
        self._build_risk_profile(category_keywords)

        print(f"\n   Created {len(self.profile_keywords)} profiles:")
        for profile_id, keywords in self.profile_keywords.items():
            print(f"      • {profile_id}: {len(keywords)} keywords")

    def _build_security_profile(self, category_keywords: Dict[str, Set[str]]):
        """Build security profile from security/urgency keywords."""
        profile = "security"
        self.profile_keywords[profile].update(
            category_keywords.get("security_keywords", set())
        )
        # Add common security terms
        security_terms = {
            "vulnerability",
            "exploit",
            "CVE",
            "patch",
            "breach",
            "attack",
        }
        self.profile_keywords[profile].update(
            kw
            for kw in category_keywords.get("keywords", set())
            if any(term in kw.lower() for term in security_terms)
        )

    def _build_releases_profile(self, category_keywords: Dict[str, Set[str]]):
        """Build releases profile."""
        profile = "releases"
        self.profile_keywords[profile].update(
            category_keywords.get("release_keywords", set())
        )
        release_terms = {"release", "update", "version", "changelog", "upgrade"}
        self.profile_keywords[profile].update(
            kw
            for kw in category_keywords.get("keywords", set())
            if any(term in kw.lower() for term in release_terms)
        )

    def _build_opportunities_profile(self, category_keywords: Dict[str, Set[str]]):
        """Build opportunities profile."""
        profile = "opportunities"
        self.profile_keywords[profile].update(
            category_keywords.get("opportunity_keywords", set())
        )
        opportunity_terms = {"airdrop", "grant", "funding", "opportunity", "token"}
        self.profile_keywords[profile].update(
            kw
            for kw in category_keywords.get("keywords", set())
            if any(term in kw.lower() for term in opportunity_terms)
        )

    def _build_governance_profile(self, category_keywords: Dict[str, Set[str]]):
        """Build governance profile."""
        profile = "governance"
        self.profile_keywords[profile].update(
            category_keywords.get("decision_keywords", set())
        )
        governance_terms = {"proposal", "vote", "governance", "ballot"}
        self.profile_keywords[profile].update(
            kw
            for kw in category_keywords.get("keywords", set())
            if any(term in kw.lower() for term in governance_terms)
        )

    def _build_technical_profile(self, category_keywords: Dict[str, Set[str]]):
        """Build technical profile."""
        profile = "technical"
        tech_terms = {"mainnet", "testnet", "hard fork", "API", "SDK", "upgrade"}
        self.profile_keywords[profile].update(
            kw
            for kw in category_keywords.get("keywords", set())
            if any(term in kw.lower() for term in tech_terms)
        )

    def _build_risk_profile(self, category_keywords: Dict[str, Set[str]]):
        """Build risk profile."""
        profile = "risk"
        self.profile_keywords[profile].update(
            category_keywords.get("risk_keywords", set())
        )
        risk_terms = {"incident", "outage", "downtime", "issue", "problem"}
        self.profile_keywords[profile].update(
            kw
            for kw in category_keywords.get("keywords", set())
            if any(term in kw.lower() for term in risk_terms)
        )

    def generate_profiles_yml(self) -> str:
        """Generate profiles.yml content."""
        print("\n📝 Generating profiles.yml...")

        profiles_dict = {}
        for profile_id, keywords in self.profile_keywords.items():
            if not keywords:
                continue

            # Categorize keywords into appropriate lists
            profile_data = {
                "name": profile_id.replace("_", " ").title(),
                "keywords": sorted(list(keywords)),
                "detect_codes": True,
                "detect_documents": True,
                "prioritize_pinned": True,
            }

            # Add default scoring weights
            scoring_weights = {
                "keywords": 0.8,
                "vip": 1.0,
                "reactions": 0.5,
                "replies": 0.5,
            }

            if profile_id == "security":
                scoring_weights.update({"security": 1.5, "urgency": 1.8})
            elif profile_id == "releases":
                scoring_weights.update({"release": 1.0})
            elif profile_id == "opportunities":
                scoring_weights.update({"opportunity": 0.8, "decision": 1.0})
            elif profile_id == "governance":
                scoring_weights.update({"decision": 1.2, "action": 1.0})
            elif profile_id == "risk":
                scoring_weights.update({"risk": 1.5, "urgency": 1.8})

            profile_data["scoring_weights"] = scoring_weights
            profiles_dict[profile_id] = profile_data

        profiles_yml = {"profiles": profiles_dict}
        return yaml.dump(profiles_yml, sort_keys=False, default_flow_style=False)

    def update_channels(self):
        """Update channels to bind profiles instead of listing keywords."""
        print("\n🔄 Updating channels to use profile bindings...")

        for channel_data in self.config_data.get("channels", []):
            # Determine which profiles this channel should bind
            profiles_to_bind = []

            # Check if channel has any keywords in each profile
            for profile_id, profile_keywords in self.profile_keywords.items():
                channel_keywords = set()
                for category in [
                    "keywords",
                    "action_keywords",
                    "decision_keywords",
                    "urgency_keywords",
                    "security_keywords",
                    "release_keywords",
                    "risk_keywords",
                    "opportunity_keywords",
                ]:
                    channel_keywords.update(channel_data.get(category, []))

                # If channel has at least 2 keywords from this profile, bind it
                overlap = len(channel_keywords & profile_keywords)
                if overlap >= 2:
                    profiles_to_bind.append(profile_id)

            # Add profile bindings
            if profiles_to_bind:
                channel_data["profiles"] = profiles_to_bind
                print(f"   • {channel_data['name']}: bound {profiles_to_bind}")

            # Clear old keyword fields (keep as comments for reference)
            # NOTE: Don't delete yet - keep for backward compatibility
            # for category in ["keywords", "action_keywords", "decision_keywords", ...]:
            #     if category in channel_data:
            #         channel_data[category] = []

    def create_backups(self):
        """Create backups of config files."""
        if self.dry_run:
            print("\n💾 [DRY RUN] Would create backups:")
            print(f"   • {self.config_path} → {self.config_path}.backup")
            return

        print("\n💾 Creating backups...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(self.config_path).parent / "backups"
        backup_dir.mkdir(exist_ok=True)

        backup_path = backup_dir / f"tgsentinel_{timestamp}.yml"
        shutil.copy2(self.config_path, backup_path)
        print(f"   ✓ Created backup: {backup_path}")

    def save_changes(self, profiles_yml_content: str):
        """Save updated configuration files."""
        profiles_path = Path(self.config_path).parent / "profiles.yml"

        if self.dry_run:
            print("\n📄 [DRY RUN] Would write files:")
            print(f"   • {profiles_path} ({len(profiles_yml_content)} bytes)")
            print(f"   • {self.config_path} (updated with profile bindings)")
            return

        print("\n💾 Saving changes...")

        # Write profiles.yml
        with open(profiles_path, "w") as f:
            f.write(profiles_yml_content)
        print(f"   ✓ Created {profiles_path}")

        # Write updated tgsentinel.yml
        with open(self.config_path, "w") as f:
            yaml.dump(self.config_data, f, sort_keys=False, default_flow_style=False)
        print(f"   ✓ Updated {self.config_path}")

    def run(self):
        """Execute migration."""
        print("=" * 80)
        print("TG Sentinel - Profile Migration")
        print("=" * 80)
        print(f"Mode: {'DRY RUN' if self.dry_run else 'APPLY CHANGES'}")
        print()

        self.load_config()
        self.analyze_keywords()
        profiles_yml = self.generate_profiles_yml()
        self.update_channels()
        self.create_backups()
        self.save_changes(profiles_yml)

        print("\n" + "=" * 80)
        if self.dry_run:
            print("✓ Dry run complete. Review changes above.")
            print("  Run with --apply to make actual changes.")
        else:
            print("✓ Migration complete!")
            print("  Backups created in config/backups/")
            print("  Review profiles.yml and restart services.")
        print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate keywords to two-layer profiles"
    )
    parser.add_argument(
        "--config",
        default="config/tgsentinel.yml",
        help="Path to tgsentinel.yml (default: config/tgsentinel.yml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would change without modifying files (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (creates backups first)",
    )

    args = parser.parse_args()

    migrator = ProfileMigrator(
        config_path=args.config,
        dry_run=not args.apply,  # dry_run=False when --apply is set
    )

    try:
        migrator.run()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
