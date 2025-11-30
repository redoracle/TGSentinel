"""
Unit tests for message format renderer variable availability.

Tests verify that all 25 unified variables are properly available
in the appropriate render functions and that templates can access them.
"""

import json

import pytest

from src.tgsentinel.message_formats.renderer import (
    render_digest_entry,
    render_digest_header,
    render_dm_alert,
    render_saved_message,
    render_webhook_payload,
    truncate_text,
)


class TestVariableAvailability:
    """Test that all documented variables are available in render functions."""

    @pytest.fixture
    def common_message_data(self):
        """Common test data for message variables."""
        return {
            "chat_title": "Test Channel",
            "message_text": (
                "This is a test message with some content that should be long enough "
                "to test truncation when we use message_preview variable in templates."
            ),
            "sender_name": "Alice Test",
            "score": 0.85,
            "profile_name": "test-profile",
            "sender_id": 123456789,
            "keyword_score": 0.82,
            "semantic_score": 0.88,
            "profile_id": "test-profile-id",
            "triggers": [("keyword", "bitcoin"), ("semantic", "crypto")],
            "timestamp": "2024-01-15T14:30:00Z",
            "message_link": "https://t.me/c/1234567890/100",
            "chat_id": -1001234567890,
            "msg_id": 100,
            "reactions": 42,
            "is_vip": True,
        }

    @pytest.fixture
    def digest_data(self):
        """Test data for digest-specific variables."""
        return {
            "top_n": 10,
            "channel_count": 5,
            "schedule": "daily",
            "digest_type": "Alerts Digest",
            "profile_id": "market-intel",
            "timestamp": "2024-01-15T18:00:00Z",
            "time_range": "last 24h",
        }

    def test_render_dm_alert_all_variables(self, common_message_data):
        """Test render_dm_alert has all message/sender/scoring/trigger variables."""
        template = """
{message_text}
{message_preview}
{chat_title}
{chat_id}
{msg_id}
{message_link}
{timestamp}
{sender_name}
{sender_id}
{is_vip}
{profile_name}
{profile_id}
{score:.2f}
{keyword_score:.2f}
{semantic_score:.2f}
{reactions}
{triggers}
{triggers_formatted}
"""
        result = render_dm_alert(**common_message_data, custom_template=template)

        # Verify all variables were substituted (no {var} placeholders remain)
        assert "{message_text}" not in result
        assert "{message_preview}" not in result
        assert "{chat_title}" not in result
        assert "{chat_id}" not in result
        assert "{msg_id}" not in result
        assert "{message_link}" not in result
        assert "{timestamp}" not in result
        assert "{sender_name}" not in result
        assert "{sender_id}" not in result
        assert "{is_vip}" not in result
        assert "{profile_name}" not in result
        assert "{profile_id}" not in result
        assert "{score" not in result
        assert "{keyword_score" not in result
        assert "{semantic_score" not in result
        assert "{reactions}" not in result
        assert "{triggers}" not in result
        assert "{triggers_formatted}" not in result

        # Verify actual content appears
        assert "Test Channel" in result
        assert "Alice Test" in result
        assert "0.85" in result
        assert "123456789" in result
        assert "true" in result  # is_vip as string

    def test_render_dm_alert_message_preview(self, common_message_data):
        """Test message_preview variable is truncated correctly."""
        template = "{message_preview}"
        result = render_dm_alert(**common_message_data, custom_template=template)

        # Message is 147 chars, under 200 limit, so should NOT be truncated
        assert len(result) <= 200
        # This message doesn't need truncation, so no ellipsis
        assert "This is a test message" in result

    def test_render_dm_alert_triggers_formatted(self, common_message_data):
        """Test triggers_formatted has icons."""
        template = "{triggers_formatted}"
        result = render_dm_alert(**common_message_data, custom_template=template)

        # Should contain trigger text and icons
        assert "bitcoin" in result
        assert "crypto" in result
        # Icons should be present (format_triggers adds them)
        assert result != "bitcoin, crypto"  # Not plain comma-separated

    def test_render_saved_message_all_variables(self, common_message_data):
        """Test render_saved_message has all variables including message_preview."""
        template = """
{message_preview}
{triggers_formatted}
{chat_title}
{sender_name}
{score:.2f}
{is_vip}
"""
        result = render_saved_message(**common_message_data, custom_template=template)

        assert "{message_preview}" not in result
        assert "{triggers_formatted}" not in result
        assert "Test Channel" in result
        assert "Alice Test" in result
        assert "0.85" in result

    def test_render_digest_header_all_variables(self, digest_data):
        """Test render_digest_header has all digest-specific variables."""
        template = """
Top {top_n} from {channel_count} channels
Schedule: {schedule}
Profile ID: {profile_id}
Time: {timestamp}
Range: {time_range}
"""
        result = render_digest_header(**digest_data, custom_template=template)

        assert "{top_n}" not in result
        assert "{channel_count}" not in result
        assert "{schedule}" not in result
        assert "{profile_id}" not in result
        assert "{timestamp}" not in result
        assert "{time_range}" not in result

        assert "Top 10" in result
        assert "5 channels" in result
        assert "daily" in result
        assert "market-intel" in result

    def test_render_digest_entry_all_variables(self, common_message_data):
        """Test render_digest_entry has all variables including is_vip."""
        template = """
{rank}. {chat_title}
{message_preview}
{sender_name} (ID: {sender_id})
Score: {score:.2f}
VIP: {is_vip}
Triggers: {triggers_formatted}
{message_link}
"""
        result = render_digest_entry(
            rank=1,
            chat_title=common_message_data["chat_title"],
            message_text=common_message_data["message_text"],
            sender_name=common_message_data["sender_name"],
            score=common_message_data["score"],
            sender_id=common_message_data["sender_id"],
            keyword_score=common_message_data["keyword_score"],
            semantic_score=common_message_data["semantic_score"],
            triggers=common_message_data["triggers"],
            timestamp=common_message_data["timestamp"],
            message_link=common_message_data["message_link"],
            chat_id=common_message_data["chat_id"],
            msg_id=common_message_data["msg_id"],
            reactions=common_message_data["reactions"],
            is_vip=common_message_data["is_vip"],  # NEW in Phase 2.2
            profile_name=common_message_data["profile_name"],
            profile_id=common_message_data["profile_id"],
            custom_template=template,
        )

        assert "{rank}" not in result
        assert "{message_preview}" not in result
        assert "{sender_id}" not in result
        assert "{is_vip}" not in result
        assert "{triggers_formatted}" not in result

        assert "1." in result
        assert "Test Channel" in result
        assert "123456789" in result
        assert "True" in result or "true" in result  # is_vip

    def test_render_digest_entry_formatted_lines(self, common_message_data):
        """Verify the formatted *_line variables are populated by the helper."""
        template = """
{?profile_line}{?triggers_line}{?semantic_score_line}
{?keyword_score_line}{?message_line}{?reactions_line}
"""
        result = render_digest_entry(
            rank=1,
            chat_title=common_message_data["chat_title"],
            message_text=common_message_data["message_text"],
            sender_name=common_message_data["sender_name"],
            score=common_message_data["score"],
            sender_id=common_message_data["sender_id"],
            keyword_score=common_message_data["keyword_score"],
            semantic_score=common_message_data["semantic_score"],
            triggers=common_message_data["triggers"],
            timestamp=common_message_data["timestamp"],
            message_link=common_message_data["message_link"],
            chat_id=common_message_data["chat_id"],
            msg_id=common_message_data["msg_id"],
            reactions=common_message_data["reactions"],
            is_vip=common_message_data["is_vip"],
            profile_name=common_message_data["profile_name"],
            profile_id=common_message_data["profile_id"],
            custom_template=template,
        )

        assert "{profile_line}" not in result
        assert "{triggers_line}" not in result
        assert "{semantic_score_line}" not in result
        assert "{keyword_score_line}" not in result
        assert "{message_line}" not in result
        assert "{reactions_line}" not in result

        assert "🎯" in result
        assert "⚡" in result
        assert "🧠" in result
        assert "🔑" in result
        assert "📝" in result
        assert "👍" in result

    def test_render_digest_entry_is_vip_parameter(self):
        """Test is_vip parameter is accepted and processed correctly."""
        template = "VIP: {is_vip}"

        # Test with is_vip=True
        result_vip = render_digest_entry(
            rank=1,
            chat_title="Test",
            message_text="Test message",
            sender_name="Alice",
            score=0.9,
            is_vip=True,
            custom_template=template,
        )
        assert "True" in result_vip or "true" in result_vip

        # Test with is_vip=False
        result_not_vip = render_digest_entry(
            rank=1,
            chat_title="Test",
            message_text="Test message",
            sender_name="Bob",
            score=0.9,
            is_vip=False,
            custom_template=template,
        )
        assert "False" in result_not_vip or "false" in result_not_vip

        # Test with is_vip=None (optional)
        result_none = render_digest_entry(
            rank=1,
            chat_title="Test",
            message_text="Test message",
            sender_name="Charlie",
            score=0.9,
            is_vip=None,
            custom_template="VIP: {?is_vip}",  # Optional syntax
        )
        # Should not fail, optional variable missing renders as empty
        assert "{is_vip}" not in result_none

    def test_render_webhook_payload_all_variables(self, common_message_data):
        """Test render_webhook_payload has all variables including message_preview."""
        # Use single braces for JSON template
        template = """{
  "chat_title": "{chat_title}",
  "message_preview": "{message_preview}",
  "sender_id": "{sender_id}",
  "is_vip": {is_vip},
  "triggers_formatted": "{triggers_formatted}",
  "score": {score}
}"""
        result = render_webhook_payload(**common_message_data, custom_template=template)

        # Verify JSON structure
        assert '"chat_title":' in result
        assert '"message_preview":' in result
        assert '"sender_id":' in result
        assert '"is_vip":' in result
        assert '"triggers_formatted":' in result

        # Verify JSON is valid
        parsed = json.loads(result)
        assert parsed["chat_title"] == "Test Channel"
        assert len(parsed["message_preview"]) <= 200
        assert parsed["sender_id"] == "123456789"
        # is_vip is boolean in JSON (True, not "true")
        assert parsed["is_vip"] is True

    def test_optional_variables_syntax(self, common_message_data):
        """Test optional variable syntax {?var} works correctly."""
        # Test with present variable
        template_present = "{?semantic_score:.2f}"
        result_present = render_dm_alert(
            **common_message_data, custom_template=template_present
        )
        assert "0.88" in result_present

        # Test with missing variable (set to None)
        data_missing = common_message_data.copy()
        data_missing["semantic_score"] = None
        result_missing = render_dm_alert(
            **data_missing, custom_template=template_present
        )
        # Should render as empty string, not literal "{?semantic_score:.2f}"
        assert "{" not in result_missing
        assert result_missing.strip() == ""

    def test_variable_filters(self, common_message_data):
        """Test that filter syntax works with variables."""
        template = """
Date: {timestamp|date}
Time: {timestamp|time}
DateTime: {timestamp|datetime}
Link: {message_link|link}
"""
        result = render_dm_alert(**common_message_data, custom_template=template)

        # Verify filters were applied
        assert "Date:" in result
        assert "Time:" in result
        assert "DateTime:" in result
        assert "Link:" in result
        assert "[View]" in result  # |link filter creates markdown link

    def test_no_deprecated_variables(self, common_message_data):
        """Verify deprecated variables are NOT available."""
        # trigger_section should not work
        template_section = "{trigger_section}"
        result_section = render_saved_message(
            **common_message_data, custom_template=template_section
        )
        # Deprecated variable should remain as placeholder (not substituted)
        assert "{trigger_section}" in result_section

        # trigger_display should not work in digest
        template_display = "{trigger_display}"
        result_display = render_digest_entry(
            rank=1,
            chat_title=common_message_data["chat_title"],
            message_text=common_message_data["message_text"],
            sender_name=common_message_data["sender_name"],
            score=common_message_data["score"],
            custom_template=template_display,
        )
        # Deprecated variable should remain as placeholder
        assert "{trigger_display}" in result_display


class TestTruncateText:
    """Test the truncate_text helper function used for message_preview."""

    def test_truncate_short_text(self):
        """Short text should not be truncated."""
        text = "Short message"
        result = truncate_text(text, max_length=200)
        assert result == text
        assert not result.endswith("...")

    def test_truncate_long_text(self):
        """Long text should be truncated with suffix."""
        text = "A" * 300
        result = truncate_text(text, max_length=200)
        assert len(result) == 200
        assert result.endswith("...")

    def test_truncate_word_boundary(self):
        """Should break at word boundary when possible."""
        text = "This is a very long message " * 20
        result = truncate_text(text, max_length=100)
        assert len(result) <= 100
        assert result.endswith("...")
        # Should break at space, not mid-word
        assert not result[:-3].endswith("mess")  # Not mid-"message"

    def test_truncate_custom_suffix(self):
        """Should use custom suffix."""
        text = "A" * 300
        result = truncate_text(text, max_length=100, suffix=" [more]")
        assert result.endswith(" [more]")
        assert len(result) == 100


class TestWebhookTriggerHandling:
    """Test webhook-specific trigger handling (tuple support)."""

    def test_webhook_triggers_plain_strings(self):
        """Webhook should handle plain string triggers."""
        # Use single braces for JSON template
        template = """{
  "triggers": "{triggers}",
  "triggers_json": {triggers_json},
  "triggers_formatted": "{triggers_formatted}"
}"""
        result = render_webhook_payload(
            chat_title="Test",
            message_text="Test",
            sender_name="Alice",
            score=0.9,
            profile_name="test",
            triggers=["bitcoin", "crypto"],
            custom_template=template,
        )

        parsed = json.loads(result)
        assert parsed["triggers"] == "bitcoin, crypto"
        assert parsed["triggers_json"] == ["bitcoin", "crypto"]

    def test_webhook_triggers_tuples(self):
        """Webhook should handle (type, trigger) tuples."""
        # Use single braces for JSON template
        template = """{
  "triggers": "{triggers}",
  "triggers_json": {triggers_json}
}"""
        result = render_webhook_payload(
            chat_title="Test",
            message_text="Test",
            sender_name="Alice",
            score=0.9,
            profile_name="test",
            triggers=[("keyword", "bitcoin"), ("semantic", "crypto")],
            custom_template=template,
        )

        parsed = json.loads(result)
        # Should extract trigger text from tuples
        assert "bitcoin" in parsed["triggers"]
        assert "crypto" in parsed["triggers"]
        assert parsed["triggers_json"] == ["bitcoin", "crypto"]


class TestRenderFunctionSignatures:
    """Test that render functions have correct parameter signatures."""

    def test_render_digest_entry_has_is_vip_parameter(self):
        """Verify render_digest_entry accepts is_vip parameter."""
        import inspect

        sig = inspect.signature(render_digest_entry)
        params = sig.parameters

        # Verify is_vip is in parameters
        assert "is_vip" in params
        assert params["is_vip"].default is None  # Optional parameter

    def test_all_render_functions_have_custom_template(self):
        """All render functions should accept custom_template parameter."""
        import inspect

        functions = [
            render_dm_alert,
            render_saved_message,
            render_digest_header,
            render_digest_entry,
            render_webhook_payload,
        ]

        for func in functions:
            sig = inspect.signature(func)
            assert (
                "custom_template" in sig.parameters
            ), f"{func.__name__} missing custom_template parameter"


class TestVariableSubstitutionEdgeCases:
    """Test edge cases in variable substitution."""

    def test_missing_optional_variables(self):
        """Optional variables should render as empty when missing."""
        template = "{chat_title} {?keyword_score:.2f} {?semantic_score:.2f}"
        result = render_dm_alert(
            chat_title="Test",
            message_text="Test",
            sender_name="Alice",
            score=0.9,
            profile_name="test",
            keyword_score=None,  # Missing
            semantic_score=None,  # Missing
            custom_template=template,
        )

        assert "Test" in result
        # Optional variables should be empty, not literal placeholders
        assert "{keyword_score" not in result
        assert "{semantic_score" not in result

    def test_none_values_handled_gracefully(self):
        """None values should not cause crashes."""
        result = render_dm_alert(
            chat_title="Test",
            message_text="Test message",
            sender_name="Alice",
            score=0.9,
            profile_name="test",
            sender_id=None,
            triggers=None,
            reactions=None,
            is_vip=None,
        )
        # Should not crash
        assert "Test" in result

    def test_empty_triggers_list(self):
        """Empty triggers list should not cause errors."""
        template = "{triggers_formatted}"
        result = render_dm_alert(
            chat_title="Test",
            message_text="Test",
            sender_name="Alice",
            score=0.9,
            profile_name="test",
            triggers=[],
            custom_template=template,
        )
        # Empty triggers should render as empty string
        # The variable is populated but empty
        assert result.strip() == ""
