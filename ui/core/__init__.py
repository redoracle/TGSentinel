"""Core infrastructure for TG Sentinel UI.

This package contains foundational components:
- dependencies: Dependency injection container
- database_wrappers: Database query utilities
"""

from .database_wrappers import execute_many, execute_statement, query_all, query_one
from .dependencies import Dependencies, get_deps

__all__ = [
    "Dependencies",
    "get_deps",
    "query_one",
    "query_all",
    "execute_statement",
    "execute_many",
]
