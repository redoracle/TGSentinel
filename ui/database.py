"""UI Database Module

This module manages the UI-specific SQLite database (ui.db) that stores:
- User authentication tokens and sessions
- UI settings and preferences
- Cached alerts and notifications from sentinel
- User profiles and filters
- Audit logs for UI actions

The UI DB is completely separate from:
- sentinel.db (sentinel worker's application data)
- tgsentinel.session (Telethon's SQLite session file)
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UIDatabase:
    """UI-specific database manager with thread-safe connection handling."""

    def __init__(self, db_uri: str = "sqlite:////app/data/ui.db"):
        """Initialize the UI database.

        Args:
            db_uri: SQLAlchemy-style database URI (default: sqlite:////app/data/ui.db)
        """
        # Extract path from sqlite:////app/data/ui.db format
        if db_uri.startswith("sqlite:///"):
            self.db_path = Path(db_uri.replace("sqlite:///", ""))
        else:
            # Fallback for simpler paths
            self.db_path = Path(db_uri)

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Thread-local storage for per-thread connections
        self._local = threading.local()
        logger.info(f"UI Database initialized at: {self.db_path}")

    def connect(self) -> sqlite3.Connection:
        """Get or create thread-local database connection.

        Each thread gets its own connection, ensuring thread-safety without
        using check_same_thread=False which is unsafe.
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path))
            self._local.conn.row_factory = sqlite3.Row
            logger.debug(
                f"Created new DB connection for thread {threading.current_thread().name}"
            )
        return self._local.conn

    def close(self):
        """Close the thread-local database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
            logger.debug(
                f"Closed DB connection for thread {threading.current_thread().name}"
            )

    def close_all(self):
        """Close connection for current thread.
        Note: Cannot close connections in other threads from here.
        """
        self.close()

    def init_schema(self):
        """Initialize database schema with all required tables."""
        conn = self.connect()
        cursor = conn.cursor()

        # Schema migrations tracking
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT
            )
        """
        )

        # UI settings table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            )
        """
        )

        # Cached alerts from sentinel
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT UNIQUE,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT,
                sender_name TEXT,
                message_text TEXT,
                score REAL NOT NULL,
                triggers TEXT,
                timestamp TEXT NOT NULL,
                read BOOLEAN DEFAULT 0,
                dismissed BOOLEAN DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """
        )

        # User profiles for scoring/filtering
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                keywords TEXT,
                muted_keywords TEXT,
                score_threshold REAL DEFAULT 5.0,
                enabled BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """
        )

        # Digest runs log
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS digest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                sent_to TEXT,
                status TEXT NOT NULL,
                error TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
        """
        )

        # Audit log for user actions
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                details TEXT,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )
        """
        )

        # UI authentication sessions
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_sessions (
                session_id TEXT PRIMARY KEY,
                user_identifier TEXT,
                authenticated BOOLEAN DEFAULT 0,
                locked BOOLEAN DEFAULT 0,
                created_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                expires_at TEXT
            )
        """
        )

        # Create indexes
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_chat_id ON alerts(chat_id)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_read ON alerts(read)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_digest_runs_started ON digest_runs(started_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at DESC)"
        )

        conn.commit()

        # Record migration
        self._record_migration(1, "Initial schema creation")

        logger.info("UI Database schema initialized successfully")

    def _record_migration(self, version: int, description: str):
        """Record a migration as applied."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT version FROM schema_migrations WHERE version = ?", (version,)
        )
        if not cursor.fetchone():
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                (version, now, description),
            )
            conn.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Retrieve a setting value."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()

        if row:
            return row["value"]
        return default

    def set_setting(self, key: str, value: str):
        """Set a setting value."""
        conn = self.connect()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        conn.commit()

    def cache_alert(self, alert_data: Dict[str, Any]):
        """Cache an alert received from sentinel."""
        conn = self.connect()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc).isoformat()

        cursor.execute(
            """
            INSERT OR REPLACE INTO alerts 
            (alert_id, chat_id, message_id, chat_title, sender_name, message_text, 
             score, triggers, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                alert_data.get("alert_id"),
                alert_data.get("chat_id"),
                alert_data.get("message_id"),
                alert_data.get("chat_title"),
                alert_data.get("sender_name"),
                alert_data.get("message_text"),
                alert_data.get("score", 0.0),
                alert_data.get("triggers", ""),
                alert_data.get("timestamp"),
                now,
            ),
        )
        conn.commit()

    def get_recent_alerts(
        self, limit: int = 50, unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Retrieve recent alerts."""
        conn = self.connect()
        cursor = conn.cursor()

        query = "SELECT * FROM alerts"
        if unread_only:
            query += " WHERE read = 0"
        query += " ORDER BY timestamp DESC LIMIT ?"

        cursor.execute(query, (limit,))

        alerts = []
        for row in cursor.fetchall():
            alerts.append(dict(row))

        return alerts

    def mark_alert_read(self, alert_id: str):
        """Mark an alert as read."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("UPDATE alerts SET read = 1 WHERE alert_id = ?", (alert_id,))
        conn.commit()

    def log_action(
        self,
        action: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        """Log a user action to audit log."""
        conn = self.connect()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc).isoformat()

        cursor.execute(
            """
            INSERT INTO audit_log 
            (action, resource_type, resource_id, details, ip_address, user_agent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (action, resource_type, resource_id, details, ip_address, user_agent, now),
        )
        conn.commit()

    def query_one(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a query and return a single scalar value.

        Provides compatibility with legacy _query_one function.
        """
        if params is None:
            params = {}

        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()

        if row:
            # Return first column value
            return row[0] if isinstance(row, tuple) else list(row.values())[0]
        return None

    def query_all(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Execute a query and return all results as list of dicts.

        Provides compatibility with legacy _query_all function.
        """
        if params is None:
            params = {}

        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        # Convert to list of dicts
        return [dict(row) for row in rows]

    def execute_write(self, sql: str, params: Optional[Dict[str, Any]] = None):
        """Execute a write operation (INSERT, UPDATE, DELETE).

        Provides compatibility with legacy _execute function.
        """
        if params is None:
            params = {}

        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()


# Singleton instance
_ui_db: Optional[UIDatabase] = None


def get_ui_db() -> UIDatabase:
    """Get or create the UI database singleton."""
    global _ui_db

    if _ui_db is None:
        db_uri = os.getenv("UI_DB_URI", "sqlite:////app/data/ui.db")
        _ui_db = UIDatabase(db_uri)
        _ui_db.init_schema()

    return _ui_db


def init_ui_db(db_uri: Optional[str] = None):
    """Initialize the UI database (call on startup)."""
    global _ui_db

    if db_uri is None:
        db_uri = os.getenv("UI_DB_URI", "sqlite:////app/data/ui.db")

    _ui_db = UIDatabase(db_uri)
    _ui_db.init_schema()

    logger.info("UI Database initialized and ready")
    return _ui_db
