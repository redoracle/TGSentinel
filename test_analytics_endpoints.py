#!/usr/bin/env python3
"""
Test script for new Analytics endpoints (Phase 2)

Tests the following endpoints:
- /api/analytics/endpoints - API health monitoring
- /api/analytics/prometheus - Prometheus metrics parsing
- /api/analytics/system-health - Overall system health score
"""

import json
import os
import sys
from typing import Dict, Any

import requests
from dotenv import load_dotenv  # type: ignore[import-not-found]

# Load environment variables
load_dotenv()

UI_BASE_URL = os.getenv("UI_BASE_URL", "http://localhost:5001")
UI_LOCK_PASSWORD = os.getenv("UI_LOCK_PASSWORD", "changeme")


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f" {title}")
    print("=" * 60)


def unlock_ui(session: requests.Session) -> bool:
    """Unlock the UI using the configured password."""
    try:
        response = session.post(
            f"{UI_BASE_URL}/api/ui/lock",
            json={"action": "unlock", "password": UI_LOCK_PASSWORD},
            timeout=5,
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                print(f"🔓 UI unlocked: locked={data.get('locked', True)}")
                return True

        print(f"❌ Failed to unlock UI: {response.status_code} - {response.text}")
        return False

    except Exception as e:
        print(f"❌ Error unlocking UI: {e}")
        return False


def test_endpoints_health(session: requests.Session):
    """Test the /api/analytics/endpoints endpoint."""
    print_section("Testing API Endpoints Health Monitor")

    try:
        response = session.get(f"{UI_BASE_URL}/api/analytics/endpoints", timeout=10)

        print(f"Response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            print(f"\n📊 Summary:")
            summary = data.get("summary", {})
            print(f"  Total endpoints: {summary.get('total_endpoints')}")
            print(f"  Online endpoints: {summary.get('online_endpoints')}")
            print(f"  Online percentage: {summary.get('online_percentage')}%")

            print(f"\n🔍 Endpoint Groups:")
            for group in data.get("groups", []):
                status_emoji = "✅" if group.get("online") else "❌"
                print(
                    f"\n  {status_emoji} {group.get('group')} (Port {group.get('port')})"
                )
                print(f"     Avg Latency: {group.get('avg_latency_ms')}ms")

                for endpoint in group.get("endpoints", []):
                    ep_status = "✅" if endpoint.get("online") else "❌"
                    path = endpoint.get("path")
                    latency = endpoint.get("latency_ms", 0)
                    print(f"     {ep_status} {path} - {latency}ms")

                    if "error" in endpoint:
                        print(f"        Error: {endpoint['error']}")

            print(f"\n✅ Endpoint health check completed successfully")
            return True
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Exception during endpoints test: {e}")
        return False


def test_prometheus_metrics(session: requests.Session):
    """Test the /api/analytics/prometheus endpoint."""
    print_section("Testing Prometheus Metrics Parser")

    try:
        response = session.get(f"{UI_BASE_URL}/api/analytics/prometheus", timeout=10)

        print(f"Response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            print(f"\n📈 Prometheus Metrics:")
            print(f"  Raw metrics count: {data.get('raw_count')}")
            print(f"  Timestamp: {data.get('timestamp')}")

            metrics = data.get("metrics", {})
            if metrics:
                print(f"\n  Parsed Metrics:")
                for metric_name, metric_data in metrics.items():
                    value = metric_data.get("value", 0)
                    metric_type = metric_data.get("type", "unknown")

                    # Format value based on type
                    if "latency" in metric_name or "seconds" in metric_name:
                        formatted_value = f"{value:.3f}s"
                    elif isinstance(value, float):
                        formatted_value = f"{value:.2f}"
                    else:
                        formatted_value = str(value)

                    print(f"    • {metric_name}: {formatted_value} ({metric_type})")

                    # Show details for complex metrics
                    if "details" in metric_data:
                        print(f"      Details: {metric_data['count']} entries")
            else:
                print(
                    "  No metrics parsed (Sentinel may not be exposing Prometheus metrics)"
                )

            print(f"\n✅ Prometheus metrics fetch completed successfully")
            return True
        elif response.status_code == 503:
            print(f"⚠️  Warning: Sentinel metrics endpoint unavailable")
            print(f"   This is expected if Prometheus metrics are not enabled")
            return True
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Exception during Prometheus test: {e}")
        return False


def test_system_health(session: requests.Session):
    """Test the /api/analytics/system-health endpoint."""
    print_section("Testing System Health Score Calculator")

    try:
        response = session.get(f"{UI_BASE_URL}/api/analytics/system-health", timeout=15)

        print(f"Response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            score = data.get("health_score", 0)
            grade = data.get("grade", "unknown")
            color = data.get("color", "unknown")

            # Health score emoji
            if score >= 90:
                score_emoji = "🎉"
            elif score >= 75:
                score_emoji = "✅"
            elif score >= 60:
                score_emoji = "⚠️"
            else:
                score_emoji = "❌"

            print(f"\n{score_emoji} Overall System Health Score: {score}/100")
            print(f"   Grade: {grade.upper()}")
            print(f"   Status: {color}")

            print(f"\n📊 Component Scores:")
            for component_name, component_data in data.get("components", {}).items():
                comp_score = component_data.get("score", 0)
                max_score = component_data.get("max_score", 0)
                percentage = (comp_score / max_score * 100) if max_score > 0 else 0

                if percentage >= 80:
                    comp_emoji = "✅"
                elif percentage >= 60:
                    comp_emoji = "⚠️"
                else:
                    comp_emoji = "❌"

                print(
                    f"\n  {comp_emoji} {component_name.replace('_', ' ').title()}: {comp_score}/{max_score} ({percentage:.0f}%)"
                )

                details = component_data.get("details", {})
                if details:
                    for key, value in details.items():
                        if key != "error":
                            print(f"     • {key}: {value}")
                        else:
                            print(f"     ⚠️  Error: {value}")

            recommendations = data.get("recommendations", [])
            if recommendations:
                print(f"\n💡 Recommendations:")
                for rec in recommendations:
                    print(f"  • {rec}")
            else:
                print(f"\n✅ No recommendations - system is healthy")

            print(f"\n✅ System health calculation completed successfully")
            return True
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Exception during system health test: {e}")
        return False


def main():
    """Main test runner."""
    print("=" * 60)
    print("  TG Sentinel Analytics Endpoints Test Suite (Phase 2)")
    print("=" * 60)

    # Create session to maintain cookies
    session = requests.Session()

    # Unlock UI
    print_section("Authentication")
    if not unlock_ui(session):
        print("\n❌ Failed to authenticate. Exiting.")
        sys.exit(1)

    # Run tests
    results = {
        "endpoints": test_endpoints_health(session),
        "prometheus": test_prometheus_metrics(session),
        "system_health": test_system_health(session),
    }

    # Summary
    print_section("Test Summary")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {test_name}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{total} tests passed")
    print("=" * 60)

    if passed == total:
        print("\n✅ All tests completed successfully!")
        sys.exit(0)
    else:
        print(f"\n❌ {total - passed} test(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
