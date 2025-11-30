#!/usr/bin/env python3
"""
Inject test messages directly into sentinel.db with semantic scoring.

This bypasses Telegram and directly populates the database for testing
alert profiles and semantic scoring.

Usage:
    docker exec tgsentinel-sentinel-1 python tools/inject_test_messages.py \
        --messages-file /app/Test_alerts.txt
"""

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def compute_content_hash(text: str) -> str:
    """Match the hash computation in tgsentinel/store.py"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="Inject test messages into sentinel.db"
    )
    parser.add_argument(
        "--messages-file",
        required=True,
        help="Path to text file with one message per line",
    )
    parser.add_argument(
        "--db-path",
        default="/app/data/sentinel.db",
        help="Path to sentinel.db (default: /app/data/sentinel.db)",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=999999999,
        help="Test chat ID (default: 999999999)",
    )
    parser.add_argument(
        "--chat-title",
        default="Test Channel",
        help="Test chat title (default: Test Channel)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print messages without inserting",
    )

    args = parser.parse_args()

    # Load messages
    msg_path = Path(args.messages_file)
    if not msg_path.exists():
        print(f"Error: messages file not found: {msg_path}")
        return 1

    messages = []
    with msg_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            text = line.strip()
            if text:
                messages.append((line_num, text))

    if not messages:
        print(f"Error: no messages found in {msg_path}")
        return 1

    print(f"Loaded {len(messages)} messages from {msg_path}")

    if args.dry_run:
        print("\n=== DRY RUN MODE ===")
        for msg_id, text in messages:
            print(f"[{msg_id}] {text[:100]}...")
        return 0

    # Connect to database
    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0

    for msg_id, text in messages:
        content_hash = compute_content_hash(text)

        # Check if message already exists (by content_hash)
        existing = cur.execute(
            "SELECT message_text FROM messages WHERE content_hash = ?", (content_hash,)
        ).fetchone()

        if existing:
            print(f"[{msg_id}] SKIP (duplicate): {text[:50]}...")
            skipped += 1
            continue

        # Insert message with minimal fields
        # The sentinel worker will compute semantic scores and triggers on next run
        cur.execute(
            """
            INSERT INTO messages (
                chat_id, msg_id, content_hash, message_text,
                chat_title, created_at, alerted, score
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0.0)
            """,
            (
                args.chat_id,
                msg_id,
                content_hash,
                text,
                args.chat_title,
                now,
            ),
        )
        print(f"[{msg_id}] INSERT: {text[:50]}...")
        inserted += 1

    conn.commit()
    conn.close()

    print("\n=== Summary ===")
    print(f"Inserted: {inserted}")
    print(f"Skipped (duplicates): {skipped}")
    print(f"Total: {len(messages)}")

    print("\n=== Next Steps ===")
    print("The messages have been inserted with minimal metadata.")
    print("To trigger semantic scoring and alert evaluation:")
    print("  1. Restart sentinel: docker compose restart sentinel")
    print("  2. Or wait for the next message processing cycle")
    print("  3. Check alerts: curl http://localhost:8080/api/alerts")

    return 0


if __name__ == "__main__":
    sys.exit(main())
