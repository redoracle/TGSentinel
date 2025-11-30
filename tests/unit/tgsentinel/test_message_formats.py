"""Unit tests for the message_formats module."""

from pathlib import Path
from unittest.mock import patch

from tgsentinel.message_formats import (
    DEFAULT_FORMATS,
    SAMPLE_DATA,
    get_format,
    load_message_formats,
    render_digest_entry,
    render_digest_header,
    render_dm_alert,
    render_saved_message,
    render_template,
    validate_formats,
    validate_template,
)


class TestRenderTemplate:
    """Tests for render_template function."""

    def test_basic_substitution(self):
        """Test basic variable substitution."""
        template = "Hello, {name}!"
        result = render_template(template, {"name": "World"})
        assert result == "Hello, World!"

    def test_multiple_variables(self):
        """Test multiple variable substitution."""
        template = "{greeting}, {name}! Score: {score:.2f}"
        result = render_template(
            template,
            {
                "greeting": "Hello",
                "name": "World",
                "score": 0.95,
            },
        )
        assert result == "Hello, World! Score: 0.95"

    def test_format_specifier(self):
        """Test format specifiers like :.2f."""
        template = "Score: {score:.2f}"
        result = render_template(template, {"score": 0.123456})
        assert result == "Score: 0.12"

    def test_safe_mode_keeps_unmatched(self):
        """Test that safe mode keeps unmatched placeholders."""
        template = "Hello, {name}! {missing}"
        result = render_template(template, {"name": "World"}, safe=True)
        assert result == "Hello, World! {missing}"

    def test_empty_template(self):
        """Test empty template returns empty string."""
        result = render_template("", {"name": "World"})
        assert result == ""

    def test_no_placeholders(self):
        """Test template with no placeholders."""
        template = "Hello, World!"
        result = render_template(template, {})
        assert result == "Hello, World!"


class TestRenderDmAlert:
    """Tests for render_dm_alert function."""

    def test_basic_dm_alert(self):
        """Test basic DM alert rendering with default template."""
        result = render_dm_alert(
            chat_title="Test Channel",
            message_text="Test message content",
        )
        assert "Test Channel" in result
        assert "Test message content" in result
        assert "🔔" in result

    def test_dm_alert_with_score(self):
        """Test DM alert with score."""
        result = render_dm_alert(
            chat_title="Test Channel",
            message_text="Test message",
            score=0.85,
        )
        assert "Test Channel" in result

    def test_dm_alert_custom_template(self):
        """Test DM alert with custom template."""
        result = render_dm_alert(
            chat_title="Test Channel",
            message_text="Test message",
            custom_template="[{chat_title}] {message_text}",
        )
        assert result == "[Test Channel] Test message"


class TestRenderSavedMessage:
    """Tests for render_saved_message function."""

    def test_basic_saved_message(self):
        """Test basic saved message rendering."""
        result = render_saved_message(
            chat_title="Test Channel",
            message_text="Test message content",
            sender_name="TestUser",
            score=0.85,
        )
        assert "Test Channel" in result
        assert "Test message content" in result
        assert "TestUser" in result
        assert "0.85" in result


class TestRenderDigest:
    """Tests for digest rendering functions."""

    def test_digest_header(self):
        """Test digest header rendering."""
        result = render_digest_header(
            top_n=10,
            channel_count=5,
            schedule="daily",
            profile_name="Test Profile",
        )
        assert "10" in result
        assert "5" in result
        assert "daily" in result
        assert "Test Profile" in result

    def test_digest_entry(self):
        """Test digest entry rendering."""
        result = render_digest_entry(
            rank=1,
            chat_title="Test Channel",
            message_text="Test message content that is quite long and should be truncated",
            sender_name="TestUser",
            score=0.92,
        )
        assert "1" in result
        assert "Test Channel" in result
        assert "TestUser" in result
        assert "0.92" in result


class TestValidateTemplate:
    """Tests for validate_template function."""

    def test_valid_template(self):
        """Test valid template passes validation."""
        template = "Hello, {name}! Score: {score:.2f}"
        is_valid, errors = validate_template(template)
        assert is_valid
        assert len(errors) == 0

    def test_unclosed_brace(self):
        """Test unclosed brace is detected."""
        template = "Hello, {name"
        is_valid, errors = validate_template(template)
        assert not is_valid
        assert any("Unclosed" in e for e in errors)

    def test_unmatched_closing_brace(self):
        """Test unmatched closing brace is detected."""
        template = "Hello, name}"
        is_valid, errors = validate_template(template)
        assert not is_valid
        assert any("Unmatched" in e for e in errors)

    def test_empty_template(self):
        """Test empty template fails validation."""
        is_valid, errors = validate_template("")
        assert not is_valid
        assert any("empty" in e.lower() for e in errors)


class TestValidateFormats:
    """Tests for validate_formats function."""

    def test_valid_formats(self):
        """Test valid formats pass validation."""
        is_valid, errors = validate_formats(DEFAULT_FORMATS)
        assert is_valid
        assert len(errors) == 0

    def test_empty_formats(self):
        """Test empty formats dict passes (uses defaults)."""
        is_valid, errors = validate_formats({})
        assert is_valid

    def test_invalid_type(self):
        """Test non-dict formats fails."""
        is_valid, errors = validate_formats([])  # type: ignore
        assert not is_valid


class TestLoadAndSaveFormats:
    """Tests for format loading and saving."""

    def test_load_defaults_when_no_file(self):
        """Test loading defaults when config file doesn't exist."""
        with patch("tgsentinel.message_formats.loader.get_formats_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/path.yml")
            formats = load_message_formats(force_reload=True)
            assert "dm_alerts" in formats
            assert "saved_messages" in formats
            assert "digest" in formats
            assert "webhook_payload" in formats

    def test_get_format_dm_alerts(self):
        """Test getting DM alerts format."""
        with patch("tgsentinel.message_formats.loader.get_formats_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/path.yml")
            template = get_format("dm_alerts")
            assert "{chat_title}" in template
            assert "{message_text}" in template


class TestSampleData:
    """Tests for sample data."""

    def test_sample_data_exists(self):
        """Test that sample data exists for all format types."""
        assert "dm_alerts" in SAMPLE_DATA
        assert "saved_messages" in SAMPLE_DATA
        assert "digest_header" in SAMPLE_DATA
        assert "digest_entry" in SAMPLE_DATA
        assert "webhook_payload" in SAMPLE_DATA

    def test_sample_data_renders(self):
        """Test that sample data can be used to render templates."""
        template = DEFAULT_FORMATS["dm_alerts"]["template"]
        result = render_template(template, SAMPLE_DATA["dm_alerts"])
        assert len(result) > 0
        assert "{" not in result  # All placeholders should be filled
