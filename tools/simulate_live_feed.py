#!/usr/bin/env python3
"""
Simulate a live Telegram feed by sending messages to users or channels.

This script supports two modes:

1. SINGLE-SESSION MODE (recommended):
   Send messages from one authenticated account to any user, group, or channel.
   You only need one session file and a target identifier.

2. DUAL-SESSION MODE (legacy):
   For bidirectional testing between two accounts you control.
   Requires two session files.

Usage Examples:

  # Single-session: Send to a user by username
  python tools/simulate_live_feed.py \\
      --session ./my_account.session \\
      --target @username \\
      --count 10

  # Single-session: Send to a channel by link
  python tools/simulate_live_feed.py \\
      --session ./my_account.session \\
      --target https://t.me/channelname \\
      --messages-file ./test_messages.txt

  # Single-session: Send to a user by phone number
  python tools/simulate_live_feed.py \\
      --session ./my_account.session \\
      --target +1234567890 \\
      --interval 3 \\
      --count 5

  # Single-session: Send to a channel/group by numeric ID
  python tools/simulate_live_feed.py \\
      --session ./my_account.session \\
      --target -1001234567890 \\
      --count 20

  # Dual-session: Bidirectional testing between two accounts
  python tools/simulate_live_feed.py \\
      --session-a ./account_a.session \\
      --session-b ./account_b.session \\
      --direction both \\
      --count 10

Target Formats (single-session mode):
  - @username          Username (user, bot, or public channel)
  - +1234567890        Phone number (must be in your contacts)
  - https://t.me/name  Telegram link (channel, group, or user)
  - t.me/name          Short link format
  - -1001234567890     Numeric channel/supergroup ID
  - 1234567890         Numeric user ID

API credentials:
  - Pass --api-id / --api-hash on the command line, or
  - Set TG_API_ID / TG_API_HASH environment variables
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Tuple, Union

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User


def _resolve_api_credentials(args: argparse.Namespace) -> Tuple[int, str]:
    """Resolve API credentials from args or environment variables."""
    api_id = args.api_id or os.getenv("TG_API_ID")
    api_hash = args.api_hash or os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        print(
            "Error: API credentials are required. Provide --api-id/--api-hash "
            "or set TG_API_ID and TG_API_HASH environment variables."
        )
        sys.exit(1)

    try:
        api_id_int = int(api_id)
    except ValueError:
        print("Error: API ID must be an integer")
        sys.exit(1)

    return api_id_int, api_hash


def _session_base(path: str) -> str:
    """
    Get the session base path for Telethon.

    Telethon appends '.session' to the provided name. Session files
    created by tools/generate_session.py already have this extension,
    so we strip it here to reuse those files.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print(f"Error: Session file does not exist: {p}")
        sys.exit(1)
    return str(p.with_suffix(""))


def _parse_target(target: str) -> Union[str, int]:
    """
    Parse target identifier into a format Telethon can resolve.

    Supported formats:
      - @username → "username"
      - +1234567890 → "+1234567890" (phone number)
      - https://t.me/name → "name"
      - t.me/name → "name"
      - -1001234567890 → -1001234567890 (channel/supergroup ID)
      - 1234567890 → 1234567890 (user ID)

    Returns:
        str or int: The parsed target identifier
    """
    target = target.strip()

    # Handle Telegram links: https://t.me/name or t.me/name
    link_match = re.match(r"(?:https?://)?t\.me/([a-zA-Z0-9_]+)", target)
    if link_match:
        return link_match.group(1)

    # Handle @username format
    if target.startswith("@"):
        return target[1:]  # Remove @ prefix

    # Handle phone numbers (keep as-is)
    if target.startswith("+"):
        return target

    # Handle numeric IDs (user or channel)
    try:
        return int(target)
    except ValueError:
        pass

    # Treat as username if no other pattern matches
    return target


async def _init_client(
    session_path: str, api_id: int, api_hash: str, label: str = ""
) -> TelegramClient:
    """
    Initialize and connect a Telegram client.

    Args:
        session_path: Path to the session file (without .session extension)
        api_id: Telegram API ID
        api_hash: Telegram API hash
        label: Optional label for log messages (e.g., "A", "B")

    Returns:
        Connected and authorized TelegramClient
    """
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print(
            f"Error: Session at {session_path}.session is not authorized. "
            "Use tools/generate_session.py to create a valid session."
        )
        client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    if not isinstance(me, User):
        print(f"Error: Could not fetch account info for session: {session_path}")
        client.disconnect()
        sys.exit(1)

    username = f"@{me.username}" if me.username else None
    display = username or f"{me.first_name or ''} {me.last_name or ''}".strip()
    label_prefix = f"[{label}] " if label else ""
    print(
        f"{label_prefix}Connected as {display} (id={me.id}) "
        f"using session {session_path}.session"
    )
    return client


async def _resolve_entity(
    client: TelegramClient, target: Union[str, int]
) -> Tuple[Any, str]:
    """
    Resolve a target identifier to a Telegram entity.

    Args:
        client: Connected TelegramClient
        target: Target identifier (username, phone, ID, etc.)

    Returns:
        Tuple of (entity, display_name)
    """
    try:
        entity = await client.get_entity(target)

        # Build display name based on entity type
        if isinstance(entity, User):
            if entity.username:
                display = f"@{entity.username}"
            else:
                display = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
                display = display or f"User({entity.id})"
        elif isinstance(entity, Channel):
            display = f"#{entity.title}" if entity.title else f"Channel({entity.id})"
        elif isinstance(entity, Chat):
            display = f"#{entity.title}" if entity.title else f"Chat({entity.id})"
        else:
            display = str(target)

        return entity, display

    except Exception as e:
        print(f"Error: Could not resolve target '{target}': {e}")
        print("\nPossible causes:")
        print("  - Username/channel doesn't exist or is private")
        print("  - Phone number is not in your contacts")
        print("  - You don't have access to this chat/channel")
        print("  - The entity ID is incorrect")
        sys.exit(1)


def _build_message(prefix: str, seq: int) -> str:
    """Build a timestamped test message."""
    now = datetime.now().isoformat(timespec="seconds")
    return f"[{prefix}] message #{seq} at {now}"


async def _run_single_session_mode(
    args: argparse.Namespace,
    api_id: int,
    api_hash: str,
    messages: List[str],
) -> int:
    """
    Run in single-session mode: send messages from one account to a target.

    Args:
        args: Parsed command-line arguments
        api_id: Telegram API ID
        api_hash: Telegram API hash
        messages: List of messages to send (or empty for auto-generated)

    Returns:
        Exit code (0 for success)
    """
    session_path = _session_base(args.session)
    client = await _init_client(session_path, api_id, api_hash)

    try:
        # Parse and resolve target
        target_id = _parse_target(args.target)
        entity, target_display = await _resolve_entity(client, target_id)

        total_messages = args.count
        if messages:
            total_messages = min(args.count, len(messages))

        print(f"\nSending {total_messages} message(s) to {target_display}")
        print(f"Interval: {args.interval}s")
        print("Press Ctrl+C to stop early.\n")

        for i in range(1, total_messages + 1):
            if messages:
                text = messages[i - 1]
            else:
                text = _build_message(args.prefix, i)

            await client.send_message(entity, text)
            print(f"[{i}/{total_messages}] → {target_display}: {text}")

            if i < total_messages:
                await asyncio.sleep(args.interval)

        print("\nDone sending messages.")
        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user, stopping simulation.")
        return 0
    finally:
        client.disconnect()


async def _run_dual_session_mode(
    args: argparse.Namespace,
    api_id: int,
    api_hash: str,
    messages: List[str],
) -> int:
    """
    Run in dual-session mode: bidirectional messaging between two accounts.

    Args:
        args: Parsed command-line arguments
        api_id: Telegram API ID
        api_hash: Telegram API hash
        messages: List of messages to send (or empty for auto-generated)

    Returns:
        Exit code (0 for success)
    """
    session_a = _session_base(args.session_a)
    session_b = _session_base(args.session_b)

    client_a = await _init_client(session_a, api_id, api_hash, label="A")
    client_b = await _init_client(session_b, api_id, api_hash, label="B")

    try:
        me_a = await client_a.get_me()
        me_b = await client_b.get_me()

        if not isinstance(me_a, User) or not isinstance(me_b, User):
            print("Error: Could not fetch user information")
            return 1

        # Use username if available, fallback to user ID
        target_a_to_b = me_b.username if me_b.username else me_b.id
        target_b_to_a = me_a.username if me_a.username else me_a.id

        # Pre-fetch entities to populate session cache
        try:
            if args.direction in ("a-to-b", "both"):
                await client_a.get_entity(target_a_to_b)
            if args.direction in ("b-to-a", "both"):
                await client_b.get_entity(target_b_to_a)
        except Exception as entity_err:
            print(f"Warning: Could not pre-fetch entities: {entity_err}")
            try:
                print("Attempting to fetch dialogs to populate entity cache...")
                await client_a.get_dialogs(limit=20)
                await client_b.get_dialogs(limit=20)
                if args.direction in ("a-to-b", "both"):
                    await client_a.get_entity(target_a_to_b)
                if args.direction in ("b-to-a", "both"):
                    await client_b.get_entity(target_b_to_a)
                print("Entity cache populated successfully")
            except Exception as dialog_err:
                print(f"Error: Could not populate entity cache: {dialog_err}")
                print("\nTo fix this:")
                print("  1. Ensure the accounts have messaged each other before")
                print("  2. Or use accounts that are in each other's contacts")
                return 1

        total_batches = args.count
        if messages:
            total_batches = min(args.count, len(messages))

        print(
            f"\nStarting dual-session simulation: direction={args.direction}, "
            f"interval={args.interval}s, count={total_batches}"
        )
        print("Press Ctrl+C to stop early.\n")

        for i in range(1, total_batches + 1):
            if args.direction in ("a-to-b", "both"):
                text = messages[i - 1] if messages else _build_message(args.prefix_a, i)
                await client_a.send_message(target_a_to_b, text)
                print(f"[{i}] A → B: {text}")

            if args.direction in ("b-to-a", "both"):
                text = messages[i - 1] if messages else _build_message(args.prefix_b, i)
                await client_b.send_message(target_b_to_a, text)
                print(f"[{i}] B → A: {text}")

            if i < total_batches:
                await asyncio.sleep(args.interval)

        print("\nDone sending messages.")
        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user, stopping simulation.")
        return 0
    finally:
        client_a.disconnect()
        client_b.disconnect()


def _load_messages_file(path: str) -> List[str]:
    """Load messages from a text file (one per line)."""
    msg_path = Path(path).expanduser().resolve()
    if not msg_path.exists():
        print(f"Error: Messages file not found: {msg_path}")
        sys.exit(1)

    messages = []
    with msg_path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if text:
                messages.append(text)

    if not messages:
        print(f"Error: Messages file is empty: {msg_path}")
        sys.exit(1)

    print(f"Loaded {len(messages)} message(s) from {msg_path}")
    return messages


async def main_async() -> int:
    """Main async entry point."""
    parser = argparse.ArgumentParser(
        description="Simulate live Telegram traffic for testing TG Sentinel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send to a user
  %(prog)s --session ./my.session --target @username --count 5

  # Send to a channel
  %(prog)s --session ./my.session --target https://t.me/channel --messages-file msgs.txt

  # Dual-session bidirectional testing
  %(prog)s --session-a ./a.session --session-b ./b.session --direction both
""",
    )

    # Single-session mode arguments
    single_group = parser.add_argument_group(
        "Single-session mode",
        "Send messages from one account to any target (user, group, or channel)",
    )
    single_group.add_argument(
        "--session",
        help="Path to Telethon session file (e.g., ./my_account.session)",
    )
    single_group.add_argument(
        "--target",
        help=(
            "Target to send messages to: @username, +phone, t.me/link, " "or numeric ID"
        ),
    )
    single_group.add_argument(
        "--prefix",
        default="TEST",
        help="Message prefix for auto-generated messages (default: 'TEST')",
    )

    # Dual-session mode arguments
    dual_group = parser.add_argument_group(
        "Dual-session mode",
        "Bidirectional messaging between two accounts you control",
    )
    dual_group.add_argument(
        "--session-a",
        help="Path to session file for account A",
    )
    dual_group.add_argument(
        "--session-b",
        help="Path to session file for account B",
    )
    dual_group.add_argument(
        "--direction",
        choices=["a-to-b", "b-to-a", "both"],
        default="a-to-b",
        help="Send direction for dual-session mode (default: a-to-b)",
    )
    dual_group.add_argument(
        "--prefix-a",
        default="A→B",
        help="Message prefix when A sends (default: 'A→B')",
    )
    dual_group.add_argument(
        "--prefix-b",
        default="B→A",
        help="Message prefix when B sends (default: 'B→A')",
    )

    # Common arguments
    common_group = parser.add_argument_group("Common options")
    common_group.add_argument(
        "--api-id",
        help="Telegram API ID (or set TG_API_ID env var)",
    )
    common_group.add_argument(
        "--api-hash",
        help="Telegram API hash (or set TG_API_HASH env var)",
    )
    common_group.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between messages (default: 5.0)",
    )
    common_group.add_argument(
        "--count",
        type=int,
        default=20,
        help="Number of messages to send (default: 20)",
    )
    common_group.add_argument(
        "--messages-file",
        help="Text file with messages to send (one per line)",
    )

    args = parser.parse_args()

    # Determine mode based on provided arguments
    single_mode = args.session and args.target
    dual_mode = args.session_a and args.session_b

    if single_mode and dual_mode:
        print(
            "Error: Cannot use both single-session (--session/--target) and "
            "dual-session (--session-a/--session-b) modes simultaneously."
        )
        return 1

    if not single_mode and not dual_mode:
        print("Error: Must specify either:")
        print("  Single-session: --session and --target")
        print("  Dual-session:   --session-a and --session-b")
        print("\nUse --help for usage examples.")
        return 1

    api_id, api_hash = _resolve_api_credentials(args)

    # Load messages from file if provided
    messages: List[str] = []
    if args.messages_file:
        messages = _load_messages_file(args.messages_file)

    # Run appropriate mode
    if single_mode:
        return await _run_single_session_mode(args, api_id, api_hash, messages)
    else:
        return await _run_dual_session_mode(args, api_id, api_hash, messages)


def main() -> None:
    """Entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
