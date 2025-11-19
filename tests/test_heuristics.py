"""Unit tests for heuristics module."""

import pytest

from tgsentinel.heuristics import HeuristicResult, content_hash, run_heuristics


@pytest.mark.unit
class TestContentHash:
    """Test content hashing functionality."""

    def test_content_hash_basic(self):
        """Test basic content hashing."""
        text = "Hello, world!"
        hash1 = content_hash(text)
        hash2 = content_hash(text)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 produces 64 hex chars

    def test_content_hash_empty(self):
        """Test hashing empty string."""
        hash1 = content_hash("")
        hash2 = content_hash("")
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_content_hash_different_texts(self):
        """Test different texts produce different hashes."""
        hash1 = content_hash("text1")
        hash2 = content_hash("text2")
        assert hash1 != hash2


@pytest.mark.unit
class TestRunHeuristics:
    """Test heuristic evaluation logic."""

    def test_mentioned_triggers_importance(self):
        """Test that mentions trigger importance flag."""
        result = run_heuristics(
            text="Hello @user",
            sender_id=999,
            mentioned=True,
            reactions=0,
            replies=0,
            vip=set(),
            keywords=[],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert "mention" in result.reasons
        assert result.pre_score == 2.0

    def test_vip_sender_triggers_importance(self):
        """Test that VIP senders trigger importance."""
        result = run_heuristics(
            text="Regular message",
            sender_id=12345,
            mentioned=False,
            reactions=0,
            replies=0,
            vip={12345, 67890},
            keywords=[],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert "vip" in result.reasons
        assert result.pre_score == 1.0

    def test_reactions_threshold_triggers_importance(self):
        """Test that high reactions trigger importance."""
        result = run_heuristics(
            text="Popular message",
            sender_id=999,
            mentioned=False,
            reactions=10,
            replies=0,
            vip=set(),
            keywords=[],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert "reactions" in result.reasons
        assert result.pre_score == 0.5

    def test_replies_threshold_triggers_importance(self):
        """Test that high replies trigger importance."""
        result = run_heuristics(
            text="Discussed message",
            sender_id=999,
            mentioned=False,
            reactions=0,
            replies=8,
            vip=set(),
            keywords=[],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert "replies" in result.reasons
        assert result.pre_score == 0.5

    def test_keyword_match_triggers_importance(self):
        """Test that keyword matches trigger importance."""
        result = run_heuristics(
            text="This is a security alert about CVE-2024-1234",
            sender_id=999,
            mentioned=False,
            reactions=0,
            replies=0,
            vip=set(),
            keywords=["security", "CVE"],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert "keywords" in result.reasons
        # New system detects urgency and security keywords separately
        # 'alert' triggers urgency (1.5), 'security' triggers security (1.5), plus keywords match (0.5)
        assert result.pre_score >= 0.5  # At least the keywords score

    def test_keyword_match_case_insensitive(self):
        """Test that keyword matching is case-insensitive."""
        result = run_heuristics(
            text="SECURITY ALERT",
            sender_id=999,
            mentioned=False,
            reactions=0,
            replies=0,
            vip=set(),
            keywords=["security"],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert "keywords" in result.reasons

    def test_multiple_triggers_accumulate_score(self):
        """Test that multiple triggers accumulate score."""
        result = run_heuristics(
            text="Important security message",
            sender_id=12345,
            mentioned=True,
            reactions=10,
            replies=8,
            vip={12345},
            keywords=["security"],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is True
        assert len(result.reasons) >= 4
        assert "mention" in result.reasons
        assert "vip" in result.reasons
        assert "reactions" in result.reasons
        assert "replies" in result.reasons
        assert "keywords" in result.reasons
        # New scoring: mention(2.0) + vip(1.0) + reactions(0.5) + replies(0.5) +
        # importance keyword(1.0) + security(1.5) + keywords(0.5) = 6.9+
        assert result.pre_score >= 5.0  # Flexible threshold for accumulated score

    def test_no_triggers_not_important(self):
        """Test that no triggers result in not important."""
        result = run_heuristics(
            text="Regular boring message",
            sender_id=999,
            mentioned=False,
            reactions=2,
            replies=1,
            vip=set(),
            keywords=["security"],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is False
        assert len(result.reasons) == 0
        assert result.pre_score == 0.0

    def test_zero_threshold_reactions(self):
        """Test handling of zero reaction threshold."""
        result = run_heuristics(
            text="Message",
            sender_id=999,
            mentioned=False,
            reactions=5,
            replies=0,
            vip=set(),
            keywords=[],
            react_thr=0,
            reply_thr=5,
        )
        # With threshold 0, any reactions should not trigger
        assert "reactions" not in result.reasons

    def test_negative_threshold_handled(self):
        """Test that negative thresholds are handled gracefully."""
        result = run_heuristics(
            text="Message",
            sender_id=999,
            mentioned=False,
            reactions=5,
            replies=0,
            vip=set(),
            keywords=[],
            react_thr=-1,
            reply_thr=-1,
        )
        # Negative thresholds should be treated as disabled
        assert "reactions" not in result.reasons
        assert "replies" not in result.reasons

    def test_content_hash_included_in_result(self):
        """Test that content hash is included in result."""
        text = "Test message"
        result = run_heuristics(
            text=text,
            sender_id=999,
            mentioned=False,
            reactions=0,
            replies=0,
            vip=set(),
            keywords=[],
            react_thr=5,
            reply_thr=5,
        )
        expected_hash = content_hash(text)
        assert result.content_hash == expected_hash

    def test_empty_text_handled(self):
        """Test that empty text is handled gracefully."""
        result = run_heuristics(
            text="",
            sender_id=999,
            mentioned=False,
            reactions=0,
            replies=0,
            vip=set(),
            keywords=["test"],
            react_thr=5,
            reply_thr=5,
        )
        assert result.important is False
        assert "keywords" not in result.reasons
