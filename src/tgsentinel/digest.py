import datetime as dt
import logging

from sqlalchemy import text
from telethon import TelegramClient

log = logging.getLogger(__name__)

DIGEST_QUERY = """
SELECT chat_id, msg_id, score FROM messages
WHERE alerted=1 AND created_at >= :since
ORDER BY score DESC
LIMIT :limit
"""


async def send_digest(
    engine,
    client: TelegramClient,
    since_hours: int,
    top_n: int,
    mode: str,
    channel: str,
    channels_config: list | None = None,
):
    """
    Send a digest of top alerted messages with full details.

    Args:
        channels_config: List of ChannelRule objects with id and name
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
            f"Digest mode '{mode}' requires a channel, but none provided. "
            "Digest not sent. Please update config.yml alerts.channel"
        )
        return

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
            text(DIGEST_QUERY), {"since": since_str, "limit": top_n}
        ).fetchall()
    if not rows:
        log.info("No messages to include in digest")
        return

    lines = [f"🗞️ **Digest — Top {top_n} highlights** (last {since_hours}h)\n"]

    for idx, r in enumerate(rows, 1):
        chat_id = r.chat_id
        msg_id = r.msg_id
        score = r.score

        # Get channel name or use chat_id as fallback
        chat_name = chat_names.get(chat_id, f"Chat {chat_id}")

        # Create Telegram link
        if str(chat_id).startswith("-100"):
            # Private channel/supergroup - remove -100 prefix
            clean_id = str(chat_id)[4:]
            msg_link = f"https://t.me/c/{clean_id}/{msg_id}"
        else:
            # Regular chat or group
            msg_link = f"tg://openmessage?chat_id={chat_id}&message_id={msg_id}"

        # Try to fetch message details from Telegram
        try:
            result = await client.get_messages(chat_id, ids=msg_id)
            message = (
                result
                if not isinstance(result, list)
                else (result[0] if result else None)
            )

            if message and hasattr(message, "text"):
                # Extract sender information
                sender_name = "Unknown"
                sender = getattr(message, "sender", None)
                if sender:
                    if hasattr(sender, "first_name"):
                        sender_name = sender.first_name or "Unknown"
                        if hasattr(sender, "last_name") and sender.last_name:
                            sender_name += f" {sender.last_name}"
                    elif hasattr(sender, "title"):
                        sender_name = sender.title or "Unknown"

                # Extract message text (truncate if too long)
                msg_text = (
                    getattr(message, "text", None)
                    or getattr(message, "message", None)
                    or "[Media/Sticker/File]"
                )
                if len(msg_text) > 150:
                    msg_text = msg_text[:150] + "..."
                msg_text = msg_text.replace("\n", " ")

                # Format timestamp
                msg_time = (
                    message.date.strftime("%H:%M")
                    if hasattr(message, "date") and message.date
                    else "Unknown time"
                )

                # Build the digest entry with full details
                lines.append(
                    f"\n**{idx}. [{chat_name}]({msg_link})** — Score: {score:.2f}\n"
                    f"   👤 {sender_name} • 🕐 {msg_time}\n"
                    f"   💬 _{msg_text}_"
                )
            else:
                # Fallback if message fetch fails
                lines.append(
                    f"\n**{idx}. [{chat_name}]({msg_link})** — Score: {score:.2f}\n"
                    f"   _(Message details unavailable)_"
                )

        except Exception as e:
            log.warning(f"Failed to fetch message {msg_id} from {chat_id}: {e}")
            # Fallback entry without details
            lines.append(
                f"\n**{idx}. [{chat_name}]({msg_link})** — Score: {score:.2f}\n"
                f"   _(Could not fetch message details)_"
            )

    msg_text = "\n".join(lines)

    log.info(f"Sending digest with {len(rows)} messages (mode={mode})")

    if mode in ("dm", "both"):
        await client.send_message("me", msg_text, link_preview=False)
        log.info("Digest sent to DM (Saved Messages)")

    if mode in ("channel", "both") and channel:
        await client.send_message(channel, msg_text, link_preview=False)
        log.info(f"Digest sent to channel {channel}")
