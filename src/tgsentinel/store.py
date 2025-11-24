import logging
from typing import Any, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  chat_id INTEGER,
  msg_id INTEGER,
  content_hash TEXT,
  score REAL,
  alerted INTEGER DEFAULT 0,
  chat_title TEXT,
  sender_name TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
);

CREATE TABLE IF NOT EXISTS feedback(
  chat_id INTEGER,
  msg_id INTEGER,
  label INTEGER, -- 1=thumbs up, 0=thumbs down
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
);

CREATE TABLE IF NOT EXISTS webhook_deliveries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  webhook_service TEXT NOT NULL,
  profile_id TEXT,
  profile_name TEXT,
  chat_id INTEGER,
  msg_id INTEGER,
  status TEXT NOT NULL, -- 'success', 'failed', 'retry_1', 'retry_2', 'retry_3'
  http_status INTEGER,
  response_time_ms INTEGER,
  error_message TEXT,
  payload TEXT, -- JSON payload sent
  attempt INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _add_column_if_missing(
    connection, table_name: str, column_name: str, column_type: str
) -> None:
    """Add a column to a table if it doesn't already exist.

    Uses SQLAlchemy's inspect to check existing columns, avoiding fragile
    string-based error parsing.

    Args:
        connection: SQLAlchemy connection object
        table_name: Name of the table to modify
        column_name: Name of the column to add
        column_type: SQL type for the column (e.g., "TEXT", "INTEGER")
    """
    inspector = inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns(table_name)}

    if column_name in existing_columns:
        log.debug(f"{column_name} column already exists in {table_name} table")
        return

    try:
        connection.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        )
        log.debug(f"Added {column_name} column to {table_name} table")
    except (OperationalError, ProgrammingError) as e:
        log.error(f"Failed to add {column_name} column to {table_name}: {e}")
        raise


def init_db(db_uri: str) -> Engine:
    engine = create_engine(db_uri, future=True)
    with engine.begin() as con:
        # Execute each CREATE TABLE statement separately
        con.execute(
            text(
                """
CREATE TABLE IF NOT EXISTS messages(
  chat_id INTEGER,
  msg_id INTEGER,
  content_hash TEXT,
  score REAL,
  alerted INTEGER DEFAULT 0,
  chat_title TEXT,
  sender_name TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
)
        """
            )
        )

        con.execute(
            text(
                """
CREATE TABLE IF NOT EXISTS feedback(
  chat_id INTEGER,
  msg_id INTEGER,
  label INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
)
        """
            )
        )

        con.execute(
            text(
                """
CREATE TABLE IF NOT EXISTS webhook_deliveries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  webhook_service TEXT NOT NULL,
  profile_id TEXT,
  profile_name TEXT,
  chat_id INTEGER,
  msg_id INTEGER,
  status TEXT NOT NULL,
  http_status INTEGER,
  response_time_ms INTEGER,
  error_message TEXT,
  payload TEXT,
  attempt INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
        """
            )
        )

        # Add columns to existing tables if they don't exist
        _add_column_if_missing(con, "messages", "chat_title", "TEXT")
        _add_column_if_missing(con, "messages", "sender_name", "TEXT")
        _add_column_if_missing(con, "messages", "message_text", "TEXT")
        _add_column_if_missing(con, "messages", "triggers", "TEXT")
        _add_column_if_missing(con, "messages", "sender_id", "INTEGER")
        _add_column_if_missing(con, "messages", "trigger_annotations", "TEXT")  # JSON

        # Digest scheduling columns (Phase 1)
        _add_column_if_missing(
            con, "messages", "matched_profiles", "TEXT"
        )  # JSON: ["security", "critical"]
        _add_column_if_missing(
            con, "messages", "digest_schedule", "TEXT"
        )  # "hourly", "daily", etc.
        _add_column_if_missing(
            con, "messages", "digest_processed", "INTEGER DEFAULT 0"
        )  # 0=pending, 1=sent

        # Create indexes for performance on common queries
        # These are idempotent - IF NOT EXISTS prevents errors on re-run
        con.execute(
            text("CREATE INDEX IF NOT EXISTS idx_messages_alerted ON messages(alerted)")
        )
        con.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)"
            )
        )
        con.execute(
            text("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id)")
        )
        con.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_feedback_chat_msg ON feedback(chat_id, msg_id)"
            )
        )

        # Digest-related indexes (Phase 1)
        con.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_messages_digest ON messages(digest_schedule, digest_processed, created_at)"
            )
        )

        # Webhook delivery indexes
        con.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_created_at ON webhook_deliveries(created_at DESC)"
            )
        )
        con.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_service ON webhook_deliveries(webhook_service, created_at DESC)"
            )
        )
        con.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries(status, created_at DESC)"
            )
        )

    log.info("DB ready")
    return engine


def upsert_message(
    engine: Engine,
    chat_id: int,
    msg_id: int,
    h: str,
    score: float,
    chat_title: str = "",
    sender_name: str = "",
    message_text: str = "",
    triggers: str = "",
    sender_id: int = 0,
    trigger_annotations: str = "",  # JSON string
    matched_profiles: str = "",  # JSON: ["security", "critical"]
    digest_schedule: str = "",  # "hourly", "daily", etc.
):
    with engine.begin() as con:
        con.execute(
            text(
                """
          INSERT INTO messages(chat_id,msg_id,content_hash,score,alerted,chat_title,sender_name,message_text,triggers,sender_id,trigger_annotations,matched_profiles,digest_schedule,digest_processed)
          VALUES(:c,:m,:h,:s,0,:title,:sender,:text,:triggers,:sender_id,:annotations,:profiles,:schedule,0)
          ON CONFLICT(chat_id,msg_id) DO UPDATE SET 
            score=excluded.score, 
            content_hash=excluded.content_hash,
            chat_title=excluded.chat_title,
            sender_name=excluded.sender_name,
            message_text=excluded.message_text,
            triggers=excluded.triggers,
            sender_id=excluded.sender_id,
            trigger_annotations=excluded.trigger_annotations,
            matched_profiles=excluded.matched_profiles,
            digest_schedule=excluded.digest_schedule
        """
            ),
            {
                "c": chat_id,
                "m": msg_id,
                "h": h,
                "s": score,
                "title": chat_title,
                "sender": sender_name,
                "text": message_text,
                "triggers": triggers,
                "sender_id": sender_id,
                "annotations": trigger_annotations,
                "profiles": matched_profiles,
                "schedule": digest_schedule,
            },
        )


def mark_alerted(engine: Engine, chat_id: int, msg_id: int):
    with engine.begin() as con:
        con.execute(
            text("UPDATE messages SET alerted=1 WHERE chat_id=:c AND msg_id=:m"),
            {"c": chat_id, "m": msg_id},
        )


def cleanup_old_messages(
    engine: Engine,
    retention_days: int = 30,
    max_messages: int = 200,
    preserve_alerted_multiplier: int = 2,
) -> dict[str, int]:
    """Clean up old messages based on retention policy.

    Args:
        engine: SQLAlchemy engine
        retention_days: Delete messages older than this many days
        max_messages: Keep only this many most recent messages
        preserve_alerted_multiplier: Keep alerted messages for this many times longer

    Returns:
        Dictionary with cleanup statistics:
        - deleted_by_age: Number of messages deleted due to age
        - deleted_by_count: Number of messages deleted due to count limit
        - total_deleted: Total messages deleted
        - remaining_count: Number of messages remaining
    """
    stats = {
        "deleted_by_age": 0,
        "deleted_by_count": 0,
        "total_deleted": 0,
        "remaining_count": 0,
    }

    with engine.begin() as con:
        # Step 1: Delete messages older than retention_days
        # Preserve alerted messages for longer (2x retention by default)
        result = con.execute(
            text(
                """
                DELETE FROM messages 
                WHERE datetime(created_at) < datetime('now', '-' || :retention_days || ' days')
                  AND (alerted = 0 OR datetime(created_at) < datetime('now', '-' || :alerted_retention_days || ' days'))
            """
            ),
            {
                "retention_days": retention_days,
                "alerted_retention_days": retention_days * preserve_alerted_multiplier,
            },
        )
        stats["deleted_by_age"] = result.rowcount

        # Step 2: If count still exceeds max_messages, delete oldest beyond limit
        # First check current count
        count_result = con.execute(text("SELECT COUNT(*) FROM messages"))
        current_count = count_result.scalar()

        if current_count is not None and current_count > max_messages:
            # Delete messages beyond the limit, keeping the most recent ones
            # Prefer keeping alerted messages within the limit
            result = con.execute(
                text(
                    """
                    DELETE FROM messages 
                    WHERE (chat_id, msg_id) NOT IN (
                        SELECT chat_id, msg_id FROM messages 
                        ORDER BY alerted DESC, created_at DESC 
                        LIMIT :max_messages
                    )
                """
                ),
                {"max_messages": max_messages},
            )
            stats["deleted_by_count"] = result.rowcount

        # Get final count
        final_count_result = con.execute(text("SELECT COUNT(*) FROM messages"))
        remaining = final_count_result.scalar()
        stats["remaining_count"] = int(remaining) if remaining is not None else 0
        stats["total_deleted"] = stats["deleted_by_age"] + stats["deleted_by_count"]

    return stats


def vacuum_database(engine: Engine) -> dict[str, Any]:
    """Run VACUUM to reclaim space and optimize database.

    Args:
        engine: SQLAlchemy engine

    Returns:
        Dictionary with vacuum statistics:
        - success: Whether VACUUM completed
        - error: Error message if failed
        - duration_seconds: Time taken to VACUUM

    Note:
        VACUUM runs outside a transaction and cannot be interrupted in SQLite.
    """
    import time

    stats = {
        "success": False,
        "error": None,
        "duration_seconds": 0.0,
    }

    start_time = time.time()

    try:
        # VACUUM cannot run inside a transaction in SQLite
        # Get raw connection for VACUUM (must be outside transaction)
        from contextlib import closing

        raw_conn = engine.raw_connection()
        try:
            cursor = raw_conn.cursor()
            try:
                cursor.execute("VACUUM")
                raw_conn.commit()
            finally:
                cursor.close()
        finally:
            raw_conn.close()

        stats["success"] = True
        stats["duration_seconds"] = time.time() - start_time

    except Exception as e:
        stats["error"] = str(e)
        stats["duration_seconds"] = time.time() - start_time
        log.error(f"VACUUM failed: {e}")

    return stats


def record_webhook_delivery(
    engine: Engine,
    webhook_service: str,
    profile_id: str,
    profile_name: str,
    chat_id: int,
    msg_id: int,
    status: str,
    http_status: Optional[int] = None,
    response_time_ms: Optional[int] = None,
    error_message: Optional[str] = None,
    payload: Optional[str] = None,
    attempt: int = 1,
):
    """Record a webhook delivery attempt in the database.

    Args:
        engine: SQLAlchemy engine
        webhook_service: Name of the webhook service (e.g., "slack", "pagerduty")
        profile_id: Profile ID that triggered the webhook
        profile_name: Profile name
        chat_id: Chat ID where the message originated
        msg_id: Message ID
        status: Delivery status ('success', 'failed', 'retry_1', 'retry_2', 'retry_3')
        http_status: HTTP status code from webhook response
        response_time_ms: Response time in milliseconds
        error_message: Error message if delivery failed
        payload: JSON payload sent to webhook
        attempt: Attempt number (1-4, where 4 is final retry)
    """
    with engine.begin() as con:
        con.execute(
            text(
                """
                INSERT INTO webhook_deliveries(
                    webhook_service, profile_id, profile_name, chat_id, msg_id,
                    status, http_status, response_time_ms, error_message, payload, attempt
                )
                VALUES(:service, :profile_id, :profile_name, :chat_id, :msg_id,
                       :status, :http_status, :response_time_ms, :error_message, :payload, :attempt)
                """
            ),
            {
                "service": webhook_service,
                "profile_id": str(profile_id) if profile_id else None,
                "profile_name": profile_name,
                "chat_id": chat_id,
                "msg_id": msg_id,
                "status": status,
                "http_status": http_status,
                "response_time_ms": response_time_ms,
                "error_message": error_message,
                "payload": payload,
                "attempt": attempt,
            },
        )


def get_recent_webhook_deliveries(engine: Engine, limit: int = 10) -> list[dict]:
    """Get recent webhook deliveries for display in UI.

    Args:
        engine: SQLAlchemy engine
        limit: Maximum number of deliveries to return

    Returns:
        List of webhook delivery records as dictionaries.
        Note: payload column is intentionally omitted from SELECT to reduce
        data transfer size for UI display purposes.
    """
    with engine.begin() as con:
        result = con.execute(
            text(
                """
                SELECT 
                    id, webhook_service, profile_id, profile_name, chat_id, msg_id,
                    status, http_status, response_time_ms, error_message,
                    attempt, created_at
                FROM webhook_deliveries
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )

        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


def cleanup_old_webhook_deliveries(engine: Engine, days: int = 30) -> int:
    """Clean up old webhook delivery records.

    Args:
        engine: SQLAlchemy engine
        days: Delete records older than this many days

    Returns:
        Number of records deleted
    """
    with engine.begin() as con:
        result = con.execute(
            text(
                """
                DELETE FROM webhook_deliveries
                WHERE created_at < datetime('now', '-' || :days || ' days')
                """
            ),
            {"days": days},
        )
        return result.rowcount
