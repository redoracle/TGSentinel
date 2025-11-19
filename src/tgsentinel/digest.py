import datetime as dt
import logging
import time

from sqlalchemy import text
from telethon import TelegramClient
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

log = logging.getLogger(__name__)

DIGEST_QUERY = """
SELECT 
    chat_id, 
    msg_id, 
    score,
    chat_title,
    sender_name,
    message_text,
    triggers,
    created_at
FROM messages
WHERE alerted = 1 
  AND created_at >= :since
  AND score >= :min_score
ORDER BY score DESC
LIMIT :limit
"""

# Only refresh the Telethon dialogs cache occasionally to avoid heavy calls.
_DIALOG_REFRESH_INTERVAL_SECONDS = 300  # 5 minutes
_last_dialog_refresh_ts: float | None = None


async def _fetch_message_with_entity_retry(
    client: TelegramClient, chat_id: int, msg_id: int
):
    """Fetch a message, refreshing entity cache when necessary."""

    try:
        raw_result = await client.get_messages(chat_id, ids=msg_id)
        return _normalize_result(raw_result)
    except ValueError as exc:
        if not await _hydrate_entity_cache(client, chat_id):
            raise exc
        raw_result = await client.get_messages(chat_id, ids=msg_id)
        return _normalize_result(raw_result)


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
    channel: str,
    channels_config: list | None = None,
    min_score: float = 0.0,
):
    """
    Send a digest of top alerted messages with full details.

    Args:
        channels_config: List of ChannelRule objects with id and name
        min_score: Minimum score threshold for messages to include in digest (default: 0.0)
    """
    # Validate mode early
    if mode not in ("dm", "channel", "both"):
        log.warning(
            f"Invalid digest mode '{mode}'. Expected 'dm', 'channel', or 'both'. "
            "Digest not sent. Please update config.yml alerts.mode"
        )
        return

    # Validate channel when required
    if mode in ("channel", "both") and not channel:
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

    with engine.begin() as con:
        rows = con.execute(
            text(DIGEST_QUERY),
            {"since": since_str, "limit": top_n, "min_score": min_score},
        ).fetchall()
    if not rows:
        log.info("No messages to include in digest (min_score=%.2f)", min_score)
        return

    lines = [f"🗞️ **Digest — Top {top_n} highlights** (last {since_hours}h)\n"]

    for idx, r in enumerate(rows, 1):
        chat_id = r.chat_id
        msg_id = r.msg_id
        score = r.score

        # Get stored data from database as fallback
        stored_chat_title = r.chat_title if hasattr(r, "chat_title") else None
        stored_sender_name = r.sender_name if hasattr(r, "sender_name") else None
        stored_message_text = r.message_text if hasattr(r, "message_text") else None
        stored_triggers = r.triggers if hasattr(r, "triggers") else None

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
        message_fetched = False
        sender_name = stored_sender_name or "Unknown"
        msg_text = stored_message_text or ""
        msg_time = "Unknown time"

        try:
            message = await _fetch_message_with_entity_retry(client, chat_id, msg_id)

            if message:
                message_fetched = True

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

                # Format timestamp
                msg_time = (
                    message.date.strftime("%H:%M")
                    if hasattr(message, "date") and message.date
                    else "Unknown time"
                )

        except Exception as e:
            log.debug(
                "Could not fetch live message %s from %s, using stored data: %s",
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
        lines.append(
            f"**{idx}. [{chat_name}]({msg_link})** — Score: {score:.2f}\n"
            f"👤 {sender_name} • 🕐 {msg_time}\n"
            f"💬 _{msg_text}_"
        )

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

        if mode in ("channel", "both") and channel:
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
