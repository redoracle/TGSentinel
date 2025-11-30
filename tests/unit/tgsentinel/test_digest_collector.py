"""Unit tests for Phase 3: DigestCollector.

Tests the message collection, deduplication, and processing logic.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from src.tgsentinel.config import DigestSchedule
from src.tgsentinel.digest_collector import DigestCollector, DigestMessage


@pytest.fixture
def test_engine():
    """Create in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", future=True)

    # Create messages table with Phase 1 schema including keyword_score and Phase 0 columns
    with engine.begin() as con:
        con.execute(
            text(
                """
        CREATE TABLE messages (
            chat_id INTEGER,
            msg_id INTEGER,
            content_hash TEXT,
            score REAL,
            keyword_score REAL DEFAULT 0.0,
            semantic_scores_json TEXT,
            semantic_type TEXT,
            flagged_for_alerts_feed INTEGER DEFAULT 0,
            flagged_for_interest_feed INTEGER DEFAULT 0,
            feed_alert_flag INTEGER DEFAULT 0,
            feed_interest_flag INTEGER DEFAULT 0,
            chat_title TEXT,
            sender_name TEXT,
            sender_id INTEGER,
            message_text TEXT,
            trigger_annotations TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            matched_profiles TEXT,
            digest_schedule TEXT,
            digest_processed INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, msg_id)
        )
        """
            )
        )

    return engine


@pytest.fixture
def sample_messages(test_engine):
    """Insert sample messages for testing."""
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    two_hours_ago = now - timedelta(hours=2)

    with test_engine.begin() as con:
        # Hourly schedule, not processed
        con.execute(
            text(
                """
            INSERT INTO messages (
                chat_id, msg_id, content_hash, score, keyword_score,
                flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                digest_schedule, digest_processed
            )
            VALUES (
                -1001111111111, 1, 'hash1', 8.5, 8.5, 1, 1, 'Security Channel', 'Bot',
                1111, 'CVE-2024-1234 critical', '{"security": ["CVE"]}', :ts1, '["security"]',
                'hourly', 0
            )
        """
            ),
            {"ts1": one_hour_ago.strftime("%Y-%m-%d %H:%M:%S")},
        )

        # Hourly schedule, not processed, lower score
        con.execute(
            text(
                """
            INSERT INTO messages (
                chat_id, msg_id, content_hash, score, keyword_score,
                flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                digest_schedule, digest_processed
            )
            VALUES (
                -1001111111111, 2, 'hash2', 7.0, 7.0, 1, 1, 'Security Channel', 'Admin',
                2222, 'Vulnerability found', '{"security": ["vulnerability"]}', :ts2,
                '["security", "critical"]', 'hourly', 0
            )
        """
            ),
            {"ts2": one_hour_ago.strftime("%Y-%m-%d %H:%M:%S")},
        )

        # Daily schedule, not processed
        con.execute(
            text(
                """
            INSERT INTO messages (
                chat_id, msg_id, content_hash, score, keyword_score,
                flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                digest_schedule, digest_processed
            )
            VALUES (
                -1001222222222, 1, 'hash3', 6.5, 6.5, 1, 1, 'News Channel', 'NewsBot',
                3333, 'Daily update', '{}', :ts3, '["news"]', 'daily', 0
            )
        """
            ),
            {"ts3": two_hours_ago.strftime("%Y-%m-%d %H:%M:%S")},
        )

        # Hourly schedule, already processed
        con.execute(
            text(
                """
            INSERT INTO messages (
                chat_id, msg_id, content_hash, score, keyword_score,
                flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                digest_schedule, digest_processed
            )
            VALUES (
                -1001111111111, 3, 'hash4', 9.0, 9.0, 1, 1, 'Security Channel', 'Bot',
                4444, 'Already sent', '{}', :ts4, '["security"]', 'hourly', 1
            )
        """
            ),
            {"ts4": one_hour_ago.strftime("%Y-%m-%d %H:%M:%S")},
        )

        # Not alerted
        con.execute(
            text(
                """
            INSERT INTO messages (
                chat_id, msg_id, content_hash, score, keyword_score,
                flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                digest_schedule, digest_processed
            )
            VALUES (
                -1001333333333, 1, 'hash5', 5.0, 5.0, 0, 0, 'Test Channel', 'User',
                5555, 'Not alerted', '{}', :ts5, '["test"]', 'hourly', 0
            )
        """
            ),
            {"ts5": one_hour_ago.strftime("%Y-%m-%d %H:%M:%S")},
        )

    return test_engine


class TestDigestMessageDataclass:
    """Tests for DigestMessage dataclass."""

    def test_dedup_key(self):
        """Test dedup_key returns correct tuple."""
        msg = DigestMessage(
            chat_id=-1001234567890,
            msg_id=123,
            score=8.5,
            chat_title="Test",
            sender_name="User",
            message_text="Test message",
            trigger_annotations="{}",
            created_at=datetime.now(timezone.utc),
            matched_profiles=["security"],
        )

        assert msg.dedup_key() == (-1001234567890, 123)

    def test_default_matched_profiles(self):
        """Test matched_profiles defaults to empty list."""
        msg = DigestMessage(
            chat_id=-1001234567890,
            msg_id=123,
            score=8.5,
            chat_title="Test",
            sender_name="User",
            message_text="Test message",
            trigger_annotations="{}",
            created_at=datetime.now(timezone.utc),
        )

        assert msg.matched_profiles == []


class TestCollectAllForSchedule:
    """Tests for collect_all_for_schedule method."""

    def test_collect_hourly_messages(self, sample_messages):
        """Test collecting all hourly schedule messages."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_all_for_schedule()

        # Should collect 2 hourly messages (msg_id 1 and 2)
        # msg_id 3 is already processed, msg_id 4 is not alerted
        assert collector.count() == 2

        messages = collector.get_all_messages()
        assert messages[0].score == 8.5  # Highest score first
        assert messages[1].score == 7.0

    def test_collect_with_min_score(self, sample_messages):
        """Test collecting with minimum score threshold."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_all_for_schedule(min_score=7.5)

        # Should only collect msg_id 1 (score 8.5)
        assert collector.count() == 1
        messages = collector.get_all_messages()
        assert messages[0].score == 8.5

    def test_collect_daily_messages(self, sample_messages):
        """Test collecting daily schedule messages."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.DAILY, since_hours=3
        )
        collector.collect_all_for_schedule()

        # Should collect 1 daily message
        assert collector.count() == 1
        messages = collector.get_all_messages()
        assert messages[0].chat_title == "News Channel"

    def test_collect_empty_schedule(self, sample_messages):
        """Test collecting when no messages match schedule."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.WEEKLY, since_hours=3
        )
        collector.collect_all_for_schedule()

        assert collector.count() == 0
        assert collector.get_all_messages() == []

    def test_collect_respects_time_window(self, test_engine):
        """Test that collection respects since_hours window."""
        now = datetime.now(timezone.utc)
        old_message = now - timedelta(hours=5)

        with test_engine.begin() as con:
            con.execute(
                text(
                    """
                INSERT INTO messages (
                    chat_id, msg_id, content_hash, score, keyword_score, flagged_for_interest_feed, feed_interest_flag,
                    chat_title, sender_name, sender_id, message_text, trigger_annotations,
                    created_at, matched_profiles, digest_schedule, digest_processed
                ) VALUES (:chat_id, :msg_id, :content_hash, :score, :keyword_score, \
                :flagged_for_interest_feed, :feed_interest_flag, :chat_title, :sender_name, :sender_id, :message_text, \
                :trigger_annotations, :created_at, :matched_profiles, :digest_schedule, :digest_processed)
                """
                ),
                {
                    "chat_id": -1001111111111,
                    "msg_id": 999,
                    "content_hash": "old_hash",
                    "score": 9.0,
                    "keyword_score": 9.0,
                    "flagged_for_interest_feed": 1,
                    "feed_interest_flag": 1,
                    "chat_title": "Old Message",
                    "sender_name": "Bot",
                    "message_text": "Too old",
                    "trigger_annotations": "{}",
                    "created_at": old_message.strftime("%Y-%m-%d %H:%M:%S"),
                    "matched_profiles": '["test"]',
                    "digest_schedule": "hourly",
                    "digest_processed": 0,
                    "sender_id": 7777,
                },
            )

        # Collect with 3-hour window: should not include 5-hour-old message
        collector = DigestCollector(test_engine, DigestSchedule.HOURLY, since_hours=3)
        collector.collect_all_for_schedule()

        assert collector.count() == 0


class TestCollectForProfiles:
    """Tests for collect_for_profiles method."""

    def test_collect_for_single_profile(self, sample_messages):
        """Test collecting messages for a specific profile."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_for_profiles(["security"])

        # Should collect messages with "security" in matched_profiles
        # msg_id 1 and 2 have security and digest_processed=0
        # msg_id 3 has security but digest_processed=1 (should be excluded)
        assert collector.count() == 2

        messages = collector.get_all_messages()
        # Both should have "security" in matched_profiles
        for msg in messages:
            assert "security" in msg.matched_profiles

    def test_collect_for_multiple_profiles(self, sample_messages):
        """Test collecting messages matching any of multiple profiles."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_for_profiles(["security", "critical", "news"])

        # Should collect messages matching any of these profiles
        # msg 1: security
        # msg 2: security, critical
        # msg 3: news (but daily schedule, still gets collected)
        assert collector.count() >= 2

    def test_collect_for_nonexistent_profile(self, sample_messages):
        """Test collecting for profile that has no messages."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_for_profiles(["nonexistent"])

        assert collector.count() == 0

    def test_collect_for_empty_profile_list(self, sample_messages):
        """Test that empty profile list is handled gracefully."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_for_profiles([])

        assert collector.count() == 0

    def test_semantic_scores_populate_message(self, sample_messages):
        """Ensure semantic_scores_json overrides legacy score for matching profiles."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )

        with sample_messages.begin() as con:
            con.execute(
                text(
                    """
                INSERT INTO messages (
                    chat_id, msg_id, content_hash, score, keyword_score,
                    flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                    sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                    digest_schedule, digest_processed, semantic_scores_json
                ) VALUES (
                    -1001111111111, 12345, 'semantic_hash', 5.0, 4.0, 1, 1,
                    'Semantic Channel', 'AI Bot', 8888, 'Semantic match', '{}', :ts,
                    '["security"]', 'hourly', 0, '{"security": 9.5, "other": 3.2}'
                )
                """
                ),
                {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
            )

        collector.collect_for_profiles(["security"])
        collected = [msg for msg in collector.get_all_messages() if msg.msg_id == 12345]
        assert collected, "Expected semantic message to be collected"
        semantic_msg = collected[0]
        assert semantic_msg.semantic_score == 9.5
        assert semantic_msg.score == 9.5

    def test_score_falls_back_to_keyword(self, sample_messages):
        """Ensure score is populated even when the legacy score column is null."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )

        with sample_messages.begin() as con:
            con.execute(
                text(
                    """
                INSERT INTO messages (
                    chat_id, msg_id, content_hash, score, keyword_score,
                    flagged_for_interest_feed, feed_interest_flag, chat_title, sender_name,
                    sender_id, message_text, trigger_annotations, created_at, matched_profiles,
                    digest_schedule, digest_processed
                ) VALUES (
                    -1001111111111, 12346, 'keyword_hash', NULL, 6.25, 1, 1,
                    'Keyword Channel', 'Keyword Bot', 6666, 'Keyword match', '{}', :ts,
                    '["security"]', 'hourly', 0
                )
                """
                ),
                {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
            )

        collector.collect_for_profiles(["security"])
        collected = [msg for msg in collector.get_all_messages() if msg.msg_id == 12346]
        assert collected, "Expected keyword fallback message to be collected"
        keyword_msg = collected[0]
        assert keyword_msg.keyword_score == 6.25
        assert keyword_msg.score == 6.25


class TestDeduplication:
    """Tests for message deduplication logic."""

    def test_duplicate_message_merged(self, test_engine):
        """Test that duplicate messages are merged."""
        now = datetime.now(timezone.utc)

        # Insert same message twice (simulating collection from different sources)
        with test_engine.begin() as con:
            con.execute(
                text(
                    """
                INSERT INTO messages (
                    chat_id, msg_id, content_hash, score, keyword_score, flagged_for_interest_feed, feed_interest_flag,
                    chat_title, sender_name, sender_id, message_text, trigger_annotations,
                    created_at, matched_profiles, digest_schedule, digest_processed
                )
                VALUES (
                    :chat_id, :msg_id, :content_hash, :score, :keyword_score,
                    :flagged_for_interest_feed, :feed_interest_flag, :chat_title, :sender_name,
                    :sender_id, :message_text, :trigger_annotations, :created_at, :matched_profiles,
                    :digest_schedule, :digest_processed
                )
                """
                ),
                {
                    "chat_id": -1001111111111,
                    "msg_id": 1,
                    "content_hash": "hash1",
                    "score": 8.5,
                    "keyword_score": 8.5,
                    "flagged_for_interest_feed": 1,
                    "feed_interest_flag": 1,
                    "chat_title": "Test",
                    "sender_name": "Bot",
                    "sender_id": 9010,
                    "message_text": "Test msg",
                    "trigger_annotations": "{}",
                    "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "matched_profiles": '["security"]',
                    "digest_schedule": "hourly",
                    "digest_processed": 0,
                },
            )

        collector = DigestCollector(test_engine, DigestSchedule.HOURLY, since_hours=3)
        collector.collect_all_for_schedule()

        # Manually add the same message again to test deduplication
        msg = DigestMessage(
            chat_id=-1001111111111,
            msg_id=1,
            score=7.0,  # Lower score
            chat_title="Test",
            sender_name="Bot",
            message_text="Test msg",
            trigger_annotations="{}",
            created_at=now,
            matched_profiles=["critical"],  # Different profile
        )
        collector._add_or_merge_message(msg)

        # Should still have only 1 message
        assert collector.count() == 1

        # Should have merged profiles and kept higher score
        messages = collector.get_all_messages()
        assert messages[0].score == 8.5  # Higher score kept
        assert set(messages[0].matched_profiles) == {"security", "critical"}

    def test_profile_deduplication(self, test_engine):
        """Test that matched_profiles are deduplicated when merging."""
        now = datetime.now(timezone.utc)

        with test_engine.begin() as con:
            con.execute(
                text(
                    """
                INSERT INTO messages (
                    chat_id, msg_id, content_hash, score, keyword_score, flagged_for_interest_feed, feed_interest_flag,
                    chat_title, sender_name, sender_id, message_text, trigger_annotations,
                    created_at, matched_profiles, digest_schedule, digest_processed
                )
                VALUES (
                    :chat_id, :msg_id, :content_hash, :score, :keyword_score,
                    :flagged_for_interest_feed, :feed_interest_flag, :chat_title, :sender_name,
                    :sender_id, :message_text, :trigger_annotations, :created_at, :matched_profiles,
                    :digest_schedule, :digest_processed
                )
                """
                ),
                {
                    "chat_id": -1001111111111,
                    "msg_id": 1,
                    "content_hash": "hash1",
                    "score": 8.5,
                    "keyword_score": 8.5,
                    "flagged_for_interest_feed": 1,
                    "feed_interest_flag": 1,
                    "chat_title": "Test",
                    "sender_name": "Bot",
                    "sender_id": 9011,
                    "message_text": "Test msg",
                    "trigger_annotations": "{}",
                    "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "matched_profiles": '["security", "critical"]',
                    "digest_schedule": "hourly",
                    "digest_processed": 0,
                },
            )

        collector = DigestCollector(test_engine, DigestSchedule.HOURLY, since_hours=3)
        collector.collect_all_for_schedule()

        # Add same message with overlapping profiles
        msg = DigestMessage(
            chat_id=-1001111111111,
            msg_id=1,
            score=7.0,
            chat_title="Test",
            sender_name="Bot",
            message_text="Test msg",
            trigger_annotations="{}",
            created_at=now,
            matched_profiles=["security", "urgent"],  # security is duplicate
        )
        collector._add_or_merge_message(msg)

        messages = collector.get_all_messages()
        # Should have 3 unique profiles: security, critical, urgent
        assert len(messages[0].matched_profiles) == 3
        assert set(messages[0].matched_profiles) == {"security", "critical", "urgent"}


class TestGetTopMessages:
    """Tests for get_top_messages method."""

    def test_get_top_n(self, sample_messages):
        """Test getting top N messages by score."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_all_for_schedule()

        top_messages = collector.get_top_messages(1)
        assert len(top_messages) == 1
        assert top_messages[0].score == 8.5

    def test_top_n_larger_than_collection(self, sample_messages):
        """Test requesting more messages than available."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_all_for_schedule()

        top_messages = collector.get_top_messages(100)
        assert len(top_messages) == collector.count()

    def test_messages_sorted_by_score(self, sample_messages):
        """Test that messages are sorted by score descending."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_all_for_schedule()

        all_messages = collector.get_all_messages()
        scores = [msg.score for msg in all_messages]

        # Should be in descending order
        assert scores == sorted(scores, reverse=True)


class TestMarkAsProcessed:
    """Tests for mark_as_processed method."""

    def test_mark_messages_as_processed(self, sample_messages):
        """Test marking collected messages as processed."""
        collector = DigestCollector(
            sample_messages, DigestSchedule.HOURLY, since_hours=3
        )
        collector.collect_all_for_schedule()

        initial_count = collector.count()
        assert initial_count == 2  # msg_id 1 and 2

        # Mark as processed
        collector.mark_as_processed()

        # Verify in database
        with sample_messages.begin() as con:
            result = con.execute(
                text(
                    """
                SELECT COUNT(*) FROM messages
                WHERE digest_schedule = 'hourly'
                  AND digest_processed = 1
                """
                )
            ).scalar()

            # Should now have 3 processed hourly messages (2 new + 1 existing)
            assert result == 3

    def test_mark_empty_collection(self, test_engine):
        """Test marking when no messages collected."""
        collector = DigestCollector(test_engine, DigestSchedule.HOURLY, since_hours=3)
        # Don't collect anything
        collector.mark_as_processed()  # Should not error

        assert collector.count() == 0


class TestParseMatchedProfiles:
    """Tests for _parse_matched_profiles static method."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON array."""
        profiles = DigestCollector._parse_matched_profiles('["security", "critical"]')
        assert profiles == ["security", "critical"]

    def test_parse_empty_array(self):
        """Test parsing empty JSON array."""
        profiles = DigestCollector._parse_matched_profiles("[]")
        assert profiles == []

    def test_parse_none(self):
        """Test parsing None value."""
        profiles = DigestCollector._parse_matched_profiles(None)
        assert profiles == []

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        profiles = DigestCollector._parse_matched_profiles("{invalid json")
        assert profiles == []

    def test_parse_non_array(self):
        """Test parsing JSON that's not an array."""
        profiles = DigestCollector._parse_matched_profiles('{"key": "value"}')
        assert profiles == []
