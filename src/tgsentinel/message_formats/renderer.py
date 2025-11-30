"""
Template renderer with safe variable substitution.

Handles rendering of message format templates with proper error handling
and support for format specifiers (e.g., {score:.2f}).

Supports advanced features:
- Optional variables: {?score:.2f} - renders empty string if score is None/missing
- Filters: {timestamp|date}, {timestamp|time}, {timestamp|relative}
- Markdown links: [View]({message_link}) - for Telegram rendering
"""

import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .defaults import TRIGGER_ICONS
from .line_builder import apply_formatted_lines, get_line_config
from .loader import get_format

log = logging.getLogger(__name__)

# Regex to match format placeholders:
# - {var} or {var:.2f} - required variable
# - {?var} or {?var:.2f} - optional variable (empty if missing)
# - {var|filter} - variable with filter (date, time, relative, link)
# - {?var|filter:.2f} - optional with filter and format spec
FORMAT_PATTERN = re.compile(
    r"\{(\?)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\|([a-zA-Z_]+))?(?::([^}]+))?\}"
)


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a timestamp value into a datetime object."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Unix timestamp
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        # Try ISO format first
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ]:
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _format_relative_time(dt: datetime) -> str:
    """Format a datetime as relative time (e.g., '2 hours ago')."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt
    seconds = int(diff.total_seconds())

    if seconds < 0:
        return "just now"
    elif seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days}d ago"
    else:
        return dt.strftime("%b %d")


def _apply_filter(value: Any, filter_name: str) -> str:
    """Apply a filter to a value."""
    if filter_name == "date":
        dt = _parse_timestamp(value)
        if dt:
            return dt.strftime("%b %d, %Y")
        return ""

    elif filter_name == "time":
        dt = _parse_timestamp(value)
        if dt:
            return dt.strftime("%H:%M")
        return ""

    elif filter_name == "datetime":
        dt = _parse_timestamp(value)
        if dt:
            return dt.strftime("%b %d, %Y %H:%M")
        return ""

    elif filter_name == "relative":
        dt = _parse_timestamp(value)
        if dt:
            return _format_relative_time(dt)
        return ""

    elif filter_name == "link":
        # Format as Markdown link for Telegram
        if value:
            return f"[View]({value})"
        return ""

    elif filter_name == "upper":
        return str(value).upper()

    elif filter_name == "lower":
        return str(value).lower()

    elif filter_name == "title":
        return str(value).title()

    else:
        log.warning(f"[MESSAGE-FORMATS] Unknown filter: {filter_name}")
        return str(value)


def render_template(
    template: str,
    variables: dict[str, Any],
    safe: bool = True,
) -> str:
    """
    Render a template string with variable substitution.

    Supports format specifiers like {score:.2f} for numeric formatting.
    When safe=True, unmatched placeholders are left in place.

    Advanced syntax:
    - {?var} - Optional: renders empty string if var is None or missing
    - {var|date} - Filter: formats timestamp as "Jan 15, 2024"
    - {var|time} - Filter: formats timestamp as "14:30"
    - {var|datetime} - Filter: formats as "Jan 15, 2024 14:30"
    - {var|relative} - Filter: formats as "2h ago"
    - {var|link} - Filter: formats URL as Markdown link "[View](url)"
    - {?var|filter:.2f} - Combine optional, filter, and format spec

    Args:
        template: Template string with {variable} placeholders
        variables: Dictionary of variable values
        safe: If True, keep unmatched placeholders; if False, raise error

    Returns:
        Rendered template string

    Raises:
        KeyError: If safe=False and a required variable is missing
        ValueError: If format specifier is invalid
    """
    if not template:
        return ""

    def replace_match(match: re.Match) -> str:
        optional = match.group(1) == "?"
        var_name = match.group(2)
        filter_name = match.group(3)
        format_spec = match.group(4)

        # Check if variable exists
        if var_name not in variables:
            if optional:
                return ""  # Optional variable missing - return empty
            if safe:
                return match.group(0)  # Keep original placeholder
            raise KeyError(f"Missing template variable: {var_name}")

        value = variables[var_name]

        # Handle None values for optional variables
        if value is None:
            if optional:
                return ""
            # For required variables, treat None as empty string
            return ""

        try:
            # Apply filter if specified
            if filter_name:
                value = _apply_filter(value, filter_name)

            # Apply format specifier
            if format_spec:
                return format(value, format_spec)
            else:
                return str(value)
        except (ValueError, TypeError) as e:
            if safe:
                log.warning(
                    "[MESSAGE-FORMATS] Format error, using raw value",
                    extra={
                        "variable": var_name,
                        "filter": filter_name,
                        "format_spec": format_spec,
                        "error": str(e),
                    },
                )
                return str(value) if value is not None else ""
            raise ValueError(f"Invalid format for {var_name}: {e}") from e

    return FORMAT_PATTERN.sub(replace_match, template)


def escape_html(text: str) -> str:
    """Escape HTML entities in text."""
    return html.escape(text, quote=True)


def escape_json_string(text: str) -> str:
    """Escape text for use in JSON string value."""
    return json.dumps(text)[1:-1]  # Remove surrounding quotes


def truncate_text(text: str, max_length: int = 200, suffix: str = "...") -> str:
    """
    Truncate text to max length, respecting word boundaries.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to append when truncated

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text

    truncated = text[: max_length - len(suffix)]
    # Try to break at word boundary
    last_space = truncated.rfind(" ")
    if last_space > max_length // 2:
        truncated = truncated[:last_space]

    return truncated + suffix


def get_trigger_icon(trigger_type: str) -> str:
    """
    Get icon for a trigger type.

    Args:
        trigger_type: One of 'keyword', 'regex', 'semantic', 'phrase', etc.

    Returns:
        Icon string
    """
    return TRIGGER_ICONS.get(trigger_type, TRIGGER_ICONS["default"])


def format_triggers(triggers: list[str | tuple], separator: str = ", ") -> str:
    """
    Format a list of triggers for display.

    Args:
        triggers: List of trigger strings or (type, trigger) tuples
        separator: Separator between triggers

    Returns:
        Formatted trigger string
    """
    if not triggers:
        return ""

    formatted = []
    for trigger in triggers:
        if isinstance(trigger, tuple) and len(trigger) == 2:
            trigger_type, trigger_text = trigger
            icon = get_trigger_icon(trigger_type)
            formatted.append(f"{icon} {trigger_text}")
        else:
            formatted.append(str(trigger))

    return separator.join(formatted)


def render_dm_alert(
    chat_title: str,
    message_text: str,
    sender_name: str | None = None,
    sender_id: str | int | None = None,
    score: float | None = None,
    keyword_score: float | None = None,
    semantic_score: float | None = None,
    profile_name: str | None = None,
    profile_id: str | None = None,
    triggers: list | None = None,
    timestamp: str | None = None,
    message_link: str | None = None,
    chat_id: str | int | None = None,
    msg_id: str | int | None = None,
    reactions: int | None = None,
    is_vip: bool | None = None,
    custom_template: str | None = None,
) -> str:
    """
    Render a DM Notification message.

    Args:
        chat_title: Title of the source channel/chat
        message_text: The message content
        sender_name: Name of the message sender
        sender_id: Numeric ID of the message sender
        score: Combined relevance score (0.0-1.0)
        keyword_score: Score from keyword/heuristic matching
        semantic_score: Score from semantic similarity (AI)
        profile_name: Name of the matching profile
        profile_id: ID of the matching profile
        triggers: List of matched triggers
        timestamp: Message timestamp
        message_link: Link to original message
        chat_id: Chat/channel numeric ID
        msg_id: Message ID within the chat
        reactions: Number of reactions on the message
        is_vip: Whether sender is a VIP
        custom_template: Optional custom template to use

    Returns:
        Rendered message string
    """
    template = custom_template or get_format("dm_alerts")
    if not template:
        # Fallback to hardcoded default
        template = "🔔 {chat_title}\n{message_text}"

    # Generate message preview
    message_preview = truncate_text(message_text, max_length=200)

    # Generate formatted triggers
    triggers_formatted = ""
    if triggers:
        triggers_formatted = format_triggers(triggers)

    variables: dict[str, Any] = {
        "chat_title": chat_title,
        "message_text": message_text,
        "message_preview": message_preview,
    }

    if sender_name is not None:
        variables["sender_name"] = sender_name
    if sender_id is not None:
        variables["sender_id"] = str(sender_id)
    if score is not None:
        variables["score"] = score
    if keyword_score is not None:
        variables["keyword_score"] = keyword_score
    if semantic_score is not None:
        variables["semantic_score"] = semantic_score
    if profile_name is not None:
        variables["profile_name"] = profile_name
    if profile_id is not None:
        variables["profile_id"] = profile_id
    if triggers is not None:
        variables["triggers"] = format_triggers(triggers)
        variables["triggers_formatted"] = triggers_formatted
    if timestamp is not None:
        variables["timestamp"] = timestamp
    if message_link is not None:
        variables["message_link"] = message_link
    if chat_id is not None:
        variables["chat_id"] = str(chat_id)
    if msg_id is not None:
        variables["msg_id"] = str(msg_id)
    if reactions is not None:
        variables["reactions"] = reactions
    if is_vip is not None:
        variables["is_vip"] = "true" if is_vip else "false"

    apply_formatted_lines(variables, config=get_line_config("dm_alerts"))

    return render_template(template, variables)


def render_saved_message(
    chat_title: str,
    message_text: str,
    sender_name: str,
    score: float,
    sender_id: str | int | None = None,
    keyword_score: float | None = None,
    semantic_score: float | None = None,
    profile_name: str | None = None,
    profile_id: str | None = None,
    triggers: list | None = None,
    timestamp: str | None = None,
    message_link: str | None = None,
    chat_id: str | int | None = None,
    msg_id: str | int | None = None,
    reactions: int | None = None,
    is_vip: bool | None = None,
    custom_template: str | None = None,
) -> str:
    """
    Render a message for saving to Saved Messages.

    Args:
        chat_title: Title of the source channel/chat
        message_text: The message content
        sender_name: Name of the message sender
        score: Combined relevance score (0.0-1.0)
        sender_id: Numeric ID of the message sender
        keyword_score: Score from keyword/heuristic matching
        semantic_score: Score from semantic similarity (AI)
        profile_name: Name of the matching profile
        profile_id: ID of the matching profile
        triggers: List of matched triggers
        timestamp: Message timestamp
        message_link: Link to original message
        chat_id: Chat/channel numeric ID
        msg_id: Message ID within the chat
        reactions: Number of reactions on the message
        is_vip: Whether sender is a VIP
        custom_template: Optional custom template to use

    Returns:
        Rendered message string
    """
    template = custom_template or get_format("saved_messages")
    if not template:
        # Fallback to hardcoded default
        template = """**🔔 Alert from {chat_title}**

**Score:** {score:.2f}
**From:** {sender_name}

{message_text}

{triggers_formatted}"""

    # Generate message preview
    message_preview = truncate_text(message_text, max_length=200)

    # Generate formatted triggers
    triggers_formatted = ""
    if triggers:
        triggers_formatted = format_triggers(triggers)

    variables = {
        "chat_title": chat_title,
        "message_text": message_text,
        "message_preview": message_preview,
        "sender_name": sender_name,
        "score": score,
        "triggers_formatted": triggers_formatted,
    }

    if sender_id is not None:
        variables["sender_id"] = str(sender_id)
    if keyword_score is not None:
        variables["keyword_score"] = keyword_score
    if semantic_score is not None:
        variables["semantic_score"] = semantic_score
    if profile_name is not None:
        variables["profile_name"] = profile_name
    if profile_id is not None:
        variables["profile_id"] = profile_id
    if triggers is not None:
        variables["triggers"] = format_triggers(triggers)
    if timestamp is not None:
        variables["timestamp"] = timestamp
    if message_link is not None:
        variables["message_link"] = message_link
    if chat_id is not None:
        variables["chat_id"] = str(chat_id)
    if msg_id is not None:
        variables["msg_id"] = str(msg_id)
    if reactions is not None:
        variables["reactions"] = reactions
    if is_vip is not None:
        variables["is_vip"] = "true" if is_vip else "false"

    apply_formatted_lines(variables, config=get_line_config("saved_messages"))

    return render_template(template, variables)


def render_digest_header(
    top_n: int,
    channel_count: int,
    schedule: str,
    digest_type: str,
    profile_id: str | None = None,
    timestamp: str | None = None,
    time_range: str | None = None,
    custom_template: str | None = None,
) -> str:
    """
    Render the digest header.

    Args:
        top_n: Number of top messages included
        channel_count: Number of unique channels
        schedule: Digest schedule (hourly/daily)
        digest_type: Type of digest (e.g., "Alerts", "Interests", "Alerts Digest", "Interests Digest")
        profile_id: ID of the digest profile
        timestamp: Digest generation timestamp
        time_range: Time range covered (e.g., 'last 24h')
        custom_template: Optional custom template to use

    Returns:
        Rendered header string
    """
    template = custom_template or get_format("digest", "header")
    if not template:
        # Fallback to hardcoded default
        template = (
            "🗞️ **Digest — Top {top_n} messages from {channel_count} channels**\n"
            "📅 {schedule} | Profile: {profile_name}\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        )

    variables = {
        "top_n": top_n,
        "channel_count": channel_count,
        "schedule": schedule,
        "digest_type": digest_type,
    }

    if profile_id is not None:
        variables["profile_id"] = profile_id
    if timestamp is not None:
        variables["timestamp"] = timestamp
    if time_range is not None:
        variables["time_range"] = time_range

    return render_template(template, variables)


def render_digest_entry(
    rank: int,
    chat_title: str,
    message_text: str,
    sender_name: str,
    score: float,
    sender_id: str | int | None = None,
    keyword_score: float | None = None,
    semantic_score: float | None = None,
    triggers: list | None = None,
    timestamp: str | None = None,
    message_link: str | None = None,
    chat_id: str | int | None = None,
    msg_id: str | int | None = None,
    reactions: int | None = None,
    is_vip: bool | None = None,
    profile_name: str | None = None,
    profile_id: str | None = None,
    max_preview_length: int = 200,
    custom_template: str | None = None,
) -> str:
    """
    Render a digest entry.

    Args:
        rank: Message rank (1-based)
        chat_title: Title of the source channel/chat
        message_text: The message content (will be truncated)
        sender_name: Name of the message sender
        score: Combined relevance score (0.0-1.0)
        sender_id: Numeric ID of the message sender
        keyword_score: Score from keyword/heuristic matching
        semantic_score: Score from semantic similarity (AI)
        triggers: List of matched triggers
        timestamp: Message timestamp
        message_link: Link to original message
        chat_id: Chat/channel numeric ID
        msg_id: Message ID within the chat
        reactions: Number of reactions on the message
        is_vip: Whether sender is a VIP
        profile_name: Name of the matching profile
        profile_id: ID of the matching profile
        max_preview_length: Maximum length for message preview
        custom_template: Optional custom template to use

    Returns:
        Rendered entry string
    """
    template = custom_template or get_format("digest", "entry")
    if not template:
        # Fallback to hardcoded default
        template = """{rank}. **{chat_title}** (Score: {score:.2f})
👤 {sender_name}
📝 {message_preview}
{triggers_formatted}
---"""

    # Truncate message for preview
    message_preview = truncate_text(message_text, max_preview_length)

    # Format triggers
    triggers_formatted = ""
    if triggers:
        triggers_formatted = format_triggers(triggers)

    variables = {
        "rank": rank,
        "chat_title": chat_title,
        "message_preview": message_preview,
        "message_text": message_text,
        "sender_name": sender_name,
        "score": score,
    }

    # Format triggers once
    if triggers:
        variables["triggers"] = format_triggers(triggers)
        variables["triggers_formatted"] = triggers_formatted

    # Basic optional fields
    if sender_id is not None:
        variables["sender_id"] = str(sender_id)
    if keyword_score is not None:
        variables["keyword_score"] = keyword_score
    if semantic_score is not None:
        variables["semantic_score"] = semantic_score
    if chat_id is not None:
        variables["chat_id"] = str(chat_id)
    if msg_id is not None:
        variables["msg_id"] = str(msg_id)
    if is_vip is not None:
        variables["is_vip"] = "true" if is_vip else "false"

    # Profile fields
    if profile_name is not None:
        variables["profile_name"] = profile_name
    if profile_id is not None:
        variables["profile_id"] = profile_id

    # Message link (set both individual and formatted line)
    if message_link:
        variables["message_link"] = message_link

    # Reactions (set both individual count and formatted line)
    if reactions is not None:
        variables["reactions"] = reactions

    # Timestamp
    if timestamp is not None:
        variables["timestamp"] = timestamp

    apply_formatted_lines(variables, config=get_line_config("digest_entry"))

    return render_template(template, variables)


def render_webhook_payload(
    chat_title: str,
    message_text: str,
    sender_name: str,
    score: float,
    profile_name: str,
    sender_id: str | int | None = None,
    keyword_score: float | None = None,
    semantic_score: float | None = None,
    profile_id: str | None = None,
    triggers: list | None = None,
    timestamp: str | None = None,
    message_link: str | None = None,
    chat_id: str | int | None = None,
    msg_id: str | int | None = None,
    reactions: int | None = None,
    is_vip: bool | None = None,
    custom_template: str | None = None,
) -> str:
    """
    Render a webhook JSON payload.

    Note: For JSON payloads, consider using render_webhook_payload_dict()
    instead for proper JSON handling.

    Args:
        chat_title: Title of the source channel/chat
        message_text: The message content
        sender_name: Name of the message sender
        score: Combined relevance score (0.0-1.0)
        profile_name: Name of the matching profile
        sender_id: Numeric ID of the message sender
        keyword_score: Score from keyword/heuristic matching
        semantic_score: Score from semantic similarity (AI)
        profile_id: ID of the matching profile
        triggers: List of matched triggers
        timestamp: Message timestamp (ISO 8601)
        message_link: Link to original message
        chat_id: Chat/channel numeric ID
        msg_id: Message ID within the chat
        reactions: Number of reactions on the message
        is_vip: Whether sender is a VIP
        custom_template: Optional custom template to use

    Returns:
        Rendered JSON string
    """
    template = custom_template or get_format("webhook_payload")

    # Generate message preview
    message_preview = truncate_text(message_text, max_length=200)

    # Generate formatted triggers
    triggers_formatted = ""
    trigger_list = []
    if triggers:
        triggers_formatted = format_triggers(triggers)
        # Extract trigger text for JSON (consistent with format_triggers)
        for t in triggers:
            if isinstance(t, tuple) and len(t) == 2:
                trigger_list.append(t[1])  # Use trigger text from tuple
            elif isinstance(t, tuple) and len(t) > 1:
                trigger_list.append(str(t[1]))  # Extract text (second element)
            else:
                trigger_list.append(str(t))

    # Escape strings for JSON
    variables = {
        "chat_title": escape_json_string(chat_title),
        "message_text": escape_json_string(message_text),
        "message_preview": escape_json_string(message_preview),
        "sender_name": escape_json_string(sender_name),
        "score": score,
        "profile_name": escape_json_string(profile_name),
        "triggers_json": json.dumps(trigger_list),
        "triggers": ", ".join(trigger_list),
        "triggers_formatted": escape_json_string(triggers_formatted),
        "timestamp": timestamp or "",
    }

    if sender_id is not None:
        variables["sender_id"] = str(sender_id)
    if keyword_score is not None:
        variables["keyword_score"] = keyword_score
    if semantic_score is not None:
        variables["semantic_score"] = semantic_score
    if profile_id is not None:
        variables["profile_id"] = escape_json_string(profile_id)
    if message_link is not None:
        variables["message_link"] = escape_json_string(message_link)
    if chat_id is not None:
        variables["chat_id"] = str(chat_id)
    if msg_id is not None:
        variables["msg_id"] = str(msg_id)
    if reactions is not None:
        variables["reactions"] = reactions
    if is_vip is not None:
        variables["is_vip"] = "true" if is_vip else "false"

    return render_template(template, variables)


def render_webhook_payload_dict(
    chat_title: str,
    message_text: str,
    sender_name: str,
    score: float,
    profile_name: str,
    sender_id: str | int | None = None,
    keyword_score: float | None = None,
    semantic_score: float | None = None,
    profile_id: str | None = None,
    triggers: list | None = None,
    timestamp: str | None = None,
    message_link: str | None = None,
    chat_id: str | int | None = None,
    msg_id: str | int | None = None,
    reactions: int | None = None,
    is_vip: bool | None = None,
    extra_fields: dict | None = None,
) -> dict[str, Any]:
    """
    Build a webhook payload as a dictionary.

    This is the preferred method for webhook payloads as it ensures
    proper JSON serialization.

    Args:
        chat_title: Title of the source channel/chat
        message_text: The message content
        sender_name: Name of the message sender
        score: Combined relevance score (0.0-1.0)
        profile_name: Name of the matching profile
        sender_id: Numeric ID of the message sender
        keyword_score: Score from keyword/heuristic matching
        semantic_score: Score from semantic similarity (AI)
        profile_id: ID of the matching profile
        triggers: List of matched triggers
        timestamp: Message timestamp (ISO 8601)
        message_link: Link to original message
        chat_id: Chat/channel numeric ID
        msg_id: Message ID within the chat
        reactions: Number of reactions on the message
        is_vip: Whether sender is a VIP
        extra_fields: Additional fields to include

    Returns:
        Webhook payload dictionary
    """
    payload = {
        "event": "alert",
        "chat_title": chat_title,
        "sender_name": sender_name,
        "message_text": message_text,
        "score": score,
        "profile_name": profile_name,
        "triggers": triggers or [],
    }

    if sender_id is not None:
        payload["sender_id"] = str(sender_id)
    if keyword_score is not None:
        payload["keyword_score"] = keyword_score
    if semantic_score is not None:
        payload["semantic_score"] = semantic_score
    if profile_id is not None:
        payload["profile_id"] = profile_id
    if timestamp:
        payload["timestamp"] = timestamp
    if message_link:
        payload["message_link"] = message_link
    if chat_id is not None:
        payload["chat_id"] = str(chat_id)
    if msg_id is not None:
        payload["msg_id"] = str(msg_id)
    if reactions is not None:
        payload["reactions"] = reactions
    if is_vip is not None:
        payload["is_vip"] = is_vip

    if extra_fields:
        payload.update(extra_fields)

    return payload
