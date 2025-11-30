"""Helper utilities for formatted line variables in message formats."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FormattedLineConfig:
    """Configuration for how pre-formatted lines should be rendered."""

    line_suffix: str = ""
    profile_indent: str = ""
    message_prefix: str = "📝 "


@dataclass
class LineDiagnostic:
    """Diagnostic information for a single line variable.

    Attributes:
        line_name: Name of the line variable (e.g., "profile_line")
        rendered: Whether the line was rendered
        reason: Reason for the render decision
        source_variable: The source variable checked (e.g., "profile_name")
        source_value: The actual value of the source variable
    """

    line_name: str
    rendered: bool
    reason: str
    source_variable: Optional[str] = None
    source_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "line_name": self.line_name,
            "rendered": self.rendered,
            "reason": self.reason,
            "source_variable": self.source_variable,
            "source_value": self._safe_value(),
        }

    def _safe_value(self) -> Any:
        """Return a JSON-safe representation of the source value."""
        if self.source_value is None:
            return None
        if isinstance(self.source_value, (str, int, float, bool)):
            return self.source_value
        if isinstance(self.source_value, (list, dict)):
            return self.source_value
        return str(self.source_value)


@dataclass
class LineBuildResult:
    """Result of building formatted lines with optional diagnostics.

    Attributes:
        lines: Dict of line_name -> formatted value
        diagnostics: List of diagnostic info for each line
    """

    lines: Dict[str, str] = field(default_factory=dict)
    diagnostics: List[LineDiagnostic] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "lines": self.lines,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "summary": {
                "total_lines": len(self.diagnostics),
                "rendered_count": sum(1 for d in self.diagnostics if d.rendered),
                "skipped_count": sum(1 for d in self.diagnostics if not d.rendered),
            },
        }


DEFAULT_FORMATTED_LINE_CONFIG = FormattedLineConfig()
FORMATTED_LINE_CONFIGS: Dict[str, FormattedLineConfig] = {
    "dm_alerts": DEFAULT_FORMATTED_LINE_CONFIG,
    "saved_messages": DEFAULT_FORMATTED_LINE_CONFIG,
    "digest_entry": FormattedLineConfig(profile_indent="  "),
}


def _to_bool(value: Any) -> bool:
    """Coerce different representations into a boolean value."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _format_score(value: Any) -> str:
    """Format a numeric score with two decimal places."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _should_render_score(value: Any) -> bool:
    """Return True when the numeric score is defined and non-zero."""
    if value is None:
        return False
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return False


def _should_render_reactions(value: Any) -> bool:
    """Return True when reactions is a positive number.

    Safely handles non-numeric inputs by attempting coercion.
    Explicitly excludes bool type to avoid True/False being treated as 1/0.
    """
    if value is None:
        return False
    # Explicitly exclude bool (before numeric checks since bool is subclass of int)
    if isinstance(value, bool):
        return False
    try:
        numeric_value = float(value)
        return numeric_value > 0
    except (TypeError, ValueError):
        return False


def build_formatted_line_values(
    variables: Dict[str, Any],
    *,
    config: FormattedLineConfig = DEFAULT_FORMATTED_LINE_CONFIG,
) -> Dict[str, str]:
    """Build the *_line variables based on the provided data."""

    lines: Dict[str, str] = {}

    profile_name = variables.get("profile_name")
    if profile_name:
        lines["profile_line"] = f"{config.profile_indent}🎯 {profile_name}"

    sender_name = variables.get("sender_name")
    if sender_name:
        lines["sender_line"] = f"👤 {sender_name}"

    is_vip = _to_bool(variables.get("is_vip"))
    if is_vip:
        lines["vip_line"] = "🧘 VIP"

    semantic_score = variables.get("semantic_score")
    if _should_render_score(semantic_score):
        lines["semantic_score_line"] = f"🧠 {_format_score(semantic_score)}"

    keyword_score = variables.get("keyword_score")
    if _should_render_score(keyword_score):
        lines["keyword_score_line"] = f"🔑 {_format_score(keyword_score)}"

    reactions = variables.get("reactions")
    if _should_render_reactions(reactions):
        # Coerce to int for display (already validated as numeric)
        try:
            reactions_value = int(float(reactions or 0))
        except (TypeError, ValueError):
            reactions_value = reactions
        lines["reactions_line"] = f"👍 {reactions_value}"

    triggers_formatted = variables.get("triggers_formatted")
    if triggers_formatted:
        lines["triggers_line"] = f"⚡ {triggers_formatted}"

    message_link = variables.get("message_link")
    if message_link:
        lines["message_link_line"] = f"🔗 [View]({message_link})"

    message_text = variables.get("message_text")
    if message_text:
        lines["message_line"] = f"{config.message_prefix}{message_text}"

    return lines


def build_formatted_lines_with_diagnostics(
    variables: Dict[str, Any],
    *,
    config: FormattedLineConfig = DEFAULT_FORMATTED_LINE_CONFIG,
) -> LineBuildResult:
    """Build formatted lines with diagnostic information.

    This function provides the same output as build_formatted_line_values()
    but also includes detailed diagnostics explaining why each line was
    rendered or skipped.

    Args:
        variables: Dict of variable values to use
        config: Line formatting configuration

    Returns:
        LineBuildResult with lines and diagnostics
    """
    result = LineBuildResult()

    # profile_line
    profile_name = variables.get("profile_name")
    if profile_name:
        result.lines["profile_line"] = f"{config.profile_indent}🎯 {profile_name}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="profile_line",
                rendered=True,
                reason="profile_name is present",
                source_variable="profile_name",
                source_value=profile_name,
            )
        )
    else:
        result.diagnostics.append(
            LineDiagnostic(
                line_name="profile_line",
                rendered=False,
                reason="profile_name is missing or empty",
                source_variable="profile_name",
                source_value=profile_name,
            )
        )

    # sender_line
    sender_name = variables.get("sender_name")
    if sender_name:
        result.lines["sender_line"] = f"👤 {sender_name}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="sender_line",
                rendered=True,
                reason="sender_name is present",
                source_variable="sender_name",
                source_value=sender_name,
            )
        )
    else:
        result.diagnostics.append(
            LineDiagnostic(
                line_name="sender_line",
                rendered=False,
                reason="sender_name is missing or empty",
                source_variable="sender_name",
                source_value=sender_name,
            )
        )

    # vip_line
    is_vip_raw = variables.get("is_vip")
    is_vip = _to_bool(is_vip_raw)
    if is_vip:
        result.lines["vip_line"] = "🧘 VIP"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="vip_line",
                rendered=True,
                reason="is_vip evaluates to True",
                source_variable="is_vip",
                source_value=is_vip_raw,
            )
        )
    else:
        result.diagnostics.append(
            LineDiagnostic(
                line_name="vip_line",
                rendered=False,
                reason="is_vip is False, missing, or invalid",
                source_variable="is_vip",
                source_value=is_vip_raw,
            )
        )

    # semantic_score_line
    semantic_score = variables.get("semantic_score")
    if _should_render_score(semantic_score):
        result.lines["semantic_score_line"] = f"🧠 {_format_score(semantic_score)}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="semantic_score_line",
                rendered=True,
                reason="semantic_score is non-zero",
                source_variable="semantic_score",
                source_value=semantic_score,
            )
        )
    else:
        reason = (
            "semantic_score is None"
            if semantic_score is None
            else "semantic_score is 0"
        )
        result.diagnostics.append(
            LineDiagnostic(
                line_name="semantic_score_line",
                rendered=False,
                reason=reason,
                source_variable="semantic_score",
                source_value=semantic_score,
            )
        )

    # keyword_score_line
    keyword_score = variables.get("keyword_score")
    if _should_render_score(keyword_score):
        result.lines["keyword_score_line"] = f"🔑 {_format_score(keyword_score)}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="keyword_score_line",
                rendered=True,
                reason="keyword_score is non-zero",
                source_variable="keyword_score",
                source_value=keyword_score,
            )
        )
    else:
        reason = (
            "keyword_score is None" if keyword_score is None else "keyword_score is 0"
        )
        result.diagnostics.append(
            LineDiagnostic(
                line_name="keyword_score_line",
                rendered=False,
                reason=reason,
                source_variable="keyword_score",
                source_value=keyword_score,
            )
        )

    # reactions_line
    reactions = variables.get("reactions")
    if _should_render_reactions(reactions):
        # Coerce to int for display (already validated as numeric)
        try:
            reactions_value = int(float(reactions or 0))
        except (TypeError, ValueError):
            reactions_value = reactions
        result.lines["reactions_line"] = f"👍 {reactions_value}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="reactions_line",
                rendered=True,
                reason=f"reactions is {reactions} (coerced to {reactions_value}, > 0)",
                source_variable="reactions",
                source_value=reactions,
            )
        )
    else:
        if reactions is None:
            reason = "reactions is None"
        elif isinstance(reactions, bool):
            reason = f"reactions is bool ({reactions}), excluded"
        else:
            try:
                numeric_value = float(reactions)
                reason = f"reactions is {reactions} (numeric: {numeric_value} <= 0)"
            except (TypeError, ValueError):
                reason = f"reactions is {reactions} (non-numeric, cannot coerce)"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="reactions_line",
                rendered=False,
                reason=reason,
                source_variable="reactions",
                source_value=reactions,
            )
        )

    # triggers_line
    triggers_formatted = variables.get("triggers_formatted")
    if triggers_formatted:
        result.lines["triggers_line"] = f"⚡ {triggers_formatted}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="triggers_line",
                rendered=True,
                reason="triggers_formatted is present",
                source_variable="triggers_formatted",
                source_value=triggers_formatted,
            )
        )
    else:
        result.diagnostics.append(
            LineDiagnostic(
                line_name="triggers_line",
                rendered=False,
                reason="triggers_formatted is missing or empty",
                source_variable="triggers_formatted",
                source_value=triggers_formatted,
            )
        )

    # message_link_line
    message_link = variables.get("message_link")
    if message_link:
        result.lines["message_link_line"] = f"🔗 [View]({message_link})"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="message_link_line",
                rendered=True,
                reason="message_link is present",
                source_variable="message_link",
                source_value=message_link,
            )
        )
    else:
        result.diagnostics.append(
            LineDiagnostic(
                line_name="message_link_line",
                rendered=False,
                reason="message_link is missing or empty",
                source_variable="message_link",
                source_value=message_link,
            )
        )

    # message_line
    message_text = variables.get("message_text")
    if message_text:
        result.lines["message_line"] = f"{config.message_prefix}{message_text}"
        result.diagnostics.append(
            LineDiagnostic(
                line_name="message_line",
                rendered=True,
                reason="message_text is present",
                source_variable="message_text",
                source_value=(
                    message_text[:100] + "..."
                    if len(message_text) > 100
                    else message_text
                ),
            )
        )
    else:
        result.diagnostics.append(
            LineDiagnostic(
                line_name="message_line",
                rendered=False,
                reason="message_text is missing or empty",
                source_variable="message_text",
                source_value=message_text,
            )
        )

    return result


def apply_formatted_lines(
    variables: Dict[str, Any],
    *,
    config: FormattedLineConfig = DEFAULT_FORMATTED_LINE_CONFIG,
) -> None:
    """Ensure the *_line variables exist without overwriting existing values."""

    formatted_lines = build_formatted_line_values(variables, config=config)
    for key, value in formatted_lines.items():
        if value is not None and key not in variables:
            variables[key] = value


def get_line_config(key: str) -> FormattedLineConfig:
    """Get the formatting config for a particular message format."""

    return FORMATTED_LINE_CONFIGS.get(key, DEFAULT_FORMATTED_LINE_CONFIG)
