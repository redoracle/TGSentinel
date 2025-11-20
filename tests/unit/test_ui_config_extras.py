import io

import pytest


@pytest.mark.unit
def test_config_export_download(client, mock_init, monkeypatch, tmp_path):
    """Export YAML returns a downloadable file."""
    # Point to a temp config file
    cfg = tmp_path / "tgsentinel.yml"
    cfg.write_text("channels: []\n", encoding="utf-8")
    monkeypatch.setenv("TG_SENTINEL_CONFIG", str(cfg))

    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    # Content-Type may vary between servers; accept yaml or octet-stream
    assert (
        "text" in resp.headers.get("Content-Type", "")
        or "yaml" in resp.headers.get("Content-Type", "")
        or "octet" in resp.headers.get("Content-Type", "")
    )
    cd = resp.headers.get("Content-Disposition", "")
    assert "attachment;" in cd
    assert ".yml" in cd or ".yaml" in cd


def test_config_rules_test_basic(client, mock_init):
    """Rules test returns a well-formed JSON structure."""
    resp = client.post(
        "/api/config/rules/test", json={"channel_ids": [], "text": "hello world"}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "tested" in data
    assert isinstance(data.get("results", []), list)


def test_config_stats_reset_ok(client, mock_init, monkeypatch):
    """Reset stats endpoint should succeed even without Redis."""
    # Ensure redis_client is None path is exercised
    monkeypatch.setattr("app.redis_client", None, raising=False)
    resp = client.post("/api/config/stats/reset")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "cleared_keys" in data
