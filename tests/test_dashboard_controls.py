def test_dashboard_controls_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # System Health refresh button
    assert 'id="btn-refresh-health"' in html
    # Live toggle presence
    assert 'id="live-toggle-switch"' in html
    # Titles present
    assert "System Health" in html
    assert "Live Activity Feed" in html
