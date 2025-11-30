import asyncio
import datetime as dt
import json
import logging
import time
from typing import Dict, Optional

from sqlalchemy import text
from telethon import TelegramClient
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

from tgsentinel.config import ProfileDefinition

# Import message formats module for template rendering
from tgsentinel.message_formats import (
    render_digest_entry,
    render_digest_header,
)

log = logging.getLogger(__name__)


def build_digest_query(feed_type: str, manual_trigger: bool = False) -> str:
    """Build digest query with the appropriate feed flag column.

    Args:
        feed_type: Either 'alerts' or 'interests'
        manual_trigger: If True, get latest N messages without any filtering

    Returns:
        SQL query string with the correct flag column
    """
    flag_column = (
        "flagged_for_alerts_feed"
        if feed_type == "alerts"
        else "flagged_for_interest_feed"
    )

    # For manual triggers: get latest N messages from feed, no filtering at all
    # For scheduled digests: apply min_score threshold and time window
    if manual_trigger:
        return f"""
SELECT
    chat_id,
    msg_id,
    score,
    keyword_score,
    semantic_scores_json,
    chat_title,
    sender_name,
    message_text,
    triggers,
    trigger_annotations,
    matched_profiles,
    created_at
FROM messages
WHERE {flag_column} = 1
ORDER BY created_at DESC
LIMIT :limit
"""
    else:
        return f"""
SELECT
    chat_id,
    msg_id,
    score,
    keyword_score,
    semantic_scores_json,
    chat_title,
    sender_name,
    message_text,
    triggers,
    trigger_annotations,
    matched_profiles,
    created_at
FROM messages
WHERE {flag_column} = 1
  AND created_at >= :since
  AND score >= :min_score
ORDER BY score DESC
LIMIT :limit
"""


# Only refresh the Telethon dialogs cache occasionally to avoid heavy calls.
_DIALOG_REFRESH_INTERVAL_SECONDS = 300  # 5 minutes
_last_dialog_refresh_ts: float | None = None


async def _fetch_message_with_entity_retry(
    client: TelegramClient, chat_id: int, msg_id: int, timeout_seconds: float = 5.0
):
    """Fetch a message, refreshing entity cache when necessary.

    Args:
        client: Telethon client
        chat_id: Chat ID to fetch from
        msg_id: Message ID to fetch
        timeout_seconds: Maximum time to spend fetching this message (default: 5s)

    Returns:
        Message object or None if fetch times out or fails
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            try:
                raw_result = await client.get_messages(chat_id, ids=msg_id)
                return _normalize_result(raw_result)
            except ValueError as exc:
                if not await _hydrate_entity_cache(client, chat_id):
                    raise exc
                raw_result = await client.get_messages(chat_id, ids=msg_id)
                return _normalize_result(raw_result)
    except asyncio.TimeoutError:
        log.warning(
            "Timeout fetching message %s from chat %s after %.1fs",
            msg_id,
            chat_id,
            timeout_seconds,
        )
        return None
    except Exception as exc:
        log.debug("Failed to fetch message %s from chat %s: %s", msg_id, chat_id, exc)
        return None


async def _hydrate_entity_cache(client: TelegramClient, chat_id: int) -> bool:
    """Ensure Telethon knows about the target peer before fetching messages."""

    try:
        numeric_id = int(chat_id)
    except (TypeError, ValueError):
        return False

    chat_str = str(chat_id)
    if chat_str.startswith("-100"):
        peer = PeerChannel(int(chat_str[4:]))
    elif numeric_id < 0:
        peer = PeerChat(abs(numeric_id))
    else:
        peer = PeerUser(numeric_id)

    try:
        await client.get_entity(peer)
        return True
    except Exception as exc:
        log.warning("Entity hydration failed for %s: %s", chat_id, exc)

        # Attempt to refresh dialog cache once before giving up.
        if await _refresh_dialog_cache(client):
            try:
                await client.get_entity(peer)
                return True
            except Exception as retry_exc:
                log.warning(
                    "Entity hydration still failing for %s after dialog refresh: %s",
                    chat_id,
                    retry_exc,
                )

        return False


async def _refresh_dialog_cache(client: TelegramClient) -> bool:
    """Fetch dialogs sparingly to populate Telethon's entity cache."""

    global _last_dialog_refresh_ts

    now = time.monotonic()
    if (
        _last_dialog_refresh_ts is not None
        and now - _last_dialog_refresh_ts < _DIALOG_REFRESH_INTERVAL_SECONDS
    ):
        return False

    try:
        await client.get_dialogs(limit=200)
        _last_dialog_refresh_ts = now
        log.debug("Refreshed dialog cache for entity hydration")
        return True
    except Exception as exc:  # pragma: no cover - network edge cases
        log.warning("Dialog cache refresh failed: %s", exc)
        return False


def _normalize_result(result):
    if isinstance(result, list):
        return result[0] if result else None
    return result


async def send_digest(
    engine,
    client: TelegramClient,
    since_hours: int,
    top_n: int,
    mode: str,
    channel: str | None,
    channels_config: list | None = None,
    min_score: float = 0.0,
    feed_type: str = "interests",
    manual_trigger: bool = False,
    global_profiles: Optional[Dict[str, ProfileDefinition]] = None,
):
    """
    Send a digest of top alerted messages with full details.

    Args:
        channels_config: List of ChannelRule objects with id and name
        min_score: Minimum score threshold for messages to include in digest (default: 0.0)
        feed_type: Type of feed to digest - 'alerts' or 'interests' (default: 'interests')
        manual_trigger: If True, fetch latest messages without score filtering (default: False)
        global_profiles: Dict mapping profile_id -> ProfileDefinition for name lookup
    """
    # Validate mode early (dm, digest, both - 'channel' is deprecated)
    if mode not in ("dm", "digest", "both"):
        log.warning(
            f"Invalid digest mode '{mode}'. Expected 'dm', 'digest', or 'both'. "
            "Digest not sent. Please update config.yml alerts.mode"
        )
        return

    # Validate channel target when required for digest/both modes
    if mode in ("digest", "both") and not channel:
        log.warning(
            "Digest channel target missing while mode=%s; falling back to DM only",
            mode,
        )
        mode = "dm"

    now_utc = dt.datetime.now(dt.UTC)
    since = (now_utc - dt.timedelta(hours=since_hours)).replace(tzinfo=None)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")

    # Create a mapping of chat_id to channel name
    chat_names = {}
    if channels_config:
        for ch in channels_config:
            chat_names[ch.id] = ch.name

    # Build the appropriate query based on feed type
    digest_query = build_digest_query(feed_type, manual_trigger)

    with engine.begin() as con:
        if manual_trigger:
            # Manual trigger: no since/min_score filtering
            rows = con.execute(
                text(digest_query),
                {"limit": top_n},
            ).fetchall()
        else:
            # Scheduled digest: use since/min_score filters
            rows = con.execute(
                text(digest_query),
                {"since": since_str, "limit": top_n, "min_score": min_score},
            ).fetchall()
    if not rows:
        log.info(
            "No messages to include in %s digest (manual=%s)",
            feed_type,
            manual_trigger,
        )
        return

    # Count unique channels for the header
    unique_chat_ids = set(r.chat_id for r in rows)
    channel_count = len(unique_chat_ids)

    # Build header using message formats renderer
    # Determine digest type for header
    if manual_trigger:
        digest_type = (
            "Interests Digest" if feed_type == "interests" else "Alerts Digest"
        )
        schedule = "Manual"
    else:
        digest_type = (
            "Interests Digest" if feed_type == "interests" else "Alerts Digest"
        )
        schedule = f"last {since_hours}h"
    header = render_digest_header(
        top_n=len(rows),  # Use actual message count, not limit parameter
        channel_count=channel_count,
        schedule=schedule,
        digest_type=digest_type,
        timestamp=now_utc.isoformat(),
    )
    lines = [header, ""]

    log.info(
        f"[DIGEST] Building {feed_type} digest with {len(rows)} messages from {channel_count} channels"
    )

    for idx, r in enumerate(rows, 1):
        chat_id = r.chat_id
        msg_id = r.msg_id
        score = r.score

        log.debug(
            f"[DIGEST] Processing message {idx}/{len(rows)}: chat_id={chat_id}, msg_id={msg_id}"
        )

        # Get stored data from database as fallback
        stored_chat_title = r.chat_title if hasattr(r, "chat_title") else None
        stored_sender_name = r.sender_name if hasattr(r, "sender_name") else None
        stored_message_text = r.message_text if hasattr(r, "message_text") else None
        stored_triggers = r.triggers if hasattr(r, "triggers") else None
        trigger_annotations_json = (
            r.trigger_annotations if hasattr(r, "trigger_annotations") else ""
        )

        # Get channel name - prioritize config, then stored title, then fallback
        chat_name = (
            chat_names.get(chat_id)
            or (stored_chat_title if stored_chat_title else None)
            or f"Chat {chat_id}"
        )

        # Create Telegram link
        if str(chat_id).startswith("-100"):
            # Private channel/supergroup - remove -100 prefix
            clean_id = str(chat_id)[4:]
            msg_link = f"https://t.me/c/{clean_id}/{msg_id}"
        else:
            # Regular chat or group
            msg_link = f"tg://openmessage?chat_id={chat_id}&message_id={msg_id}"

        # Try to fetch fresh message details from Telegram first
        sender_name = stored_sender_name or "Unknown"
        msg_text = stored_message_text or ""

        try:
            message = await _fetch_message_with_entity_retry(client, chat_id, msg_id)

            if message:
                log.debug(
                    f"[DIGEST] Successfully fetched message {msg_id} from Telegram"
                )
                # Extract sender information
                sender = getattr(message, "sender", None)
                if sender:
                    if hasattr(sender, "first_name"):
                        sender_name = sender.first_name or "Unknown"
                        if hasattr(sender, "last_name") and sender.last_name:
                            sender_name += f" {sender.last_name}"
                    elif hasattr(sender, "title"):
                        sender_name = sender.title or "Unknown"

                # Extract message text (truncate if too long)
                fetched_text = (
                    getattr(message, "text", None)
                    or getattr(message, "message", None)
                    or ""
                )

                # If we got text from Telegram, use it; otherwise use stored
                if fetched_text:
                    msg_text = fetched_text
                elif not msg_text and hasattr(message, "media"):
                    # Has media but no text
                    msg_text = "[Media/Photo/Document]"

                if len(msg_text) > 150:
                    msg_text = msg_text[:150] + "..."
                msg_text = msg_text.replace("\n", " ")

        except Exception as e:
            log.warning(
                "[DIGEST] Could not fetch message %s from chat %s, using stored data: %s",
                msg_id,
                chat_id,
                str(e)[:100],
            )
            # Will use stored data from database (already set above)

        # Use stored data if we couldn't fetch or no text was found
        if not msg_text:
            if stored_triggers and stored_triggers.startswith("media-"):
                # Media message
                media_type = stored_triggers.replace("media-", "").replace(
                    "MessageMedia", ""
                )
                msg_text = f"[{media_type}]"
            else:
                msg_text = "[No content available]"

        # Build the digest entry (compact format, no extra newlines)
        # Parse trigger annotations for passing to renderer
        parsed_triggers = []
        if trigger_annotations_json:
            try:
                annotations = json.loads(trigger_annotations_json)
                for category, keywords in annotations.items():
                    if isinstance(keywords, list):
                        for kw in keywords[:3]:
                            parsed_triggers.append((category, str(kw)))
            except (json.JSONDecodeError, TypeError):
                pass

        # Look up profile name(s) from matched_profiles
        entry_profile_name = None
        matched_profiles_json = (
            r.matched_profiles if hasattr(r, "matched_profiles") else None
        )
        if matched_profiles_json:
            try:
                matched_profile_ids = json.loads(matched_profiles_json)
                if matched_profile_ids and isinstance(matched_profile_ids, list):
                    profile_names = []
                    for prof_id in matched_profile_ids[:2]:  # Show up to 2 profiles
                        prof_id_str = str(prof_id)
                        if global_profiles and prof_id_str in global_profiles:
                            profile = global_profiles[prof_id_str]
                            name = profile.name or prof_id_str
                            profile_names.append(name)
                        else:
                            # Fallback to profile ID if name not found
                            profile_names.append(prof_id_str)
                    if profile_names:
                        entry_profile_name = ", ".join(profile_names)
            except (json.JSONDecodeError, TypeError):
                pass

        # Extract keyword_score and semantic_score from database if available
        keyword_score_value = r.keyword_score if hasattr(r, "keyword_score") else None
        semantic_score_value = None

        # Extract semantic score from semantic_scores_json if present
        if hasattr(r, "semantic_scores_json") and r.semantic_scores_json:
            try:
                semantic_scores = json.loads(r.semantic_scores_json)
                if semantic_scores and isinstance(semantic_scores, dict):
                    # Get max semantic score across all profiles
                    semantic_score_value = max(semantic_scores.values())
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        entry = render_digest_entry(
            rank=idx,
            chat_title=f"[{chat_name}]({msg_link})",
            message_text=msg_text,
            sender_name=sender_name,  # Pass raw sender name
            score=score,
            triggers=parsed_triggers if parsed_triggers else None,
            max_preview_length=150,
            # Additional variables for complete rendering
            sender_id=None,  # Not available in current schema
            keyword_score=keyword_score_value,
            semantic_score=semantic_score_value,
            timestamp=r.created_at,  # Pass actual timestamp from database
            message_link=msg_link,
            chat_id=chat_id,
            msg_id=msg_id,
            reactions=None,  # Not tracked in digests currently
            is_vip=False,  # TODO: Could be determined from VIP list
            profile_name=entry_profile_name,
            profile_id=None,
        )

        lines.append(entry)

    msg_text = "\n".join(lines)

    # Telegram message limit is 4096 characters
    MAX_MESSAGE_LENGTH = 4000  # Leave some margin

    # Split into chunks if too long
    if len(msg_text) > MAX_MESSAGE_LENGTH:
        log.info(f"Digest is {len(msg_text)} chars, splitting into multiple messages")
        chunks = []
        current_chunk = lines[0]  # Start with header

        for line in lines[1:]:  # Skip header
            if len(current_chunk) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += "\n" + line

        if current_chunk:
            chunks.append(current_chunk)

        log.info(f"Split digest into {len(chunks)} parts")
    else:
        chunks = [msg_text]

    log.info(
        f"Sending digest with {len(rows)} messages in {len(chunks)} part(s) (mode={mode})"
    )

    try:
        if mode in ("dm", "both"):
            for i, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    # Add part indicator for multi-part messages
                    part_header = f"[Part {i+1}/{len(chunks)}]\n" if i > 0 else ""
                    await client.send_message(
                        "me", part_header + chunk, link_preview=False
                    )
                else:
                    await client.send_message("me", chunk, link_preview=False)
            log.info("Digest sent to DM (Saved Messages)")

        if mode in ("digest", "both") and channel:
            for i, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    part_header = f"[Part {i+1}/{len(chunks)}]\n" if i > 0 else ""
                    await client.send_message(
                        channel, part_header + chunk, link_preview=False
                    )
                else:
                    await client.send_message(channel, chunk, link_preview=False)
            log.info(f"Digest sent to channel {channel}")

    except Exception as e:
        log.error(f"Failed to send digest: {e}", exc_info=True)
