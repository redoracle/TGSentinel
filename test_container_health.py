#!/usr/bin/env python3
"""
Test script for container health monitoring endpoint with authentication.
Demonstrates how to:
1. Unlock UI with password
2. Authenticate with session
3. Access protected endpoints like /api/analytics/containers
"""

import os
import sys
import json
import requests

# Configuration
UI_BASE_URL = os.getenv("UI_BASE_URL", "http://localhost:5001")
UI_LOCK_PASSWORD = os.getenv("UI_LOCK_PASSWORD", "changeme")


def unlock_ui(session):
    """Unlock the UI with password."""
    print("🔓 Unlocking UI...")
    unlock_url = f"{UI_BASE_URL}/api/ui/lock"
    response = session.post(
        unlock_url, json={"action": "unlock", "password": UI_LOCK_PASSWORD}
    )

    if response.status_code == 200:
        data = response.json()
        if data.get("status") == "ok":
            print(f"✅ UI unlocked: locked={data.get('locked', False)}")
            return True
        else:
            print(f"❌ Failed to unlock UI: {data.get('message', 'Unknown error')}")
            return False
    elif response.status_code == 403:
        print(f"❌ Invalid password")
        return False
    else:
        print(f"❌ Unlock request failed: {response.status_code} - {response.text}")
        return False


def check_worker_status(session):
    """Check if worker is authorized."""
    print("\n🔍 Checking worker status...")
    status_url = f"{UI_BASE_URL}/api/worker/status"
    response = session.get(status_url)

    if response.status_code == 200:
        data = response.json()
        authorized = data.get("authorized", False)
        status = data.get("status", "unknown")
        print(f"Worker authorized: {authorized}, status: {status}")
        return authorized
    else:
        print(f"❌ Worker status check failed: {response.status_code}")
        return False


def get_container_health(session):
    """Get container health information."""
    print("\n🐳 Fetching container health...")
    containers_url = f"{UI_BASE_URL}/api/analytics/containers"
    response = session.get(containers_url)

    print(f"Response status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        if data.get("status") == "ok":
            containers = data.get("containers", [])
            print(f"\n✅ Found {len(containers)} containers:")
            print("-" * 80)

            for container in containers:
                name = container.get("short_name", container.get("name", "Unknown"))
                status = container.get("status", "unknown")
                running = "✅ Running" if container.get("running") else "❌ Stopped"
                uptime = container.get("uptime_display", "N/A")
                cpu = container.get("cpu_percent", 0)
                mem_mb = container.get("memory_mb", 0)
                mem_pct = container.get("memory_percent", 0)
                restarts = container.get("restarts", 0)

                print(f"\n📦 {name.upper()}")
                print(f"   Status:   {running} ({status})")
                print(f"   Uptime:   {uptime}")
                print(f"   CPU:      {cpu:.1f}%")
                print(f"   Memory:   {mem_mb:.0f} MB ({mem_pct:.1f}%)")
                print(f"   Restarts: {restarts}")

            print("\n" + "-" * 80)
            return True
        else:
            print(f"❌ Error: {data.get('message', 'Unknown error')}")
            return False
    elif response.status_code == 401:
        print("❌ Authentication required - please upload a session first")
        return False
    elif response.status_code == 423:
        print("❌ UI is locked - unlock first")
        return False
    elif response.status_code == 503:
        print("❌ Service unavailable - Docker daemon may not be accessible")
        print(f"   Response: {response.text}")
        return False
    else:
        print(f"❌ Request failed: {response.status_code}")
        print(f"   Response: {response.text}")
        return False


def main():
    """Main test flow."""
    print("=" * 80)
    print("TG Sentinel - Container Health Monitoring Test")
    print("=" * 80)

    # Create session to maintain cookies
    session = requests.Session()

    # Step 1: Unlock UI
    if not unlock_ui(session):
        print("\n⚠️  UI unlock failed, but continuing (might be already unlocked)...")

    # Step 2: Check worker status
    worker_authorized = check_worker_status(session)

    if not worker_authorized:
        print("\n⚠️  Worker not authorized. You may need to:")
        print("   1. Upload a Telegram session file via the UI")
        print("   2. Complete the authentication flow")
        print("\nNote: Container health endpoint requires authentication.")

    # Step 3: Get container health (will work if authenticated OR show appropriate error)
    success = get_container_health(session)

    print("\n" + "=" * 80)
    if success:
        print("✅ Test completed successfully!")
        return 0
    else:
        print("❌ Test completed with errors")
        print("\nTroubleshooting:")
        print(
            "1. Ensure Docker socket is mounted: /var/run/docker.sock:/var/run/docker.sock:ro"
        )
        print("2. Ensure UI container has Docker SDK: pip install docker>=7.0.0")
        print("3. Upload a valid Telegram session if not authenticated")
        print("4. Check UI logs: docker logs tgsentinel-ui-1")
        return 1


if __name__ == "__main__":
    sys.exit(main())
