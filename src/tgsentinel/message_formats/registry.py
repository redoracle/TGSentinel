"""
FormatRegistry: Unified metadata registry for message formats.

This module provides a centralized registry for all format metadata, including:
- Template strings
- Variable definitions and descriptions
- Sample data for previews
- Validation rules

The registry consolidates what was previously spread across DEFAULT_FORMATS
and SAMPLE_DATA, ensuring consistency and enabling auto-discovery of variables.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Pattern to extract variable names from templates
# Matches {var}, {?var}, {var:.2f}, {var|filter}, etc.
VARIABLE_PATTERN = re.compile(
    r"\{(\?)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\|[a-zA-Z_]+)?(?::[^}]+)?\}"
)


@dataclass
class VariableSpec:
    """Specification for a template variable.

    Attributes:
        name: Variable name (e.g., "chat_title")
        description: Human-readable description
        example: Example value for previews
        required: Whether the variable is required (vs optional {?var})
        type_hint: Expected type (str, float, int, bool, list)
        format_hint: Suggested format spec (e.g., ".2f" for floats)
    """

    name: str
    description: str
    example: Any = None
    required: bool = True
    type_hint: str = "str"
    format_hint: Optional[str] = None


@dataclass
class FormatSpec:
    """Specification for a message format.

    Attributes:
        key: Format identifier (e.g., "dm_alerts", "digest_entry")
        template: The template string
        description: Human-readable description of this format
        variables: Dict of variable name -> VariableSpec
        sample_data: Dict of sample values for preview rendering
        category: Format category (alerts, digest, webhook)
    """

    key: str
    template: str
    description: str
    variables: Dict[str, VariableSpec] = field(default_factory=dict)
    sample_data: Dict[str, Any] = field(default_factory=dict)
    category: str = "general"

    def get_variable_names(self) -> Set[str]:
        """Extract all variable names used in the template.

        Returns:
            Set of variable names found in the template
        """
        matches = VARIABLE_PATTERN.findall(self.template)
        return {match[1] for match in matches}

    def get_required_variables(self) -> Set[str]:
        """Extract required (non-optional) variable names from template.

        Returns:
            Set of required variable names (those without ?)
        """
        matches = VARIABLE_PATTERN.findall(self.template)
        return {match[1] for match in matches if not match[0]}

    def get_optional_variables(self) -> Set[str]:
        """Extract optional variable names from template.

        Returns:
            Set of optional variable names (those with ?)
        """
        matches = VARIABLE_PATTERN.findall(self.template)
        return {match[1] for match in matches if match[0]}

    def validate_sample_data(self) -> List[str]:
        """Validate that sample data covers all required variables.

        Returns:
            List of warning messages for missing required variables
        """
        warnings = []
        required = self.get_required_variables()
        sample_keys = set(self.sample_data.keys())

        # Check for missing required variables
        missing = required - sample_keys
        for var in missing:
            warnings.append(f"Missing required sample data for '{var}'")

        return warnings

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses.

        Returns:
            Dict representation of the format spec
        """
        return {
            "key": self.key,
            "template": self.template,
            "description": self.description,
            "category": self.category,
            "variables": {
                name: {
                    "name": spec.name,
                    "description": spec.description,
                    "example": spec.example,
                    "required": spec.required,
                    "type_hint": spec.type_hint,
                    "format_hint": spec.format_hint,
                }
                for name, spec in self.variables.items()
            },
            "extracted_variables": {
                "all": list(self.get_variable_names()),
                "required": list(self.get_required_variables()),
                "optional": list(self.get_optional_variables()),
            },
        }


class FormatRegistry:
    """Central registry for all message format specifications.

    This registry provides:
    - Template storage and retrieval
    - Variable metadata management
    - Sample data for previews
    - Auto-extraction of variables from templates
    - Validation utilities
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._formats: Dict[str, FormatSpec] = {}

    def register(
        self,
        key: str,
        template: str,
        description: str,
        variables: Optional[Dict[str, VariableSpec]] = None,
        sample_data: Optional[Dict[str, Any]] = None,
        category: str = "general",
    ) -> FormatSpec:
        """Register a new format specification.

        Args:
            key: Format identifier
            template: Template string
            description: Human-readable description
            variables: Variable specifications
            sample_data: Sample data for previews
            category: Format category

        Returns:
            The created FormatSpec
        """
        spec = FormatSpec(
            key=key,
            template=template,
            description=description,
            variables=variables or {},
            sample_data=sample_data or {},
            category=category,
        )
        self._formats[key] = spec
        return spec

    def get(self, key: str) -> Optional[FormatSpec]:
        """Get a format specification by key.

        Args:
            key: Format identifier

        Returns:
            FormatSpec or None if not found
        """
        return self._formats.get(key)

    def get_template(self, key: str) -> Optional[str]:
        """Get just the template string for a format.

        Args:
            key: Format identifier

        Returns:
            Template string or None if not found
        """
        spec = self.get(key)
        return spec.template if spec else None

    def get_sample_data(self, key: str) -> Dict[str, Any]:
        """Get sample data for a format.

        Args:
            key: Format identifier

        Returns:
            Sample data dict (empty if not found)
        """
        spec = self.get(key)
        return dict(spec.sample_data) if spec else {}

    def get_variables(self, key: str) -> Dict[str, VariableSpec]:
        """Get variable specifications for a format.

        Args:
            key: Format identifier

        Returns:
            Variables dict (empty if not found)
        """
        spec = self.get(key)
        return dict(spec.variables) if spec else {}

    def list_formats(self, category: Optional[str] = None) -> List[FormatSpec]:
        """List all registered formats.

        Args:
            category: Optional filter by category

        Returns:
            List of FormatSpec objects
        """
        if category:
            return [
                spec for spec in self._formats.values() if spec.category == category
            ]
        return list(self._formats.values())

    def list_keys(self, category: Optional[str] = None) -> List[str]:
        """List all format keys.

        Args:
            category: Optional filter by category

        Returns:
            List of format keys
        """
        if category:
            return [
                key for key, spec in self._formats.items() if spec.category == category
            ]
        return list(self._formats.keys())

    def validate_all(self) -> Dict[str, List[str]]:
        """Validate all registered formats.

        Returns:
            Dict mapping format key to list of warnings
        """
        results = {}
        for key, spec in self._formats.items():
            warnings = spec.validate_sample_data()
            if warnings:
                results[key] = warnings
        return results

    def to_dict(self) -> Dict[str, Any]:
        """Convert entire registry to dictionary.

        Returns:
            Dict representation of all formats
        """
        return {
            "formats": {key: spec.to_dict() for key, spec in self._formats.items()},
            "categories": list(set(spec.category for spec in self._formats.values())),
        }


# Global registry instance
_registry: Optional[FormatRegistry] = None


def get_registry() -> FormatRegistry:
    """Get or create the global format registry.

    Returns:
        The global FormatRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = _build_default_registry()
    return _registry


def _build_default_registry() -> FormatRegistry:
    """Build the default registry from existing DEFAULT_FORMATS and SAMPLE_DATA.

    Returns:
        FormatRegistry populated with default formats
    """
    from .defaults import DEFAULT_FORMATS, SAMPLE_DATA

    registry = FormatRegistry()

    # Register dm_alerts
    if "dm_alerts" in DEFAULT_FORMATS:
        fmt = DEFAULT_FORMATS["dm_alerts"]
        registry.register(
            key="dm_alerts",
            template=fmt["template"],
            description=fmt.get("description", "DM alert format"),
            variables=_convert_variables(fmt.get("variables", {})),
            sample_data=SAMPLE_DATA.get("dm_alerts", {}),
            category="alerts",
        )

    # Register saved_messages
    if "saved_messages" in DEFAULT_FORMATS:
        fmt = DEFAULT_FORMATS["saved_messages"]
        registry.register(
            key="saved_messages",
            template=fmt["template"],
            description=fmt.get("description", "Saved messages format"),
            variables=_convert_variables(fmt.get("variables", {})),
            sample_data=SAMPLE_DATA.get("saved_messages", {}),
            category="alerts",
        )

    # Register digest formats
    if "digest" in DEFAULT_FORMATS:
        digest = DEFAULT_FORMATS["digest"]

        if "header" in digest:
            header = digest["header"]
            registry.register(
                key="digest_header",
                template=header["template"],
                description=header.get("description", "Digest header format"),
                variables=_convert_variables(header.get("variables", {})),
                sample_data=SAMPLE_DATA.get("digest_header", {}),
                category="digest",
            )

        if "entry" in digest:
            entry = digest["entry"]
            registry.register(
                key="digest_entry",
                template=entry["template"],
                description=entry.get("description", "Digest entry format"),
                variables=_convert_variables(entry.get("variables", {})),
                sample_data=SAMPLE_DATA.get("digest_entry", {}),
                category="digest",
            )

    # Register webhook_payload
    if "webhook_payload" in DEFAULT_FORMATS:
        fmt = DEFAULT_FORMATS["webhook_payload"]
        registry.register(
            key="webhook_payload",
            template=fmt["template"],
            description=fmt.get("description", "Webhook payload format"),
            variables=_convert_variables(fmt.get("variables", {})),
            sample_data=SAMPLE_DATA.get("webhook_payload", {}),
            category="webhook",
        )

    return registry


def _convert_variables(variables_dict: Dict[str, str]) -> Dict[str, VariableSpec]:
    """Convert legacy variable descriptions to VariableSpec objects.

    Args:
        variables_dict: Dict of variable name -> description string

    Returns:
        Dict of variable name -> VariableSpec
    """
    result = {}
    for name, description in variables_dict.items():
        # Infer type from variable name patterns
        type_hint = "str"
        if name in ("score", "keyword_score", "semantic_score"):
            type_hint = "float"
        elif name in ("rank", "reactions", "top_n", "channel_count"):
            type_hint = "int"
        elif name in ("is_vip",):
            type_hint = "bool"
        elif name in ("triggers",):
            type_hint = "list"

        # Infer required status from description
        required = "optional" not in description.lower()

        result[name] = VariableSpec(
            name=name,
            description=description,
            required=required,
            type_hint=type_hint,
        )
    return result
