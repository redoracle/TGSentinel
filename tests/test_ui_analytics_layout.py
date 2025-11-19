"""UI-focused tests for the Analytics page.

These tests verify that recent UI tweaks to the analytics page are present in
the rendered HTML and that the backend endpoints used by the charts respond
successfully. We intentionally avoid browser automation here and assert on the
server-rendered markup and static JS config strings.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg


pytestmark = pytest.mark.contract


@pytest.fixture
def mock_config():
    return AppCfg(
        telegram_session="/tmp/test.session",
        api_id=12345,
        api_hash="test_hash",
        alerts=AlertsCfg(
            mode="both",
            target_channel="@test_bot",
            digest=DigestCfg(hourly=True, daily=True, top_n=10),
        ),
        channels=[],
        monitored_users=[],
        interests=["kw1", "kw2"],
        redis={"host": "redis", "port": 6379, "stream": "test"},
        db_uri="sqlite:///test.db",
        embeddings_model="all-MiniLM-L6-v2",
        similarity_threshold=0.42,
    )


@pytest.fixture
def app_client(mock_config):
    ui_path = Path(__file__).parent.parent / "ui"
    import sys

    sys.path.insert(0, str(ui_path))

    with patch("redis.Redis") as mock_redis:
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis_instance.xlen.return_value = 0
        mock_redis.return_value = mock_redis_instance

        with patch("app.load_config", return_value=mock_config):
            import app as flask_app  # type: ignore

            flask_app.app.config["TESTING"] = True
            with flask_app.app.test_client() as client:
                yield client


def test_keyword_heatmap_header_alignment(app_client):
    """Refresh button aligned to the far right of heatmap header."""
    resp = app_client.get("/analytics")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    # The header should use justify-content-between to push refresh to far right
    assert "Keyword Heatmap" in html
    assert "card-header d-flex justify-content-between align-items-center" in html


def test_channel_donut_legend_color_white_and_shadow(app_client):
    """Legend labels must use white text and donut should have soft shadow class."""
    resp = app_client.get("/analytics")
    html = resp.data.decode("utf-8")
    # Legend label color forced to white in Chart.js config
    assert "labels: {" in html
    assert "color: '#ffffff'" in html
    # Canvas uses shadow-soft class to add subtle separation
    assert 'id="channel-chart"' in html and 'class="shadow-soft"' in html


def test_performance_chart_scale_contrast_and_padding(app_client):
    """Ensure readability tweaks for scales and layout are present."""
    html = app_client.get("/analytics").data.decode("utf-8")
    # Ticks/grid color adjustments and layout padding
    assert 'ticks: { color: "#cfd6ff" }' in html or "#cfd6ff" in html
    assert 'grid: { color: "rgba(255,255,255,0.08)" }' in html
    assert "layout: { padding:" in html


def test_keywords_chart_config_compact_and_colors(app_client):
    """Heatmap bar color and compact layout hints exist in config."""
    html = app_client.get("/analytics").data.decode("utf-8")
    assert "keywords-chart" in html
    # Check for dynamic color palette generation
    assert "backgroundColor: backgroundColor" in html
    assert "basePalette" in html
    assert 'indexAxis: "y"' in html


def test_anomaly_monitor_table_structure_and_empty_state(app_client):
    """Anomaly monitor table renders with correct columns and empty state."""
    html = app_client.get("/analytics").data.decode("utf-8")
    # Column headers present
    for col in ["Channel", "Signal", "Severity", "Detected", "Action"]:
        assert f">{col}<" in html
    # Empty state row
    assert "No anomalies flagged." in html


def test_analytics_api_endpoints_live(app_client):
    """Back-end analytics endpoints should respond successfully for charts."""
    r1 = app_client.get("/api/analytics/metrics")
    assert r1.status_code == 200
    assert isinstance(r1.get_json(), dict)

    r2 = app_client.get("/api/analytics/keywords")
    assert r2.status_code == 200
    assert "keywords" in r2.get_json()
