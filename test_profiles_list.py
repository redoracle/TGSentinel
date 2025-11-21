#!/usr/bin/env python3
"""
Quick test to verify the Interest Profiles list endpoint structure.
This simulates what the UI JavaScript does when loading profiles.
"""
import requests
import json


def test_sentinel_api():
    """Test Sentinel API directly"""
    print("=" * 60)
    print("1. Testing Sentinel API (http://localhost:8080/api/profiles/interest)")
    print("=" * 60)

    response = requests.get("http://localhost:8080/api/profiles/interest")
    data = response.json()

    print(f"Status: {data.get('status')}")
    profiles_dict = data.get("data", {})
    print(f"Profile count: {len(profiles_dict)}")

    if profiles_dict:
        first_key = list(profiles_dict.keys())[0]
        first_profile = profiles_dict[first_key]
        print(
            f"\nFirst profile key (dict key): {first_key!r} (type: {type(first_key).__name__})"
        )
        print(
            f"First profile.id field: {first_profile.get('id')!r} (type: {type(first_profile.get('id')).__name__})"
        )
        print(f"First profile name: {first_profile.get('name')}")
        print(f"\nProfile has these fields: {', '.join(first_profile.keys())}")

    return profiles_dict


def test_ui_list_endpoint():
    """Test UI list endpoint (requires auth - will likely fail without session)"""
    print("\n" + "=" * 60)
    print(
        "2. Testing UI List Endpoint (http://localhost:5001/api/profiles/interest/list)"
    )
    print("=" * 60)

    try:
        response = requests.get("http://localhost:5001/api/profiles/interest/list")

        if response.status_code == 200:
            data = response.json()
            print(f"Status: {data.get('status')}")
            profiles_list = data.get("profiles", [])
            print(f"Profile count: {len(profiles_list)}")
            print(f"Profiles is a: {type(profiles_list).__name__}")

            if profiles_list:
                first_profile = profiles_list[0]
                print(
                    f"\nFirst profile.id: {first_profile.get('id')!r} (type: {type(first_profile.get('id')).__name__})"
                )
                print(f"First profile name: {first_profile.get('name')}")

            return profiles_list
        else:
            print(f"Response status: {response.status_code}")
            print(f"Response: {response.text[:200]}")

    except Exception as exc:
        print(f"Error: {exc}")

    return None


def simulate_ui_conversion(sentinel_profiles_dict):
    """Simulate what the UI endpoint does"""
    print("\n" + "=" * 60)
    print("3. Simulating UI Conversion Logic")
    print("=" * 60)

    # OLD WAY (was causing issues):
    old_way = [
        {**profile, "id": profile_id}
        for profile_id, profile in sentinel_profiles_dict.items()
    ]

    # NEW WAY (current fix):
    new_way = [profile for profile_id, profile in sentinel_profiles_dict.items()]

    print("\nOLD WAY (broken):")
    if old_way:
        first = old_way[0]
        print(
            f"  First profile has {len([k for k, v in first.items() if k == 'id'])} 'id' field(s)"
        )
        print(
            f"  Profile dict: {json.dumps({k: v for k, v in first.items() if k in ['id', 'name']}, indent=2)}"
        )

    print("\nNEW WAY (fixed):")
    if new_way:
        first = new_way[0]
        print(
            f"  First profile has {len([k for k, v in first.items() if k == 'id'])} 'id' field(s)"
        )
        print(
            f"  Profile dict: {json.dumps({k: v for k, v in first.items() if k in ['id', 'name']}, indent=2)}"
        )

    return new_way


if __name__ == "__main__":
    print("\n🔍 Testing Interest Profiles List Endpoint Fix\n")

    # Test Sentinel API
    sentinel_profiles = test_sentinel_api()

    # Test UI endpoint (may require auth)
    test_ui_list_endpoint()

    # Simulate conversion
    if sentinel_profiles:
        simulate_ui_conversion(sentinel_profiles)

    print("\n" + "=" * 60)
    print("✅ Test Complete")
    print("=" * 60)
    print(
        "\nIf you see 'id' field only ONCE in the NEW WAY section, the fix is correct!"
    )
    print("The UI JavaScript expects an array where each profile has an 'id' field.\n")
