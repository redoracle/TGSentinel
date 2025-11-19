#!/usr/bin/env python3
"""
Reorganize tests into proper directory structure following TESTS.instructions.md.

This script:
1. Categorizes tests into unit/integration/contract/e2e
2. Moves them to appropriate directories
3. Adds pytest markers
4. Creates __init__.py files
"""

import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Test categorization based on TESTS.instructions.md analysis
CATEGORIZATION = {
    "unit/tgsentinel": [
        "test_heuristics.py",
        "test_config.py",
        "test_config_priority.py",
        "test_store.py",
        "test_semantic.py",
        "test_metrics.py",
        "test_notifier.py",
        "test_digest.py",
    ],
    "unit/ui": [
        "test_ui_config.py",
        "test_ui_config_extras.py",
        "test_ui_channel_delete.py",
    ],
    "integration": [
        "test_app_integration.py",
        "test_client.py",
        "test_worker.py",
        "test_dashboard_data.py",
        "test_dashboard_live.py",
        "test_identity_caching.py",
        "test_participant_info.py",
        "test_telegram_users_api.py",
        "test_ui_channels.py",
        "test_ui_alerts_endpoints.py",
        "test_ui_login_endpoints.py",
        "test_performance_fixes.py",
    ],
    "contracts": [
        "test_ui_endpoints.py",
        "test_ui_analytics_layout.py",
        "test_ui_config_integration.py",
        "test_ui_missing_endpoints.py",
        "test_dashboard_controls.py",
    ],
    "e2e": [
        "test_console_e2e.py",
    ],
}

# Marker to add to each category
MARKERS = {
    "unit/tgsentinel": "unit",
    "unit/ui": "unit",
    "integration": "integration",
    "contracts": "contract",
    "e2e": "e2e",
}


def add_marker_to_file(file_path: Path, marker: str) -> None:
    """Add pytest marker to all test functions and classes in a file."""
    content = file_path.read_text()

    # Check if file already has markers
    if f"@pytest.mark.{marker}" in content:
        print(f"  ⚠️  {file_path.name} already has {marker} markers, skipping")
        return

    lines = content.split("\n")
    new_lines = []

    for i, line in enumerate(lines):
        # Add marker before test classes
        if line.startswith("class Test"):
            new_lines.append(f"@pytest.mark.{marker}")
        # Add marker before standalone test functions (not in classes)
        elif line.startswith("def test_") or line.startswith("async def test_"):
            # Check if this is inside a class (previous non-empty line is indented or is a class)
            is_in_class = False
            for j in range(i - 1, -1, -1):
                prev_line = lines[j].strip()
                if not prev_line or prev_line.startswith("#"):
                    continue
                if lines[j].startswith("    "):  # Indented, likely in class
                    is_in_class = True
                break

            if not is_in_class:
                new_lines.append(f"@pytest.mark.{marker}")

        new_lines.append(line)

    file_path.write_text("\n".join(new_lines))
    print(f"  ✅ Added {marker} markers to {file_path.name}")


def create_init_files():
    """Create __init__.py files in all test directories."""
    for category in CATEGORIZATION.keys():
        init_file = TESTS_DIR / category / "__init__.py"
        if not init_file.exists():
            init_file.write_text('"""Test package."""\n')
            print(f"✅ Created {init_file.relative_to(REPO_ROOT)}")


def main():
    print("🔄 Reorganizing tests following TESTS.instructions.md...\n")

    # Create __init__.py files
    create_init_files()
    print()

    # Move and mark files
    for category, files in CATEGORIZATION.items():
        dest_dir = TESTS_DIR / category
        marker = MARKERS[category]

        print(f"📁 Processing {category}/ (marker: @pytest.mark.{marker})")

        for filename in files:
            src = TESTS_DIR / filename
            dest = dest_dir / filename

            if not src.exists():
                print(f"  ⚠️  {filename} not found, skipping")
                continue

            if dest.exists():
                print(f"  ⚠️  {filename} already in {category}/, adding markers only")
                add_marker_to_file(dest, marker)
            else:
                # Move file
                shutil.move(str(src), str(dest))
                print(f"  ✅ Moved {filename} to {category}/")

                # Add markers
                add_marker_to_file(dest, marker)

        print()

    print("✅ Test reorganization complete!")
    print("\nNext steps:")
    print("1. Review moved files and markers")
    print("2. Run: pytest -m unit (should be fast, no network/Redis)")
    print("3. Run: pytest -m integration (needs Redis)")
    print("4. Run: pytest -m contract (API contract tests)")
    print("5. Run: pytest -m e2e (full stack tests)")


if __name__ == "__main__":
    main()
