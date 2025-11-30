"""
Message Formats Module

Provides configurable message format templates for alerts, digests, and webhooks.
Templates are stored in YAML and can be edited via the UI.
"""

from .defaults import DEFAULT_FORMATS, SAMPLE_DATA
from .loader import (
    get_format,
    get_formats_path,
    load_message_formats,
    reload_formats,
    save_message_formats,
)
from .renderer import (
    get_trigger_icon,
    render_digest_entry,
    render_digest_header,
    render_dm_alert,
    render_saved_message,
    render_template,
    render_webhook_payload,
    render_webhook_payload_dict,
)
from .validator import validate_formats, validate_template

__all__ = [
    # Loader
    "load_message_formats",
    "save_message_formats",
    "get_format",
    "reload_formats",
    "get_formats_path",
    # Renderer
    "render_template",
    "render_dm_alert",
    "render_saved_message",
    "render_digest_header",
    "render_digest_entry",
    "render_webhook_payload",
    "render_webhook_payload_dict",
    "get_trigger_icon",
    # Validator
    "validate_formats",
    "validate_template",
    # Defaults
    "DEFAULT_FORMATS",
    "SAMPLE_DATA",
]
