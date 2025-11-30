"""Dependency container for TG Sentinel UI application.

This module provides a singleton container for all application dependencies,
replacing the global variables pattern with explicit dependency management.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, Tuple

from sqlalchemy.engine import Engine


class Dependencies:
    """Container for all application dependencies.

    This singleton class manages all shared application state and services,
    providing a single source of truth for dependency injection.

    Attributes:
        config: Application configuration object
        redis_client: Redis client for caching and messaging
        engine: SQLAlchemy engine (deprecated - use UI DB only)
        data_service: Service for data operations
        profile_service: Service for profile management
        config_service: Service for configuration file operations
        alert_loader: Callable for loading alerts with signature (limit: int) -> list[dict]
        _cached_summary: Cached dashboard summary with timestamp
        _cached_health: Cached health metrics with timestamp
        _login_ctx: In-memory login context (single-worker fallback)
        _is_initialized: Initialization flag
    """

    _instance: Dependencies | None = None
    _lock = threading.Lock()

    def __init__(self):
        """Initialize empty dependency container."""
        from tgsentinel.config import AppCfg  # type: ignore

        self.config: AppCfg | None = None
        self.redis_client: Any = None
        self.engine: Engine | None = (
            None  # Deprecated - UI should not access sentinel DB
        )
        self.data_service: Any = None  # DataService instance
        self.profile_service: Any = None  # ProfileService instance
        self.config_service: Any = None  # ConfigService instance
        self.alert_loader: Any = None  # Callable[[int], list[dict]] for loading alerts

        # Caches with timestamps
        self._cached_summary: Tuple[datetime, Dict[str, Any]] | None = None
        self._cached_health: Tuple[datetime, Dict[str, Any]] | None = None

        # Login context (in-memory fallback for single-worker deployments)
        self._login_ctx: Dict[str, Dict[str, Any]] = {}

        # Initialization state
        self._is_initialized = False
        self._init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> Dependencies:
        """Get singleton instance of Dependencies container.

        Returns:
            The singleton Dependencies instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance (for testing only).

        This method is provided for test isolation. It clears the singleton
        instance and all cached state.
        """
        with cls._lock:
            cls._instance = None

    def clear_caches(self) -> None:
        """Clear all cached data."""
        self._cached_summary = None
        self._cached_health = None

    def is_initialized(self) -> bool:
        """Check if dependencies have been initialized."""
        with self._init_lock:
            return self._is_initialized

    def mark_initialized(self) -> None:
        """Mark dependencies as initialized."""
        with self._init_lock:
            self._is_initialized = True


# Convenience function for getting the singleton instance
def get_deps() -> Dependencies:
    """Get the application dependencies container.

    Returns:
        The singleton Dependencies instance.
    """
    return Dependencies.get_instance()
