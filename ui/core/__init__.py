"""Core infrastructure for TG Sentinel UI.

This package contains foundational components:
- dependencies: Dependency injection container
"""

from .dependencies import Dependencies, get_deps

__all__ = ["Dependencies", "get_deps"]
