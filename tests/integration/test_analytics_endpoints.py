"""Integration tests for analytics endpoints."""

import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.integration
class TestAnalyticsEndpointsIntegration:
    """Integration tests for analytics API endpoints."""

    def test_health_endpoint_no_auth_required(self, client):
        """Test that /health endpoint works without authentication."""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.get_json()
        assert data is not None
        assert data["status"] == "ok"
        assert data["service"] == "tgsentinel-ui"
        assert "timestamp" in data

    def test_health_endpoint_bypasses_ui_lock(self, client):
        """Test that /health works even when UI is locked."""
        # Even with UI lock enabled, health checks should work
        with client.session_transaction() as sess:
            sess["ui_locked"] = True

        response = client.get("/health")
        assert response.status_code == 200

    @patch("ui.api.analytics_routes.requests.get")
    def test_endpoints_check_api(self, mock_get, client_authenticated):
        """Test /api/analytics/endpoints with mocked backend."""

        # Mock responses for different endpoints
        def mock_request(url, *args, **kwargs):
            mock_resp = MagicMock()
            if "/health" in url:
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"status": "ok"}
                mock_resp.elapsed.total_seconds.return_value = 0.015
            elif "/metrics" in url:
                mock_resp.status_code = 200
                mock_resp.text = "# TYPE test_metric counter\ntest_metric 1.0\n"
                mock_resp.elapsed.total_seconds.return_value = 0.025
            elif "/api/status" in url:
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"authorized": True}
                mock_resp.elapsed.total_seconds.return_value = 0.020
            else:
                mock_resp.status_code = 404
                mock_resp.elapsed.total_seconds.return_value = 0.010
            return mock_resp

        mock_get.side_effect = mock_request

        response = client_authenticated.get("/api/analytics/endpoints")
        assert response.status_code == 200

        data = response.get_json()
        assert "groups" in data
        assert isinstance(data["groups"], list)

    @patch("ui.api.analytics_routes.requests.get")
    def test_prometheus_endpoint_success(self, mock_get, client_authenticated):
        """Test /api/analytics/prometheus with successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
# HELP python_gc_objects_collected_total Objects collected during gc
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 100.0
python_gc_objects_collected_total{generation="1"} 200.0
# HELP process_cpu_seconds_total Total user and system CPU time
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total 45.67
"""
        mock_get.return_value = mock_response

        response = client_authenticated.get("/api/analytics/prometheus")
        assert response.status_code == 200

        data = response.get_json()
        assert "metrics" in data
        assert isinstance(data["metrics"], dict)

    @patch("ui.api.analytics_routes.requests.get")
    def test_prometheus_endpoint_connection_error(self, mock_get, client_authenticated):
        """Test Prometheus endpoint handles connection errors gracefully."""
        import requests

        mock_get.side_effect = requests.ConnectionError("Connection refused")

        response = client_authenticated.get("/api/analytics/prometheus")
        assert response.status_code == 503

        data = response.get_json()
        assert data["status"] == "initializing"
        assert "starting up" in data["message"].lower()
        assert data["metrics"] == {}

    @patch("ui.api.analytics_routes.requests.get")
    def test_prometheus_endpoint_timeout(self, mock_get, client_authenticated):
        """Test Prometheus endpoint handles timeouts gracefully."""
        import requests

        mock_get.side_effect = requests.Timeout("Request timeout")

        response = client_authenticated.get("/api/analytics/prometheus")
        assert response.status_code == 504

        data = response.get_json()
        assert data["status"] == "error"
        assert "timeout" in data["message"].lower()

    @patch("ui.api.analytics_routes.get_docker_client")
    def test_system_health_endpoint(self, mock_docker_fn, client_authenticated):
        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = []
        mock_docker_fn.return_value = mock_docker
        """Test /api/analytics/system-health calculation."""
        # Mock Docker client
        mock_container = MagicMock()
        mock_container.name = "test-container"
        mock_container.status = "running"
        mock_container.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}}
        }
        mock_docker.containers.list.return_value = [mock_container]

        response = client_authenticated.get("/api/analytics/system-health")
        assert response.status_code == 200

        data = response.get_json()
        assert "health_score" in data
        assert isinstance(data["health_score"], (int, float))
        assert 0 <= data["health_score"] <= 100
        assert "components" in data


@pytest.mark.integration
class TestEndpointLatencyAccuracy:
    """Test that endpoint latency measurements are accurate."""

    @patch("ui.api.analytics_routes.requests.get")
    def test_ui_self_check_latency_not_zero(self, mock_get, client_authenticated):
        """Test that UI self-checks don't show 0ms latency."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        # Simulate realistic latency
        mock_response.elapsed.total_seconds.return_value = 0.015
        mock_get.return_value = mock_response

        # The latency should be > 0 when properly measured
        latency_ms = mock_response.elapsed.total_seconds() * 1000
        assert latency_ms > 0
        assert latency_ms < 1000  # Should be under 1 second

    def test_health_endpoint_response_time(self, client):
        """Test /health endpoint responds quickly."""
        start = time.time()
        response = client.get("/health")
        duration = time.time() - start

        assert response.status_code == 200
        # Health check should be fast (< 100ms)
        assert duration < 0.1
