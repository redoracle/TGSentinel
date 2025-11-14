#!/usr/bin/env python3
"""
Verification script to test UI config loading from environment variables.
This script:
1. Calls the /api/config/current endpoint
2. Verifies all expected fields are present
3. Checks that environment variables are correctly loaded
"""

import json
import os
import sys
from urllib.request import Request, urlopen


def test_config_endpoint():
    """Test the /api/config/current endpoint."""
    url = "http://localhost:5001/api/config/current"

    print(f"Testing endpoint: {url}")
    print("-" * 60)

    try:
        req = Request(url)
        with urlopen(req, timeout=5) as response:
            if response.status != 200:
                print(f"❌ FAIL: HTTP {response.status}")
                return False

            data = json.loads(response.read())

            # Check telegram config
            print("\n✓ Telegram Configuration:")
            telegram = data.get("telegram", {})
            print(f"  API ID: {telegram.get('api_id', 'MISSING')}")
            print(
                f"  API Hash: {'*' * len(telegram.get('api_hash', '')) if telegram.get('api_hash') else 'MISSING'}"
            )
            print(f"  Phone: {telegram.get('phone_number', 'MISSING')}")
            print(f"  Session: {telegram.get('session', 'MISSING')}")

            # Check alerts config
            print("\n✓ Alerts Configuration:")
            alerts = data.get("alerts", {})
            print(f"  Mode: {alerts.get('mode', 'MISSING')}")
            print(f"  Channel: {alerts.get('target_channel', 'MISSING')}")

            # Check digest config
            print("\n✓ Digest Configuration:")
            digest = data.get("digest", {})
            print(f"  Hourly: {digest.get('hourly', 'MISSING')}")
            print(f"  Daily: {digest.get('daily', 'MISSING')}")
            print(f"  Top N: {digest.get('top_n', 'MISSING')}")

            # Check redis config
            print("\n✓ Redis Configuration:")
            redis = data.get("redis", {})
            print(f"  Host: {redis.get('host', 'MISSING')}")
            print(f"  Port: {redis.get('port', 'MISSING')}")

            # Check semantic config
            print("\n✓ Semantic Configuration:")
            semantic = data.get("semantic", {})
            print(f"  Model: {semantic.get('embeddings_model', 'MISSING')}")
            print(f"  Threshold: {semantic.get('similarity_threshold', 'MISSING')}")

            # Check database
            print(f"\n✓ Database: {data.get('database_uri', 'MISSING')}")

            # Verify critical fields are populated from env vars
            print("\n" + "=" * 60)
            print("Verification:")

            checks = [
                (
                    "TG_API_ID populated",
                    telegram.get("api_id") == os.getenv("TG_API_ID"),
                ),
                (
                    "TG_API_HASH populated",
                    telegram.get("api_hash") == os.getenv("TG_API_HASH"),
                ),
                (
                    "TG_PHONE populated",
                    telegram.get("phone_number") == os.getenv("TG_PHONE"),
                ),
                ("ALERT_MODE populated", alerts.get("mode") == os.getenv("ALERT_MODE")),
                (
                    "ALERT_CHANNEL populated",
                    alerts.get("target_channel") == os.getenv("ALERT_CHANNEL"),
                ),
                ("REDIS_HOST populated", redis.get("host") == os.getenv("REDIS_HOST")),
                (
                    "EMBEDDINGS_MODEL populated",
                    semantic.get("embeddings_model") == os.getenv("EMBEDDINGS_MODEL"),
                ),
            ]

            all_passed = True
            for check_name, result in checks:
                status = "✓" if result else "✗"
                print(f"  {status} {check_name}")
                if not result:
                    all_passed = False

            print("=" * 60)
            if all_passed:
                print("\n✅ All checks PASSED!")
                return True
            else:
                print("\n⚠️  Some checks FAILED - see above")
                return False

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


if __name__ == "__main__":
    success = test_config_endpoint()
    sys.exit(0 if success else 1)
