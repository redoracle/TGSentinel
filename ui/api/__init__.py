"""API route blueprints for TG Sentinel UI.

This package contains modular Flask blueprints organized by functional area.
"""

from .analytics_routes import analytics_bp
from .config_info_routes import config_info_bp
from .console_routes import console_bp
from .developer_routes import developer_bp
from .participant_routes import participant_bp
from .static_routes import static_bp
from .telegram_routes import telegram_bp
from .ui_lock_routes import ui_lock_bp

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
