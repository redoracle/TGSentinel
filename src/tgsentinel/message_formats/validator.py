"""
Message format validator.

Validates template syntax, required variables, and format structure.
"""

import logging
import re
from typing import Any

from .defaults import DEFAULT_FORMATS

log = logging.getLogger(__name__)

# Regex to extract variable names from templates
VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]+)?\}")

# Required format types and their structure
REQUIRED_STRUCTURE = {
    "dm_alerts": {"template": str, "description": str, "variables": dict},
    "saved_messages": {"template": str, "description": str, "variables": dict},
    "digest": {
        "header": {"template": str, "description": str, "variables": dict},
        "entry": {"template": str, "description": str, "variables": dict},
        "trigger_format": {"template": str, "description": str, "variables": dict},
    },
    "webhook_payload": {"template": str, "description": str, "variables": dict},
}


class ValidationError(Exception):
    """Raised when format validation fails."""

    def __init__(self, message: str, path: str = "", details: list | None = None):
        self.message = message
        self.path = path
        self.details = details or []
        super().__init__(f"{path}: {message}" if path else message)


def extract_variables(template: str) -> set[str]:
    """
    Extract variable names from a template string.

    Args:
        template: Template string with {variable} placeholders

    Returns:
        Set of variable names
    """
    return set(VARIABLE_PATTERN.findall(template))


def validate_template(
    template: str,
    expected_variables: set[str] | None = None,
    allow_extra: bool = True,
) -> tuple[bool, list[str]]:
    """
    Validate a template string.

    Args:
        template: Template string to validate
        expected_variables: Optional set of expected variable names
        allow_extra: If True, allow extra variables not in expected set

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    if not template:
        errors.append("Template is empty")
        return False, errors

    # Check for unclosed braces
    brace_count = 0
    for i, char in enumerate(template):
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
        if brace_count < 0:
            errors.append(f"Unmatched closing brace at position {i}")
            brace_count = 0

    if brace_count > 0:
        errors.append("Unclosed opening brace(s)")

    # Check for invalid variable names
    # Look for patterns like {123abc} which are invalid
    invalid_vars = re.findall(r"\{(\d[^}]*)\}", template)
    for var in invalid_vars:
        errors.append(f"Invalid variable name starting with digit: {var}")

    # Extract and validate variables
    found_variables = extract_variables(template)

    if expected_variables is not None:
        # Check for missing required variables
        missing = expected_variables - found_variables
        if missing:
            errors.append(f"Missing required variables: {', '.join(sorted(missing))}")

        # Check for unexpected variables
        if not allow_extra:
            extra = found_variables - expected_variables
            if extra:
                errors.append(f"Unexpected variables: {', '.join(sorted(extra))}")

    # Validate format specifiers
    format_specs = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*:([^}]+)\}", template)
    for spec in format_specs:
        try:
            # Try to validate the format spec
            format(1.0, spec)  # Test with a float
        except ValueError:
            try:
                format("test", spec)  # Test with a string
            except ValueError:
                errors.append(f"Invalid format specifier: {spec}")

    return len(errors) == 0, errors


def validate_format_section(
    section: dict[str, Any],
    path: str,
) -> list[str]:
    """
    Validate a format section (template + description + variables).

    Args:
        section: Section dictionary to validate
        path: Path for error messages (e.g., "dm_alerts")

    Returns:
        List of error messages
    """
    errors = []

    if not isinstance(section, dict):
        errors.append(f"{path}: Expected dict, got {type(section).__name__}")
        return errors

    # Check for required template field
    if "template" not in section:
        errors.append(f"{path}: Missing 'template' field")
    elif not isinstance(section["template"], str):
        errors.append(
            f"{path}.template: Expected string, got {type(section['template']).__name__}"
        )
    else:
        # Validate template syntax
        is_valid, template_errors = validate_template(section["template"])
        for err in template_errors:
            errors.append(f"{path}.template: {err}")

    # Check optional fields
    if "description" in section and not isinstance(section["description"], str):
        errors.append(
            f"{path}.description: Expected string, got {type(section['description']).__name__}"
        )

    if "variables" in section and not isinstance(section["variables"], dict):
        errors.append(
            f"{path}.variables: Expected dict, got {type(section['variables']).__name__}"
        )

    return errors


def validate_formats(formats: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate a complete message formats dictionary.

    Args:
        formats: Message formats dictionary to validate

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    if not isinstance(formats, dict):
        return False, ["Formats must be a dictionary"]

    # Validate dm_alerts
    if "dm_alerts" in formats:
        errors.extend(validate_format_section(formats["dm_alerts"], "dm_alerts"))

    # Validate saved_messages
    if "saved_messages" in formats:
        errors.extend(
            validate_format_section(formats["saved_messages"], "saved_messages")
        )

    # Validate digest (nested structure)
    if "digest" in formats:
        digest = formats["digest"]
        if not isinstance(digest, dict):
            errors.append(f"digest: Expected dict, got {type(digest).__name__}")
        else:
            for subtype in ["header", "entry", "trigger_format"]:
                if subtype in digest:
                    errors.extend(
                        validate_format_section(
                            digest[subtype],
                            f"digest.{subtype}",
                        )
                    )

    # Validate webhook_payload
    if "webhook_payload" in formats:
        webhook = formats["webhook_payload"]
        errors.extend(validate_format_section(webhook, "webhook_payload"))

        # Additional validation: try to parse as JSON template
        if isinstance(webhook, dict) and "template" in webhook:
            template = webhook["template"]
            if isinstance(template, str):
                # Check if it looks like JSON
                stripped = template.strip()
                if stripped.startswith("{") and stripped.endswith("}"):
                    # Validate JSON structure (with placeholder markers)
                    # For JSON templates, we need smarter substitution:
                    # - "{var}" (quoted) -> "test" (string value)
                    # - {var} (unquoted) -> 0 (number placeholder)
                    # - {var_json} (typically for arrays) -> []
                    import re

                    # Replace quoted placeholders with string values
                    test_json = re.sub(
                        r'"\{[a-zA-Z_][a-zA-Z0-9_]*\}"', '"test"', stripped
                    )
                    # Replace unquoted placeholders for JSON arrays/objects with valid JSON
                    test_json = re.sub(
                        r"\{([a-zA-Z_][a-zA-Z0-9_]*)_json\}", "[]", test_json
                    )
                    # Replace remaining unquoted placeholders with numbers
                    test_json = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "0", test_json)

                    try:
                        import json

                        json.loads(test_json)
                    except json.JSONDecodeError as e:
                        errors.append(
                            f"webhook_payload.template: Invalid JSON structure: {e}"
                        )

    return len(errors) == 0, errors


def validate_and_merge(
    user_formats: dict[str, Any],
    strict: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """
    Validate user formats and merge with defaults.

    Args:
        user_formats: User-provided format dictionary
        strict: If True, fail on any validation error

    Returns:
        Tuple of (merged formats, list of warnings/errors)
    """
    from .loader import _deep_merge

    warnings = []

    # Validate user formats
    is_valid, errors = validate_formats(user_formats)

    if not is_valid:
        if strict:
            raise ValidationError(
                "Format validation failed",
                details=errors,
            )
        warnings.extend([f"Warning: {e}" for e in errors])

    # Merge with defaults
    merged = _deep_merge(DEFAULT_FORMATS, user_formats)

    return merged, warnings


def get_required_variables(format_type: str, subtype: str | None = None) -> set[str]:
    """
    Get the set of required variables for a format type.

    Based on the default format templates.

    Args:
        format_type: One of 'dm_alerts', 'saved_messages', 'digest', 'webhook_payload'
        subtype: For digest formats, one of 'header', 'entry', 'trigger_format'

    Returns:
        Set of required variable names
    """
    try:
        if subtype:
            template = DEFAULT_FORMATS[format_type][subtype]["template"]
        else:
            template = DEFAULT_FORMATS[format_type]["template"]
        return extract_variables(template)
    except (KeyError, TypeError):
        return set()
