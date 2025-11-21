"""Unit tests for Phase 4: Unified Digest Worker.

Tests the UnifiedDigestWorker implementation and digest formatting logic.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tgsentinel.config import (
    AlertsCfg,
    AppCfg,
    DatabaseCfg,
    DigestCfg,
    DigestSchedule,
    ProfileDigestConfig,
    ScheduleConfig,
    SystemCfg,
)
from src.tgsentinel.digest_collector import DigestMessage
from src.tgsentinel.digest_scheduler import DigestScheduler
from src.tgsentinel.digest_worker import UnifiedDigestWorker


def create_test_app_cfg() -> AppCfg:
    """Create a minimal AppCfg for testing."""
    return AppCfg(
        telegram_session="test.session",
        api_id=12345,
        api_hash="test_hash",
        alerts=AlertsCfg(
            mode="dm",
            target_channel="",
            digest=DigestCfg(hourly=True, daily=True, top_n=10),
        ),
        channels=[],
        monitored_users=[],
        interests=[],
        system=SystemCfg(
            redis=MagicMock(),
            logging=MagicMock(),
            database=DatabaseCfg(
                cleanup_enabled=False,
                retention_days=30,
                max_messages=1000,
                cleanup_interval_hours=24,
                vacuum_on_cleanup=False,
                vacuum_hour=3,
            ),
        ),
        embeddings_model="test_model",
        global_profiles={},
        similarity_threshold=0.42,
    )


@pytest.fixture
def mock_engine():
    """Create a mock SQLAlchemy engine."""
    return MagicMock()


@pytest.fixture
def mock_scheduler():
    """Create a mock DigestScheduler."""
    cfg = create_test_app_cfg()
    return DigestScheduler(cfg, state_file=":memory:")


@pytest.fixture
def digest_worker(mock_engine, mock_scheduler):
    """Create a UnifiedDigestWorker instance."""
    cfg = create_test_app_cfg()
    return UnifiedDigestWorker(cfg, mock_engine, mock_scheduler)


class TestScheduleWindowHours:
    """Tests for _get_schedule_window_hours helper."""

    def test_hourly_window(self, digest_worker):
        """Test hourly schedule returns 1 hour window."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.HOURLY) == 1

    def test_every_4h_window(self, digest_worker):
        """Test every_4h schedule returns 4 hour window."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.EVERY_4H) == 4

    def test_every_6h_window(self, digest_worker):
        """Test every_6h schedule returns 6 hour window."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.EVERY_6H) == 6

    def test_every_12h_window(self, digest_worker):
        """Test every_12h schedule returns 12 hour window."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.EVERY_12H) == 12

    def test_daily_window(self, digest_worker):
        """Test daily schedule returns 24 hour window."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.DAILY) == 24

    def test_weekly_window(self, digest_worker):
        """Test weekly schedule returns 168 hour (7 day) window."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.WEEKLY) == 168

    def test_unknown_schedule_default(self, digest_worker):
        """Test unknown schedule defaults to 1 hour."""
        assert digest_worker._get_schedule_window_hours(DigestSchedule.NONE) == 1


class TestAggregateMinScore:
    """Tests for _aggregate_min_score helper."""

    def test_no_profiles(self, digest_worker):
        """Test with no profiles returns default 0.0."""
        assert digest_worker._aggregate_min_score([]) == 0.0

    def test_single_profile_with_score(self, digest_worker):
        """Test single profile with min_score set."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=7.5)
                    ]
                ),
                ["security"],  # Add profile_ids
            )
        ]
        assert digest_worker._aggregate_min_score(profiles) == 7.5

    def test_multiple_profiles_returns_minimum(self, digest_worker):
        """Test multiple profiles returns the minimum score."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=7.5)
                    ]
                ),
                ["security"],
            ),
            (
                "trading",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=6.0)
                    ]
                ),
                ["trading"],
            ),
            (
                "urgent",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=8.5)
                    ]
                ),
                ["urgent"],
            ),
        ]
        assert digest_worker._aggregate_min_score(profiles) == 6.0

    def test_profiles_with_none_min_score(self, digest_worker):
        """Test profiles without min_score set are ignored."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=None)
                    ]
                ),
                ["security"],
            ),
            (
                "trading",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=7.0)
                    ]
                ),
                ["trading"],
            ),
        ]
        assert digest_worker._aggregate_min_score(profiles) == 7.0

    def test_all_profiles_none_min_score(self, digest_worker):
        """Test all profiles with None min_score returns default."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=None)
                    ]
                ),
                ["security"],
            ),
            (
                "trading",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=None)
                    ]
                ),
                ["trading"],
            ),
        ]
        assert digest_worker._aggregate_min_score(profiles) == 0.0


class TestAggregateTopN:
    """Tests for _aggregate_top_n helper."""

    def test_no_profiles_returns_global_default(self, digest_worker):
        """Test with no profiles returns global config default."""
        assert digest_worker._aggregate_top_n([]) == 10  # From create_test_app_cfg

    def test_single_profile_with_top_n(self, digest_worker):
        """Test single profile with top_n set."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(
                    schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)], top_n=15
                ),
                ["security"],
            )
        ]
        assert digest_worker._aggregate_top_n(profiles) == 15

    def test_multiple_profiles_returns_maximum(self, digest_worker):
        """Test multiple profiles returns the maximum top_n."""
        profiles = [
            ("security", ProfileDigestConfig(schedules=[], top_n=15), ["security"]),
            ("trading", ProfileDigestConfig(schedules=[], top_n=25), ["trading"]),
            ("urgent", ProfileDigestConfig(schedules=[], top_n=5), ["urgent"]),
        ]
        assert digest_worker._aggregate_top_n(profiles) == 25

    def test_profiles_with_none_top_n(self, digest_worker):
        """Test profiles using default top_n value."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(schedules=[]),
                ["security"],
            ),  # Uses default top_n=10
            ("trading", ProfileDigestConfig(schedules=[], top_n=20), ["trading"]),
        ]
        # Should return 20 (max of 10 and 20)
        assert digest_worker._aggregate_top_n(profiles) == 20

    def test_all_profiles_default_top_n(self, digest_worker):
        """Test all profiles with default top_n returns max default."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(schedules=[]),
                ["security"],
            ),  # default top_n=10
            (
                "trading",
                ProfileDigestConfig(schedules=[]),
                ["trading"],
            ),  # default top_n=10
        ]
        # Both have default top_n=10
        assert digest_worker._aggregate_top_n(profiles) == 10

    def test_schedule_level_top_n(self, digest_worker):
        """Test schedule-level top_n is considered."""
        profiles = [
            (
                "security",
                ProfileDigestConfig(
                    schedules=[
                        ScheduleConfig(schedule=DigestSchedule.HOURLY, top_n=30)
                    ],
                ),
                ["security"],
            )
        ]
        # Should consider both profile-level (10) and schedule-level (30), return max
        assert digest_worker._aggregate_top_n(profiles) == 30


class TestFormatTriggers:
    """Tests for _format_triggers helper."""

    def test_empty_string(self, digest_worker):
        """Test empty trigger annotations returns empty string."""
        assert digest_worker._format_triggers("") == ""

    def test_invalid_json(self, digest_worker):
        """Test invalid JSON returns empty string."""
        assert digest_worker._format_triggers("not-json") == ""

    def test_empty_dict(self, digest_worker):
        """Test empty dict returns empty string."""
        import json

        assert digest_worker._format_triggers(json.dumps({})) == ""

    def test_single_category(self, digest_worker):
        """Test single category with keywords."""
        import json

        annotations = {"security": ["CVE", "vulnerability"]}
        result = digest_worker._format_triggers(json.dumps(annotations))
        assert "🔒 security: CVE, vulnerability" in result
        assert result.startswith("\n🎯 ")

    def test_multiple_categories(self, digest_worker):
        """Test multiple categories with keywords."""
        import json

        annotations = {
            "security": ["CVE", "exploit"],
            "urgency": ["critical", "urgent"],
        }
        result = digest_worker._format_triggers(json.dumps(annotations))
        assert "🔒 security: CVE, exploit" in result
        assert "⚡ urgency: critical, urgent" in result
        assert " • " in result

    def test_keyword_limit(self, digest_worker):
        """Test that keywords are limited to 3 with +N more indicator."""
        import json

        annotations = {"security": ["CVE", "vuln", "exploit", "patch", "update"]}
        result = digest_worker._format_triggers(json.dumps(annotations))
        assert "CVE, vuln, exploit, +2 more" in result


class TestFormatProfileBadges:
    """Tests for _format_profile_badges helper."""

    def test_empty_profiles(self, digest_worker):
        """Test empty profiles list returns empty string."""
        assert digest_worker._format_profile_badges([]) == ""

    def test_single_profile(self, digest_worker):
        """Test single profile badge formatting."""
        result = digest_worker._format_profile_badges(["security"])
        assert result == "\n📋 `security`"

    def test_multiple_profiles(self, digest_worker):
        """Test multiple profile badges formatting."""
        result = digest_worker._format_profile_badges(
            ["security", "critical_updates", "urgent"]
        )
        assert result == "\n📋 `security` `critical_updates` `urgent`"


class TestSplitIntoChunks:
    """Tests for _split_into_chunks helper."""

    def test_short_text_single_chunk(self, digest_worker):
        """Test short text returns single chunk."""
        text = "Short digest message"
        chunks = digest_worker._split_into_chunks(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_multiple_chunks(self, digest_worker):
        """Test long text is split into multiple chunks."""
        # Create text longer than 4000 chars
        lines = ["Header\n"]
        for i in range(100):
            lines.append(f"**{i}. Very long message line** with lots of content\n")

        text = "\n".join(lines)
        chunks = digest_worker._split_into_chunks(text, max_length=500)
        assert len(chunks) > 1
        # First chunk should have header
        assert chunks[0].startswith("Header")

    def test_respect_max_length(self, digest_worker):
        """Test chunks respect max_length parameter."""
        text = "A" * 5000
        chunks = digest_worker._split_into_chunks(text, max_length=1000)
        for chunk in chunks:
            assert len(chunk) <= 1000


class TestFormatDigest:
    """Tests for _format_digest method."""

    def test_basic_digest_format(self, digest_worker):
        """Test basic digest formatting with single message."""
        messages = [
            DigestMessage(
                chat_id=-1001234567890,
                msg_id=100,
                score=8.5,
                chat_title="Security Channel",
                sender_name="Security Bot",
                message_text="Critical CVE discovered",
                trigger_annotations='{"security": ["CVE"]}',
                created_at=datetime(2025, 11, 20, 14, 30, tzinfo=timezone.utc),
                matched_profiles=["security"],
            )
        ]

        result = digest_worker._format_digest(DigestSchedule.HOURLY, messages, 1)

        # Check header
        assert "Hourly Digest" in result
        assert "Top 1 highlights" in result
        assert "(last 1h)" in result

        # Check message content
        assert "Security Channel" in result
        assert "Score: 8.5" in result
        assert "Security Bot" in result
        assert "14:30" in result
        assert "Critical CVE discovered" in result

        # Check profile badge
        assert "`security`" in result

        # Check trigger formatting
        assert "🔒 security: CVE" in result

    def test_multiple_messages(self, digest_worker):
        """Test digest with multiple messages is numbered correctly."""
        messages = [
            DigestMessage(
                chat_id=-1001234567890,
                msg_id=100,
                score=9.0,
                chat_title="Channel 1",
                sender_name="User 1",
                message_text="Message 1",
                trigger_annotations="",
                created_at=datetime(2025, 11, 20, 14, 30, tzinfo=timezone.utc),
                matched_profiles=[],
            ),
            DigestMessage(
                chat_id=-1001234567890,
                msg_id=101,
                score=8.5,
                chat_title="Channel 2",
                sender_name="User 2",
                message_text="Message 2",
                trigger_annotations="",
                created_at=datetime(2025, 11, 20, 14, 35, tzinfo=timezone.utc),
                matched_profiles=[],
            ),
        ]

        result = digest_worker._format_digest(DigestSchedule.DAILY, messages, 24)

        # Check numbering
        assert "**1. [Channel 1]" in result
        assert "**2. [Channel 2]" in result

    def test_message_text_truncation(self, digest_worker):
        """Test long message text is truncated."""
        long_text = "A" * 200  # Longer than 150 char limit

        messages = [
            DigestMessage(
                chat_id=12345,
                msg_id=100,
                score=7.0,
                chat_title="Test",
                sender_name="Test User",
                message_text=long_text,
                trigger_annotations="",
                created_at=datetime(2025, 11, 20, 14, 30, tzinfo=timezone.utc),
                matched_profiles=[],
            )
        ]

        result = digest_worker._format_digest(DigestSchedule.HOURLY, messages, 1)

        # Should be truncated to 150 chars + "..."
        assert "A" * 150 + "..." in result
        assert long_text not in result

    def test_telegram_link_formatting(self, digest_worker):
        """Test Telegram links are formatted correctly for different chat types."""
        # Supergroup/channel (starts with -100)
        messages = [
            DigestMessage(
                chat_id=-1001234567890,
                msg_id=100,
                score=7.0,
                chat_title="Channel",
                sender_name="User",
                message_text="Test",
                trigger_annotations="",
                created_at=datetime(2025, 11, 20, 14, 30, tzinfo=timezone.utc),
                matched_profiles=[],
            )
        ]

        result = digest_worker._format_digest(DigestSchedule.HOURLY, messages, 1)
        assert "https://t.me/c/1234567890/100" in result

        # Regular group (negative ID, not starting with -100)
        messages = [
            DigestMessage(
                chat_id=-12345,
                msg_id=100,
                score=7.0,
                chat_title="Group",
                sender_name="User",
                message_text="Test",
                trigger_annotations="",
                created_at=datetime(2025, 11, 20, 14, 30, tzinfo=timezone.utc),
                matched_profiles=[],
            )
        ]

        result = digest_worker._format_digest(DigestSchedule.HOURLY, messages, 1)
        assert "tg://openmessage?chat_id=-12345&message_id=100" in result


class TestDeliveryModeValidation:
    """Tests for digest delivery mode validation in _send_digest."""

    @pytest.mark.asyncio
    async def test_invalid_mode_logs_warning(self, digest_worker, caplog):
        """Test invalid mode logs warning and returns without sending."""
        import logging

        caplog.set_level(logging.WARNING)

        mock_client = AsyncMock()

        await digest_worker._send_digest(
            client=mock_client,
            schedule=DigestSchedule.HOURLY,
            messages=[],
            mode="invalid",
            target_channel=None,
            since_hours=1,
        )

        # Verify warning was logged
        assert any("Invalid digest mode" in record.message for record in caplog.records)
        # Should not attempt to send
        mock_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_mode_without_target_falls_back_to_dm(
        self, digest_worker, caplog
    ):
        """Test channel mode without target_channel falls back to DM."""
        mock_client = AsyncMock()
        messages = [
            DigestMessage(
                chat_id=12345,
                msg_id=100,
                score=7.0,
                chat_title="Test",
                sender_name="User",
                message_text="Test message",
                trigger_annotations="",
                created_at=datetime(2025, 11, 20, 14, 30, tzinfo=timezone.utc),
                matched_profiles=[],
            )
        ]

        await digest_worker._send_digest(
            client=mock_client,
            schedule=DigestSchedule.HOURLY,
            messages=messages,
            mode="channel",
            target_channel=None,  # Missing channel
            since_hours=1,
        )

        # Should send to DM only
        assert mock_client.send_message.call_count >= 1
        # First call should be to "me"
        assert mock_client.send_message.call_args_list[0][0][0] == "me"


@pytest.mark.asyncio
class TestProcessDigestSchedule:
    """Integration tests for _process_digest_schedule."""

    @pytest.mark.asyncio
    async def test_no_profiles_skips_processing(
        self, digest_worker, mock_scheduler, caplog
    ):
        """Test schedule with no profiles skips processing."""
        import logging

        # Set logger to capture INFO level messages
        caplog.set_level(logging.INFO)

        mock_client = AsyncMock()
        mock_scheduler.discover_profile_schedules = MagicMock(return_value=[])

        await digest_worker._process_digest_schedule(
            DigestSchedule.HOURLY, mock_client, datetime.now(timezone.utc)
        )

        # Should log and return early
        assert any(
            "No profiles configured" in record.message for record in caplog.records
        )
        mock_client.send_message.assert_not_called()

    async def test_no_messages_skips_sending(
        self, digest_worker, mock_scheduler, mock_engine
    ):
        """Test schedule with no messages skips sending."""
        mock_client = AsyncMock()
        mock_scheduler.discover_profile_schedules = MagicMock(
            return_value=[
                (
                    "security",
                    ProfileDigestConfig(
                        schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
                        top_n=10,
                    ),
                    ["security"],  # Add profile_ids
                )
            ]
        )

        # Mock DigestCollector to return no messages
        with patch(
            "src.tgsentinel.digest_worker.DigestCollector"
        ) as mock_collector_class:
            mock_collector = MagicMock()
            mock_collector.get_top_messages.return_value = []
            mock_collector_class.return_value = mock_collector

            await digest_worker._process_digest_schedule(
                DigestSchedule.HOURLY, mock_client, datetime.now(timezone.utc)
            )

        # Should not send anything
        mock_client.send_message.assert_not_called()
