#!/usr/bin/env python3
"""Convert demo profiles from flat digest fields to proper nested digest structure."""

import json
from pathlib import Path

# Process Alert profiles
alert_files = Path("demo/Alerts").glob("*.json")
for file_path in alert_files:
    print(f"Processing {file_path}...")
    with open(file_path, "r") as f:
        profile = json.load(f)

    # Extract flat fields (if they exist from previous run)
    digest_mode = profile.pop("digest_mode", "dm")
    digest_target_channel = profile.pop("digest_target_channel", "")
    digest_schedules = profile.pop("digest_schedules", [])
    digest_config = profile.pop("digest_config", None)

    # If digest_config exists, use its schedules
    if digest_config and "schedules" in digest_config:
        digest_schedules = digest_config["schedules"]

    # Create proper nested digest object
    profile["digest"] = {
        "mode": digest_mode,
        "target_channel": digest_target_channel or None,
        "schedules": digest_schedules,
    }

    # Write back with proper formatting
    with open(file_path, "w") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
        f.write("\n")  # Add trailing newline

    print(f"  ✅ Converted {file_path.name} to nested digest structure")

# Process Interest profiles
interest_files = Path("demo/Interests").glob("*.json")
for file_path in interest_files:
    print(f"Processing {file_path}...")
    with open(file_path, "r") as f:
        profile = json.load(f)

    # Remove deprecated flat fields
    profile.pop("digest_mode", None)
    profile.pop("digest_target_channel", None)
    profile.pop("digest_schedules", None)
    profile.pop("notify_always", None)
    profile.pop("include_digest", None)

    # Ensure proper nested digest object exists
    if "digest" not in profile:
        profile["digest"] = {
            "schedules": [],
        }

    # Update schedule_type -> schedule in nested schedules
    if "schedules" in profile.get("digest", {}):
        for sched in profile["digest"]["schedules"]:
            if "schedule_type" in sched and "schedule" not in sched:
                sched["schedule"] = sched.pop("schedule_type")

    # Write back with proper formatting
    with open(file_path, "w") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
        f.write("\n")  # Add trailing newline

    print(f"  ✅ Cleaned {file_path.name}")

print("\n✅ All demo profiles cleaned!")
