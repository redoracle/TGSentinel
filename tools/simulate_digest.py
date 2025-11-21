#!/usr/bin/env python3
"""Test script to trigger digest delivery immediately."""

import asyncio
import sys

# Add src to path
sys.path.insert(0, "/app/src")

from tgsentinel.client import make_client
from tgsentinel.config import load_config
from tgsentinel.digest import send_digest
from tgsentinel.store import init_db, mark_alerted, upsert_message


async def main():
    """Simulate a digest by creating test messages and sending digest."""
    print("Loading config...")
    cfg = load_config()

    print("Initializing database...")
    engine = init_db(cfg.db_uri)

    # Insert some test messages
    print("Creating test messages...")
    upsert_message(engine, -1001234567890, 1, "hash1", 2.5)
    mark_alerted(engine, -1001234567890, 1)

    upsert_message(engine, -1001234567890, 2, "hash2", 1.8)
    mark_alerted(engine, -1001234567890, 2)

    upsert_message(engine, -1001234567890, 3, "hash3", 3.2)
    mark_alerted(engine, -1001234567890, 3)

    print("Connecting to Telegram...")
    client = make_client(cfg)

    try:
        await client.start()  # type: ignore[misc]

        print(
            f"Sending digest to mode={cfg.alerts.mode}, channel={cfg.alerts.target_channel}"
        )
        await send_digest(
            engine=engine,
            client=client,
            since_hours=24,
            top_n=cfg.alerts.digest.top_n,
            mode=cfg.alerts.mode,
            channel=cfg.alerts.target_channel,
        )

        print("✅ Digest sent successfully!")
    except Exception as e:
        print(f"❌ Error sending digest: {e}", file=sys.stderr)
        raise
    finally:
        print("Disconnecting client...")
        await client.disconnect()  # type: ignore[misc]


if __name__ == "__main__":
    asyncio.run(main())
