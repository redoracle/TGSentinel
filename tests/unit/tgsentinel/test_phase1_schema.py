"""
Unit tests for Phase 1 database schema changes

Tests that the new tables and columns are created correctly:
- profile_adjustments table
- semantic_score column in feedback table
"""

import pytest
from sqlalchemy import text

from tgsentinel.store import init_db


@pytest.mark.unit
class TestPhase1DatabaseSchema:
    """Test Phase 1 database schema additions."""

    def test_profile_adjustments_table_created(self):
        """Verify profile_adjustments table exists with correct schema."""
        engine = init_db("sqlite:///:memory:")

        with engine.connect() as conn:
            # Check table exists
            result = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_adjustments'"
                )
            )
            assert result.fetchone() is not None

            # Check schema
            result = conn.execute(text("PRAGMA table_info(profile_adjustments)"))
            columns = {row[1]: row[2] for row in result.fetchall()}

            # Verify all required columns exist
            assert "profile_id" in columns
            assert "profile_type" in columns
            assert "adjustment_type" in columns
            assert "old_value" in columns
            assert "new_value" in columns
            assert "adjustment_reason" in columns
            assert "feedback_count" in columns
            assert "trigger_chat_id" in columns
            assert "trigger_msg_id" in columns
            assert "created_at" in columns

    def test_profile_adjustments_indexes_created(self):
        """Verify indexes on profile_adjustments table."""
        engine = init_db("sqlite:///:memory:")

        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='index' AND tbl_name='profile_adjustments'
                """
                )
            )
            indexes = [row[0] for row in result.fetchall()]

            # Check our custom indexes exist
            assert any("idx_profile_adjustments_profile" in idx for idx in indexes)
            assert any("idx_profile_adjustments_type" in idx for idx in indexes)

    def test_feedback_semantic_score_column_added(self):
        """Verify semantic_score column added to feedback table."""
        engine = init_db("sqlite:///:memory:")

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(feedback)"))
            columns = {row[1]: row[2] for row in result.fetchall()}

            assert "semantic_score" in columns
            assert columns["semantic_score"] == "REAL"

    def test_profile_adjustments_insert_and_query(self):
        """Test inserting and querying adjustment records."""
        engine = init_db("sqlite:///:memory:")

        with engine.begin() as conn:
            # Insert test adjustment
            conn.execute(
                text(
                    """
                    INSERT INTO profile_adjustments(
                        profile_id, profile_type, adjustment_type,
                        old_value, new_value, adjustment_reason,
                        feedback_count, trigger_chat_id, trigger_msg_id
                    ) VALUES(
                        '3000', 'interest', 'threshold',
                        0.45, 0.55, 'negative_feedback',
                        3, -123, 456
                    )
                """
                )
            )

        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT profile_id, old_value, new_value, feedback_count
                    FROM profile_adjustments
                    WHERE profile_id = '3000'
                """
                )
            )
            row = result.fetchone()

        assert row is not None
        assert row[0] == "3000"
        assert row[1] == 0.45
        assert row[2] == 0.55
        assert row[3] == 3

    def test_feedback_with_semantic_score(self):
        """Test storing feedback with semantic_score."""
        engine = init_db("sqlite:///:memory:")

        with engine.begin() as conn:
            # Insert feedback with semantic_score
            conn.execute(
                text(
                    """
                    INSERT INTO feedback(chat_id, msg_id, label, semantic_score)
                    VALUES(-123, 456, 0, 0.52)
                """
                )
            )

        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT chat_id, msg_id, label, semantic_score
                    FROM feedback
                    WHERE chat_id = -123 AND msg_id = 456
                """
                )
            )
            row = result.fetchone()

        assert row is not None
        assert row[0] == -123
        assert row[1] == 456
        assert row[2] == 0
        assert row[3] == 0.52
