"""API route blueprints for TG Sentinel UI.

This package contains modular Flask blueprints organized by functional area.

Note: Blueprints are NOT imported at package level to allow individual imports
even when some blueprints have missing optional dependencies (e.g., flask_limiter).
"""

__all__ = [
    "analytics_bp",
    "config_info_bp",
    "console_bp",
    "developer_bp",
    "participant_bp",
    "static_bp",
    "telegram_bp",
    "ui_lock_bp",
]
