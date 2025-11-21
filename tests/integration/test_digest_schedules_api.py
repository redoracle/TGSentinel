import pytest

from tgsentinel.api import create_api_app, set_config
from tgsentinel.config import (
    AlertsCfg,
    AppCfg,
    DigestSchedule,
    ProfileDefinition,
    ProfileDigestConfig,
    RedisCfg,
    ScheduleConfig,
    SystemCfg,
)


def _build_test_config() -> AppCfg:
    security_profile = ProfileDefinition(
        id="security",
        digest=ProfileDigestConfig(
            schedules=[
                ScheduleConfig(
                    schedule=DigestSchedule.HOURLY,
                    top_n=6,
                    min_score=4.2,
                ),
                ScheduleConfig(
                    schedule=DigestSchedule.DAILY,
                    daily_hour=9,
                ),
            ],
            top_n=8,
            min_score=4.0,
            mode="both",
            target_channel="-100200",
        ),
    )

    ops_profile = ProfileDefinition(
        id="ops",
        digest=ProfileDigestConfig(
            schedules=[
                ScheduleConfig(
                    schedule=DigestSchedule.WEEKLY,
                    weekly_day=2,
                    weekly_hour=7,
                )
            ],
            top_n=5,
            min_score=3.5,
            mode="dm",
        ),
    )

    system_cfg = SystemCfg(
        redis=RedisCfg(stream="test:stream", group="test-group", consumer="worker-y"),
        database_uri="sqlite:///:memory:",
    )

    return AppCfg(
        telegram_session="/tmp/test.session",
        api_id=12345,
        api_hash="hash",
        alerts=AlertsCfg(min_score=2.0),
        channels=[],
        monitored_users=[],
        interests=[],
        system=system_cfg,
        embeddings_model=None,
        similarity_threshold=0.5,
        global_profiles={
            security_profile.id: security_profile,
            ops_profile.id: ops_profile,
        },
    )


@pytest.fixture
def digest_schedule_api_app():
    import tgsentinel.api as api_module

    prev_config = getattr(api_module, "_config", None)
    cfg = _build_test_config()
    api_module.set_config(cfg)
    app = api_module.create_api_app()
    yield app
    api_module.set_config(prev_config)


@pytest.mark.integration
def test_digest_schedules_endpoint_reports_all_profiles(digest_schedule_api_app):
    client = digest_schedule_api_app.test_client()
    response = client.get("/api/digest/schedules")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["status"] == "ok"

    schedules = payload["data"]["schedules"]
    schedule_map = {entry["schedule"]: entry for entry in schedules}

    assert set(schedule_map) == {"hourly", "daily", "weekly"}

    hourly_profiles = set(schedule_map["hourly"]["profiles"])
    assert hourly_profiles == {"security"}
    daily_profiles = set(schedule_map["daily"]["profiles"])
    assert daily_profiles == {"security"}
    weekly_profiles = set(schedule_map["weekly"]["profiles"])
    assert weekly_profiles == {"ops"}


@pytest.mark.integration
def test_profile_digest_config_endpoint_returns_profile_details(
    digest_schedule_api_app,
):
    client = digest_schedule_api_app.test_client()
    response = client.get("/api/digest/schedules/security")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["status"] == "ok"

    data = payload["data"]
    assert data["profile_id"] == "security"
    assert data["mode"] == "both"
    assert data["target_channel"] == "-100200"
    assert data["top_n"] == 8
    assert data["min_score"] == 4.0

    schedule_types = {entry["schedule"]: entry for entry in data["schedules"]}
    hourly_schedule = schedule_types["hourly"]
    assert hourly_schedule["top_n"] == 6
    assert hourly_schedule["min_score"] == 4.2
    daily_schedule = schedule_types["daily"]
    assert daily_schedule["daily_hour"] == 9


@pytest.mark.integration
def test_profile_digest_config_endpoint_returns_404_for_missing_profile(
    digest_schedule_api_app,
):
    client = digest_schedule_api_app.test_client()
    response = client.get("/api/digest/schedules/unknown")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["status"] == "error"
    assert "not found" in payload["error"].lower()
