"""
Tests for developer panel webhook testing functionality.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml


class TestWebhookTesting:
    """Test webhook delivery testing endpoints."""

    def test_test_single_webhook__success__returns_ok(self, client, mock_init):
        """Test successful webhook delivery test."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"
            webhook_data = {
                "webhooks": [
                    {
                        "service": "slack",
                        "url": "https://hooks.slack.com/test",
                        "enabled": True,
                    }
                ]
            }

            with open(webhooks_path, "w") as f:
                yaml.dump(webhook_data, f)

            # Mock successful HTTP response
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.123

            with patch("ui.app.Path", return_value=webhooks_path):
                with patch("requests.post", return_value=mock_response) as mock_post:
                    response = client.post("/api/webhooks/slack/test")

                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert data["status"] == "ok"
                    assert data["service"] == "slack"
                    assert data["status_code"] == 200
                    assert "response_time_ms" in data

                    # Verify request was made
                    mock_post.assert_called_once()

    def test_test_webhook__not_found__returns_404(self, client, mock_init):
        """Test webhook test for non-existent service."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"
            webhook_data = {"webhooks": []}

            with open(webhooks_path, "w") as f:
                yaml.dump(webhook_data, f)

            with patch("ui.app.Path", return_value=webhooks_path):
                response = client.post("/api/webhooks/nonexistent/test")

                assert response.status_code == 404
                data = json.loads(response.data)
                assert "not found" in data["message"].lower()

    def test_test_webhook__timeout__returns_504(self, client, mock_init):
        """Test webhook delivery timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"
            webhook_data = {
                "webhooks": [
                    {
                        "service": "slow-service",
                        "url": "https://example.com/slow",
                        "enabled": True,
                    }
                ]
            }

            with open(webhooks_path, "w") as f:
                yaml.dump(webhook_data, f)

            # Mock timeout
            import requests

            with patch("ui.app.Path", return_value=webhooks_path):
                with patch("requests.post", side_effect=requests.exceptions.Timeout()):
                    response = client.post("/api/webhooks/slow-service/test")

                    assert response.status_code == 504
                    data = json.loads(response.data)
                    assert (
                        "timed out" in data["message"].lower()
                        or "timeout" in data["message"].lower()
                    )

    def test_test_webhook__with_secret__includes_signature(self, client, mock_init):
        """Test webhook delivery includes HMAC signature when secret is configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"
            webhook_data = {
                "webhooks": [
                    {
                        "service": "secure",
                        "url": "https://example.com/webhook",
                        "secret": "my-secret-key",
                        "enabled": True,
                    }
                ]
            }

            with open(webhooks_path, "w") as f:
                yaml.dump(webhook_data, f)

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.1

            with patch("ui.app.Path", return_value=webhooks_path):
                with patch("requests.post", return_value=mock_response) as mock_post:
                    response = client.post("/api/webhooks/secure/test")

                    assert response.status_code == 200

                    # Verify signature header was included
                    call_args = mock_post.call_args
                    headers = call_args[1]["headers"]
                    assert "X-Webhook-Signature" in headers
                    assert headers["X-Webhook-Signature"].startswith("sha256=")

    def test_test_all_webhooks__multiple_services__returns_all_results(
        self, client, mock_init
    ):
        """Test sending sample alert to all webhooks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"
            webhook_data = {
                "webhooks": [
                    {
                        "service": "service1",
                        "url": "https://example.com/webhook1",
                        "enabled": True,
                    },
                    {
                        "service": "service2",
                        "url": "https://example.com/webhook2",
                        "enabled": True,
                    },
                    {
                        "service": "service3",
                        "url": "https://example.com/webhook3",
                        "enabled": False,  # Should be skipped
                    },
                ]
            }

            with open(webhooks_path, "w") as f:
                yaml.dump(webhook_data, f)

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.1

            with patch("ui.app.Path", return_value=webhooks_path):
                with patch("requests.post", return_value=mock_response):
                    response = client.post("/api/webhooks/test-all")

                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert data["status"] == "ok"
                    assert len(data["results"]) == 3

                    # Check results
                    results = {r["service"]: r for r in data["results"]}
                    assert results["service1"]["status"] == "ok"
                    assert results["service2"]["status"] == "ok"
                    assert results["service3"]["status"] == "skipped"
                    assert "disabled" in results["service3"]["message"].lower()

    def test_test_all_webhooks__no_webhooks__returns_empty_results(
        self, client, mock_init
    ):
        """Test sample alert when no webhooks configured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"
            webhook_data = {"webhooks": []}

            with open(webhooks_path, "w") as f:
                yaml.dump(webhook_data, f)

            with patch("ui.app.Path", return_value=webhooks_path):
                response = client.post("/api/webhooks/test-all")

                assert response.status_code == 200
                data = json.loads(response.data)
                assert data["status"] == "ok"
                assert len(data["results"]) == 0
                assert "No webhooks configured" in data["message"]


class TestWebhookURLValidation:
    """Test URL format validation for webhook creation."""

    def test_create_webhook__valid_url__accepts(self, client, mock_init):
        """Test creating webhook with valid URL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"

            with patch("ui.app.Path", return_value=webhooks_path):
                response = client.post(
                    "/api/webhooks",
                    json={
                        "service": "valid",
                        "url": "https://hooks.example.com/webhook/abc123",
                    },
                    content_type="application/json",
                )

                assert response.status_code == 201
                data = json.loads(response.data)
                assert data["status"] == "ok"

    def test_create_webhook__invalid_url__rejects(self, client, mock_init):
        """Test creating webhook with invalid URL format."""
        response = client.post(
            "/api/webhooks",
            json={
                "service": "invalid",
                "url": "not a valid url",
            },
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid URL format" in data["message"]

    def test_create_webhook__missing_protocol__rejects(self, client, mock_init):
        """Test creating webhook without http/https protocol."""
        response = client.post(
            "/api/webhooks",
            json={
                "service": "noprotocol",
                "url": "example.com/webhook",
            },
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid URL format" in data["message"]
        assert "http://" in data["message"] or "https://" in data["message"]

    def test_create_webhook__localhost_url__accepts(self, client, mock_init):
        """Test creating webhook with localhost URL (valid for testing)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"

            with patch("ui.app.Path", return_value=webhooks_path):
                response = client.post(
                    "/api/webhooks",
                    json={
                        "service": "localhost",
                        "url": "http://localhost:3000/webhook",
                    },
                    content_type="application/json",
                )

                assert response.status_code == 201
                data = json.loads(response.data)
                assert data["status"] == "ok"

    def test_create_webhook__ip_address_url__accepts(self, client, mock_init):
        """Test creating webhook with IP address URL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webhooks_path = Path(tmpdir) / "webhooks.yml"

            with patch("ui.app.Path", return_value=webhooks_path):
                response = client.post(
                    "/api/webhooks",
                    json={
                        "service": "ip-based",
                        "url": "http://192.168.1.100:8080/webhook",
                    },
                    content_type="application/json",
                )

                assert response.status_code == 201
                data = json.loads(response.data)
                assert data["status"] == "ok"
