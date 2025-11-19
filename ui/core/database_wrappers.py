"""Database wrapper utilities for TG Sentinel UI.

These utilities provide convenient access to the UI database (ui.db).
Following the dual-database architecture, these wrappers should ONLY
access the UI database, never the Sentinel database (tgsentinel.session).
"""

from typing import Any, Dict, List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine


def query_one(
    engine: Engine, stmt: str, params: Dict[str, Any] | None = None
) -> Dict[str, Any] | None:
    """Execute a SELECT query and return the first row as a dictionary.

    Args:
        engine: SQLAlchemy engine for UI database
        stmt: SQL statement to execute
        params: Optional parameters for the statement

    Returns:
        First row as dictionary, or None if no results
    """
    with engine.connect() as conn:
        result = conn.execute(text(stmt), params or {})
        row = result.fetchone()
        if row is None:
            return None
        return dict(row._mapping)


def query_all(
    engine: Engine, stmt: str, params: Dict[str, Any] | None = None
) -> List[Dict[str, Any]]:
    """Execute a SELECT query and return all rows as dictionaries.

    Args:
        engine: SQLAlchemy engine for UI database
        stmt: SQL statement to execute
        params: Optional parameters for the statement

    Returns:
        List of rows as dictionaries
    """
    with engine.connect() as conn:
        result = conn.execute(text(stmt), params or {})
        return [dict(row._mapping) for row in result]


def execute_statement(
    engine: Engine, stmt: str, params: Dict[str, Any] | None = None
) -> None:
    """Execute an INSERT, UPDATE, or DELETE statement.

    Args:
        engine: SQLAlchemy engine for UI database
        stmt: SQL statement to execute
        params: Optional parameters for the statement
    """
    with engine.connect() as conn:
        conn.execute(text(stmt), params or {})
        conn.commit()


def execute_many(engine: Engine, stmt: str, params_list: List[Dict[str, Any]]) -> None:
    """Execute a statement multiple times with different parameter sets.

    Args:
        engine: SQLAlchemy engine for UI database
        stmt: SQL statement to execute
        params_list: List of parameter dictionaries
    """
    with engine.connect() as conn:
        for params in params_list:
            conn.execute(text(stmt), params)
        conn.commit()
