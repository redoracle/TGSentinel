"""Route blueprints for TG Sentinel UI.

This package contains modular Flask blueprints organized by functionality.
Each blueprint handles a specific domain of routes (session, dashboard, alerts, etc.).
"""

from __future__ import annotations

__all__ = [
    "session_bp",
    "dashboard_bp",
    "worker_bp",
    "views_bp",
]

try:
    from .dashboard import dashboard_bp
    from .session import session_bp
    from .views import views_bp
    from .worker import worker_bp
except ImportError:
    # Graceful fallback if blueprints not yet extracted
    session_bp = None  # type: ignore
    dashboard_bp = None  # type: ignore
    worker_bp = None  # type: ignore
    views_bp = None  # type: ignore
