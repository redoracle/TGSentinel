"""Unit tests for global profile API endpoints.

Tests the new /api/profiles/global/* endpoints for managing global profiles
in the two-layer architecture.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


@pytest.fixture
def mock_profile_service():
    """Mock ProfileService for API testing."""
    service = Mock()
    service.list_global_profiles.return_value = [
        {
            "id": "security",
            "name": "Security & Vulnerabilities",
            "keywords": ["vulnerability", "CVE", "exploit"],
            "security_keywords": ["critical", "zero-day"],
            "scoring_weights": {"security": 1.5, "urgency": 1.8},
        },
        {
            "id": "releases",
            "name": "Releases & Updates",
            "keywords": ["release", "version", "update"],
            "release_keywords": ["beta", "stable"],
            "scoring_weights": {"release": 1.0},
        },
    ]
    service.get_global_profile.return_value = {
        "id": "security",
        "name": "Security & Vulnerabilities",
        "keywords": ["vulnerability", "CVE", "exploit"],
        "scoring_weights": {"security": 1.5},
    }
    service.validate_global_profile.return_value = {"valid": True, "errors": []}
    service.create_global_profile.return_value = True
    service.update_global_profile.return_value = True
    service.delete_global_profile.return_value = True
    service.get_profile_usage.return_value = {"channels": [], "users": []}
    return service


@pytest.fixture
def app_with_profiles(mock_profile_service):
    """Flask app with profiles routes initialized."""
    from flask import Flask
    from ui.routes.profiles import profiles_bp, init_profiles_routes

    app = Flask(__name__)
    app.config["TESTING"] = True

    # Initialize with mocks
    init_profiles_routes(
        config=Mock(),
        engine=Mock(),
        query_one=Mock(),
        query_all=Mock(),
        profile_service=mock_profile_service,
    )

    app.register_blueprint(profiles_bp)
    return app


def test_list_global_profiles(app_with_profiles, mock_profile_service):
    """Test GET /api/profiles/global/list returns all profiles."""
    client = app_with_profiles.test_client()

    response = client.get("/api/profiles/global/list")
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert "profiles" in data
    assert len(data["profiles"]) == 2
    assert data["profiles"][0]["id"] == "security"
    assert data["profiles"][1]["id"] == "releases"

    mock_profile_service.list_global_profiles.assert_called_once()


def test_get_global_profile(app_with_profiles, mock_profile_service):
    """Test GET /api/profiles/global/<id> returns specific profile."""
    client = app_with_profiles.test_client()

    response = client.get("/api/profiles/global/security")
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["profile"]["id"] == "security"
    assert data["profile"]["name"] == "Security & Vulnerabilities"

    mock_profile_service.get_global_profile.assert_called_once_with("security")


def test_get_global_profile_not_found(app_with_profiles, mock_profile_service):
    """Test GET /api/profiles/global/<id> returns 404 for missing profile."""
    mock_profile_service.get_global_profile.return_value = None
    client = app_with_profiles.test_client()

    response = client.get("/api/profiles/global/nonexistent")
    assert response.status_code == 404

    data = response.get_json()
    assert data["status"] == "error"
    assert "not found" in data["message"].lower()


def test_create_global_profile(app_with_profiles, mock_profile_service):
    """Test POST /api/profiles/global/create creates new profile."""
    mock_profile_service.get_global_profile.return_value = None  # Doesn't exist yet
    client = app_with_profiles.test_client()

    payload = {
        "id": "custom",
        "profile": {
            "name": "Custom Profile",
            "keywords": ["test", "demo"],
            "scoring_weights": {"keywords": 1.0},
        },
    }

    response = client.post(
        "/api/profiles/global/create",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 201

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["id"] == "custom"

    mock_profile_service.create_global_profile.assert_called_once_with(
        "custom", payload["profile"]
    )


def test_create_global_profile_validation_error(
    app_with_profiles, mock_profile_service
):
    """Test POST /api/profiles/global/create rejects invalid profile."""
    mock_profile_service.validate_global_profile.return_value = {
        "valid": False,
        "errors": ["Profile name is required"],
    }
    client = app_with_profiles.test_client()

    payload = {"id": "invalid", "profile": {"keywords": ["test"]}}

    response = client.post(
        "/api/profiles/global/create",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 400

    data = response.get_json()
    assert data["status"] == "error"
    assert "validation failed" in data["message"].lower()
    assert len(data["errors"]) > 0


def test_create_global_profile_conflict(app_with_profiles, mock_profile_service):
    """Test POST /api/profiles/global/create returns 409 if profile exists."""
    mock_profile_service.get_global_profile.return_value = {
        "id": "security",
        "name": "Existing",
    }
    client = app_with_profiles.test_client()

    payload = {"id": "security", "profile": {"name": "Security", "keywords": []}}

    response = client.post(
        "/api/profiles/global/create",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 409

    data = response.get_json()
    assert data["status"] == "error"
    assert "already exists" in data["message"].lower()


def test_update_global_profile(app_with_profiles, mock_profile_service):
    """Test PUT /api/profiles/global/<id> updates existing profile."""
    client = app_with_profiles.test_client()

    payload = {
        "profile": {
            "name": "Updated Security Profile",
            "keywords": ["vulnerability", "CVE", "exploit", "breach"],
            "scoring_weights": {"security": 2.0},
        }
    }

    response = client.put(
        "/api/profiles/global/security",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["id"] == "security"

    mock_profile_service.update_global_profile.assert_called_once_with(
        "security", payload["profile"]
    )


def test_update_global_profile_not_found(app_with_profiles, mock_profile_service):
    """Test PUT /api/profiles/global/<id> returns 404 if profile doesn't exist."""
    mock_profile_service.get_global_profile.return_value = None
    client = app_with_profiles.test_client()

    payload = {"profile": {"name": "Test", "keywords": []}}

    response = client.put(
        "/api/profiles/global/nonexistent",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 404

    data = response.get_json()
    assert data["status"] == "error"
    assert "not found" in data["message"].lower()


def test_delete_global_profile(app_with_profiles, mock_profile_service):
    """Test DELETE /api/profiles/global/<id> removes profile."""
    client = app_with_profiles.test_client()

    response = client.delete("/api/profiles/global/custom")
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["id"] == "custom"

    mock_profile_service.delete_global_profile.assert_called_once_with("custom")


def test_delete_global_profile_in_use(app_with_profiles, mock_profile_service):
    """Test DELETE /api/profiles/global/<id> returns 409 if profile is in use."""
    mock_profile_service.get_profile_usage.return_value = {
        "channels": [{"id": 123, "name": "Test Channel"}],
        "users": [],
    }
    client = app_with_profiles.test_client()

    response = client.delete("/api/profiles/global/security")
    assert response.status_code == 409

    data = response.get_json()
    assert data["status"] == "error"
    assert "in use" in data["message"].lower()
    assert "usage" in data


def test_validate_global_profile(app_with_profiles, mock_profile_service):
    """Test POST /api/profiles/global/validate validates profile structure."""
    client = app_with_profiles.test_client()

    payload = {
        "profile": {
            "name": "Test Profile",
            "keywords": ["test"],
            "scoring_weights": {"keywords": 1.0},
        }
    }

    response = client.post(
        "/api/profiles/global/validate",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["valid"] is True
    assert data["errors"] == []

    mock_profile_service.validate_global_profile.assert_called_once_with(
        payload["profile"]
    )


def test_get_profile_usage(app_with_profiles, mock_profile_service):
    """Test GET /api/profiles/global/<id>/usage returns usage information."""
    mock_profile_service.get_profile_usage.return_value = {
        "channels": [
            {"id": 123, "name": "Channel 1"},
            {"id": 456, "name": "Channel 2"},
        ],
        "users": [{"id": 789, "name": "User 1"}],
    }
    client = app_with_profiles.test_client()

    response = client.get("/api/profiles/global/security/usage")
    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert data["profile_id"] == "security"
    assert data["in_use"] is True
    assert len(data["usage"]["channels"]) == 2
    assert len(data["usage"]["users"]) == 1

    mock_profile_service.get_profile_usage.assert_called_once_with("security")


def test_api_requires_json_content_type():
    """Test that API endpoints reject non-JSON requests."""
    from flask import Flask
    from ui.routes.profiles import profiles_bp, init_profiles_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    init_profiles_routes(profile_service=Mock())
    app.register_blueprint(profiles_bp)
    client = app.test_client()

    # Test create endpoint
    response = client.post(
        "/api/profiles/global/create", data="not json", content_type="text/plain"
    )
    assert response.status_code == 400
    assert b"application/json" in response.data
