"""Timestamp utilities for TG Sentinel.

Provides consistent timestamp formatting across the entire application.
All timestamps are stored in UTC in the database using SQLite's CURRENT_TIMESTAMP format.
"""

from datetime import datetime, timezone


def format_db_timestamp(dt: datetime) -> str:
    """Format a datetime for SQLite comparison.

    Args:
        dt: datetime object (will be converted to UTC if not already)

    Returns:
        String in format 'YYYY-MM-DD HH:MM:SS' (no timezone marker, assumes UTC)

    Example:
        >>> dt = datetime(2025, 11, 26, 18, 30, 45, tzinfo=timezone.utc)
        >>> format_db_timestamp(dt)
        '2025-11-26 18:30:45'
    """
    if dt.tzinfo is None:
        # Assume UTC if no timezone
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Convert to UTC
        dt = dt.astimezone(timezone.utc)

    # SQLite CURRENT_TIMESTAMP format: 'YYYY-MM-DD HH:MM:SS'
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def now_db_timestamp() -> str:
    """Get current UTC timestamp in database format.

    Returns:
        Current UTC time as 'YYYY-MM-DD HH:MM:SS'
    """
    return format_db_timestamp(datetime.now(timezone.utc))


def parse_db_timestamp(timestamp_str: str) -> datetime:
    """Parse a database timestamp string to datetime.

    Args:
        timestamp_str: String in format 'YYYY-MM-DD HH:MM:SS' (UTC)

    Returns:
        datetime object with UTC timezone

    Example:
        >>> parse_db_timestamp('2025-11-26 18:30:45')
        datetime.datetime(2025, 11, 26, 18, 30, 45, tzinfo=datetime.timezone.utc)
    """
    # Parse without timezone, then set to UTC
    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)
