"""
FormatterContext: Unified context builder for message formatting.

This module provides a centralized way to build formatting context dictionaries
used by both the preview API and the actual renderer functions. It eliminates
duplicate context-building logic scattered across renderer.py and api.py.

Usage:
    # From a DeliveryPayload or similar raw data
    ctx = FormatterContext.from_payload(
        format_type="dm_alerts",
        chat_title="My Channel",
        message_text="Hello world",
        sender_name="John",
        score=0.85,
    )
    variables = ctx.build()  # Returns dict ready for render_template()

    # From sample data for previews
    ctx = FormatterContext.from_sample("dm_alerts")
    variables = ctx.build()
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .defaults import SAMPLE_DATA
from .line_builder import FormattedLineConfig, apply_formatted_lines, get_line_config


def _truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _format_triggers(triggers: Union[List[str], List[tuple], str, None]) -> str:
    """Format triggers list into a display string.

    Args:
        triggers: List of trigger strings or (type, text) tuples, or comma-string

    Returns:
        Formatted trigger string with icons
    """
    if not triggers:
        return ""

    # Handle comma-separated string
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",") if t.strip()]

    if not triggers:
        return ""

    formatted = []
    for trigger in triggers:
        if isinstance(trigger, tuple) and len(trigger) == 2:
            trigger_type, trigger_text = trigger
            # Use default icon for now
            formatted.append(f"🔑 {trigger_text}")
        else:
            formatted.append(f"🔑 {trigger}")

    return ", ".join(formatted)


@dataclass
class FormatterContext:
    """Builder for message formatting context.

    This class provides a fluent interface for building the variables dictionary
    needed by render_template(). It handles:
    - Optional field inclusion (only adds if not None)
    - Trigger formatting (list to string)
    - Message preview generation (truncation)
    - Pre-formatted line generation via apply_formatted_lines()

    Attributes:
        format_type: The type of format (dm_alerts, saved_messages, digest_entry, etc.)
        variables: The accumulated variables dictionary
    """

    format_type: str
    variables: Dict[str, Any] = field(default_factory=dict)

    # Core message data
    chat_title: Optional[str] = None
    message_text: Optional[str] = None
    chat_id: Optional[Union[str, int]] = None
    msg_id: Optional[Union[str, int]] = None
    message_link: Optional[str] = None
    timestamp: Optional[str] = None

    # Sender information
    sender_name: Optional[str] = None
    sender_id: Optional[Union[str, int]] = None
    is_vip: Optional[bool] = None

    # Profile matching
    profile_name: Optional[str] = None
    profile_id: Optional[str] = None

    # Scoring
    score: Optional[float] = None
    keyword_score: Optional[float] = None
    semantic_score: Optional[float] = None
    rank: Optional[int] = None
    reactions: Optional[int] = None

    # Triggers
    triggers: Optional[Union[List[str], List[tuple], str]] = None

    # Digest-specific
    digest_type: Optional[str] = None
    top_n: Optional[int] = None
    channel_count: Optional[int] = None
    schedule: Optional[str] = None
    time_range: Optional[str] = None

    # Preview length
    preview_max_length: int = 200

    @classmethod
    def from_payload(
        cls,
        format_type: str,
        chat_title: Optional[str] = None,
        message_text: Optional[str] = None,
        sender_name: Optional[str] = None,
        sender_id: Optional[Union[str, int]] = None,
        score: Optional[float] = None,
        keyword_score: Optional[float] = None,
        semantic_score: Optional[float] = None,
        profile_name: Optional[str] = None,
        profile_id: Optional[str] = None,
        triggers: Optional[Union[List[str], List[tuple], str]] = None,
        timestamp: Optional[str] = None,
        message_link: Optional[str] = None,
        chat_id: Optional[Union[str, int]] = None,
        msg_id: Optional[Union[str, int]] = None,
        reactions: Optional[int] = None,
        is_vip: Optional[bool] = None,
        rank: Optional[int] = None,
        digest_type: Optional[str] = None,
        top_n: Optional[int] = None,
        channel_count: Optional[int] = None,
        schedule: Optional[str] = None,
        time_range: Optional[str] = None,
        **extra_fields: Any,
    ) -> "FormatterContext":
        """Create a FormatterContext from raw payload data.

        This is the primary factory method for building context from actual
        message data (DeliveryPayload, database rows, etc.).

        Args:
            format_type: The format type (dm_alerts, saved_messages, etc.)
            **kwargs: All the message/sender/scoring fields

        Returns:
            FormatterContext ready to build()
        """
        ctx = cls(format_type=format_type)

        # Set all provided fields
        ctx.chat_title = chat_title
        ctx.message_text = message_text
        ctx.sender_name = sender_name
        ctx.sender_id = sender_id
        ctx.score = score
        ctx.keyword_score = keyword_score
        ctx.semantic_score = semantic_score
        ctx.profile_name = profile_name
        ctx.profile_id = profile_id
        ctx.triggers = triggers
        ctx.timestamp = timestamp
        ctx.message_link = message_link
        ctx.chat_id = chat_id
        ctx.msg_id = msg_id
        ctx.reactions = reactions
        ctx.is_vip = is_vip
        ctx.rank = rank
        ctx.digest_type = digest_type
        ctx.top_n = top_n
        ctx.channel_count = channel_count
        ctx.schedule = schedule
        ctx.time_range = time_range

        # Store any extra fields directly
        for key, value in extra_fields.items():
            ctx.variables[key] = value

        return ctx

    @classmethod
    def from_sample(
        cls,
        format_type: str,
        custom_sample: Optional[Dict[str, Any]] = None,
    ) -> "FormatterContext":
        """Create a FormatterContext from sample data.

        This is used by the preview API to generate example renderings.

        Args:
            format_type: The format type to get sample data for
            custom_sample: Optional custom sample data to override defaults

        Returns:
            FormatterContext populated with sample data
        """
        # Map nested format types (e.g., "digest.header" -> "digest_header")
        sample_key = format_type.replace(".", "_")
        sample = custom_sample or SAMPLE_DATA.get(sample_key, {})

        # Create context from sample data
        ctx = cls.from_payload(
            format_type=format_type,
            chat_title=sample.get("chat_title"),
            message_text=sample.get("message_text"),
            sender_name=sample.get("sender_name"),
            sender_id=sample.get("sender_id"),
            score=sample.get("score"),
            keyword_score=sample.get("keyword_score"),
            semantic_score=sample.get("semantic_score"),
            profile_name=sample.get("profile_name"),
            profile_id=sample.get("profile_id"),
            triggers=sample.get("triggers"),
            timestamp=sample.get("timestamp"),
            message_link=sample.get("message_link"),
            chat_id=sample.get("chat_id"),
            msg_id=sample.get("msg_id"),
            reactions=sample.get("reactions"),
            is_vip=sample.get("is_vip"),
            rank=sample.get("rank"),
            digest_type=sample.get("digest_type"),
            top_n=sample.get("top_n"),
            channel_count=sample.get("channel_count"),
            schedule=sample.get("schedule"),
            time_range=sample.get("time_range"),
        )

        # Also copy triggers_formatted if present in sample
        if "triggers_formatted" in sample:
            ctx.variables["triggers_formatted"] = sample["triggers_formatted"]
        if "triggers_json" in sample:
            ctx.variables["triggers_json"] = sample["triggers_json"]
        if "message_preview" in sample:
            ctx.variables["message_preview"] = sample["message_preview"]

        return ctx

    def build(
        self,
        apply_lines: bool = True,
        line_config: Optional[FormattedLineConfig] = None,
    ) -> Dict[str, Any]:
        """Build the final variables dictionary for rendering.

        This method:
        1. Collects all non-None fields into the variables dict
        2. Generates message_preview if message_text is present
        3. Formats triggers into triggers_formatted
        4. Optionally applies formatted lines (*_line variables)

        Args:
            apply_lines: Whether to apply formatted line building (default: True)
            line_config: Optional custom line config (uses format_type default if None)

        Returns:
            Dict ready for render_template()
        """
        variables = dict(self.variables)

        # Core message fields
        if self.chat_title is not None:
            variables["chat_title"] = self.chat_title
        if self.message_text is not None:
            variables["message_text"] = self.message_text
            # Generate preview if not already present
            if "message_preview" not in variables:
                variables["message_preview"] = _truncate_text(
                    self.message_text, self.preview_max_length
                )
        if self.chat_id is not None:
            variables["chat_id"] = str(self.chat_id)
        if self.msg_id is not None:
            variables["msg_id"] = str(self.msg_id)
        if self.message_link is not None:
            variables["message_link"] = self.message_link
        if self.timestamp is not None:
            variables["timestamp"] = self.timestamp

        # Sender information
        if self.sender_name is not None:
            variables["sender_name"] = self.sender_name
        if self.sender_id is not None:
            variables["sender_id"] = str(self.sender_id)
        if self.is_vip is not None:
            variables["is_vip"] = self.is_vip

        # Profile matching
        if self.profile_name is not None:
            variables["profile_name"] = self.profile_name
        if self.profile_id is not None:
            variables["profile_id"] = self.profile_id

        # Scoring
        if self.score is not None:
            variables["score"] = self.score
        if self.keyword_score is not None:
            variables["keyword_score"] = self.keyword_score
        if self.semantic_score is not None:
            variables["semantic_score"] = self.semantic_score
        if self.rank is not None:
            variables["rank"] = self.rank
        if self.reactions is not None:
            variables["reactions"] = self.reactions

        # Triggers - format and add both raw and formatted
        if self.triggers is not None:
            triggers_formatted = _format_triggers(self.triggers)
            variables["triggers"] = triggers_formatted
            if "triggers_formatted" not in variables:
                variables["triggers_formatted"] = triggers_formatted
            # Also provide JSON format for webhooks
            if "triggers_json" not in variables:
                if isinstance(self.triggers, str):
                    trigger_list = [
                        t.strip() for t in self.triggers.split(",") if t.strip()
                    ]
                else:
                    trigger_list = [
                        t[1] if isinstance(t, tuple) else str(t) for t in self.triggers
                    ]
                variables["triggers_json"] = json.dumps(trigger_list)

        # Digest-specific fields
        if self.digest_type is not None:
            variables["digest_type"] = self.digest_type
        if self.top_n is not None:
            variables["top_n"] = self.top_n
        if self.channel_count is not None:
            variables["channel_count"] = self.channel_count
        if self.schedule is not None:
            variables["schedule"] = self.schedule
        if self.time_range is not None:
            variables["time_range"] = self.time_range

        # Apply formatted lines if requested
        if apply_lines:
            config = line_config or get_line_config(self.format_type.replace(".", "_"))
            apply_formatted_lines(variables, config=config)

        return variables

    def with_field(self, key: str, value: Any) -> "FormatterContext":
        """Add a custom field to the context (fluent interface).

        Args:
            key: Field name
            value: Field value

        Returns:
            self for chaining
        """
        self.variables[key] = value
        return self


def build_context(
    format_type: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience function to build a context dict in one call.

    Args:
        format_type: The format type (dm_alerts, saved_messages, etc.)
        **kwargs: All message/sender/scoring fields

    Returns:
        Dict ready for render_template()
    """
    return FormatterContext.from_payload(format_type, **kwargs).build()


def build_sample_context(
    format_type: str,
    custom_sample: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience function to build sample context for previews.

    Args:
        format_type: The format type
        custom_sample: Optional custom sample data

    Returns:
        Dict with sample data ready for render_template()
    """
    return FormatterContext.from_sample(format_type, custom_sample).build()
