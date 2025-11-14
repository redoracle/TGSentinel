import asyncio
import json
import logging
import os

from redis import Redis
from telethon import TelegramClient, events

from .config import AppCfg

log = logging.getLogger(__name__)


def make_client(cfg: AppCfg) -> TelegramClient:
    client = TelegramClient(cfg.telegram_session, cfg.api_id, cfg.api_hash)
    return client


def _reaction_count(msg) -> int:
    rs = getattr(msg, "reactions", None)
    if not rs or not rs.results:
        return 0
    return sum([r.count for r in rs.results])


async def _cache_avatar(
    client: TelegramClient, entity_id: int, photo, r: Redis
) -> str | None:
    """Cache avatar for user or chat entity.

    Args:
        client: Telegram client
        entity_id: User ID or Chat ID
        photo: Photo object from entity
        r: Redis client

    Returns:
        Avatar URL if successfully cached, None otherwise
    """
    if not photo:
        return None

    try:
        # Determine if this is a chat (negative ID) or user (positive ID)
        is_chat = entity_id < 0
        prefix = "chat" if is_chat else "user"
        cache_key = f"tgsentinel:{prefix}_avatar:{entity_id}"
        data_dir = os.path.join("/app", "data", "avatars")
        avatar_file = os.path.join(data_dir, f"{prefix}_{entity_id}.jpg")
        avatar_url = f"/data/avatars/{prefix}_{entity_id}.jpg"

        # Check both Redis cache and filesystem existence
        redis_cached = r.exists(cache_key)
        file_exists = os.path.exists(avatar_file)

        # If file exists but Redis key is missing, restore Redis cache
        if file_exists and not redis_cached:
            try:
                r.setex(cache_key, 3600, avatar_url)  # Cache for 1 hour
                # For private chats (positive IDs), set a chat_avatar alias too
                if not is_chat:
                    r.setex(f"tgsentinel:chat_avatar:{entity_id}", 3600, avatar_url)
                log.debug(
                    "Restored Redis cache for existing avatar: %s %s", prefix, entity_id
                )
            except Exception as cache_err:
                log.debug("Could not restore Redis cache: %s", cache_err)

        # Download if either Redis or file is missing
        if not redis_cached or not file_exists:
            try:
                # Ensure data directory exists
                os.makedirs(data_dir, exist_ok=True)

                # Download to temporary file for atomic operation
                temp_file = f"{avatar_file}.tmp"

                # Download profile photo
                await client.download_profile_photo(entity_id, file=temp_file)

                # Atomically rename to final location
                try:
                    os.replace(temp_file, avatar_file)

                    # Only set Redis key after successful file write
                    r.setex(cache_key, 3600, avatar_url)  # Cache for 1 hour
                    # For private chats (positive IDs), also expose under chat_avatar key
                    if not is_chat:
                        r.setex(f"tgsentinel:chat_avatar:{entity_id}", 3600, avatar_url)
                    log.debug("Cached avatar for %s %s", prefix, entity_id)
                except Exception as rename_err:
                    # Clean up temp file if rename failed
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    except Exception:
                        pass
                    raise rename_err

            except Exception as photo_err:
                log.debug("Could not download profile photo: %s", photo_err)
                return None

        return avatar_url

    except Exception as avatar_err:
        log.debug("Could not cache avatar: %s", avatar_err)
        return None


def _safe_get_reply_to_id(message):
    """Safely extract reply_to_msg_id, handling None and mock objects."""
    try:
        reply_to = getattr(message, "reply_to", None)
        if reply_to is None:
            return None
        # Check if it's a mock object (has _mock_name attribute)
        if hasattr(reply_to, "_mock_name"):
            return None
        reply_to_id = getattr(reply_to, "reply_to_msg_id", None)
        return int(reply_to_id) if reply_to_id is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _safe_get_media_type(message):
    """Safely extract media type, handling None and mock objects."""
    try:
        media = getattr(message, "media", None)
        if media is None:
            return None
        # Check if it's a mock object
        if hasattr(media, "_mock_name"):
            return None
        return media.__class__.__name__ if hasattr(media, "__class__") else None
    except (AttributeError, TypeError):
        return None


def _safe_get_forward_from(message):
    """Safely extract forward from user ID, handling None and mock objects."""
    try:
        forward = getattr(message, "forward", None)
        if forward is None:
            return None
        # Check if it's a mock object
        if hasattr(forward, "_mock_name"):
            return None
        if hasattr(forward, "from_id") and hasattr(forward.from_id, "user_id"):
            return int(forward.from_id.user_id)
        return None
    except (AttributeError, TypeError, ValueError):
        return None


def start_ingestion(cfg: AppCfg, client, r: Redis) -> None:
    stream = cfg.redis["stream"]

    listener = events.NewMessage()

    async def handler(event):
        m = event.message
        try:
            # Fetch sender entity to get proper name information
            sender_name = ""
            sender_id = m.sender_id
            # Track cached avatar URLs to include in payload for UI
            sender_avatar_url = None
            chat_avatar_url = None

            try:
                sender = await event.get_sender()
                if sender:
                    if hasattr(sender, "first_name"):
                        # Build name from non-empty parts to avoid leading/trailing spaces
                        name_parts = []
                        if sender.first_name:
                            name_parts.append(sender.first_name)
                        if hasattr(sender, "last_name") and sender.last_name:
                            name_parts.append(sender.last_name)
                        sender_name = " ".join(name_parts)
                    elif hasattr(sender, "title"):
                        sender_name = sender.title or ""
                    elif hasattr(sender, "username"):
                        sender_name = f"@{sender.username}" if sender.username else ""

                    # Try to cache avatar URL if user has a profile photo
                    if (
                        sender_id is not None
                        and hasattr(sender, "photo")
                        and sender.photo
                    ):
                        try:
                            avatar_url = await _cache_avatar(
                                client, sender_id, sender.photo, r
                            )
                            sender_avatar_url = avatar_url or sender_avatar_url
                        except Exception as avatar_err:
                            log.debug("Could not cache avatar: %s", avatar_err)
            except Exception as sender_err:
                log.debug("Could not fetch sender: %s", sender_err)

            # Fetch chat entity to get proper chat title and cache chat type
            chat_title = ""
            try:
                chat = await event.get_chat()
                if chat:
                    if hasattr(chat, "title") and chat.title:
                        chat_title = chat.title
                    elif hasattr(chat, "first_name") and chat.first_name:
                        # For private chats, use first_name
                        chat_title = chat.first_name
                        if hasattr(chat, "last_name") and chat.last_name:
                            chat_title += f" {chat.last_name}"
                    elif hasattr(chat, "username") and chat.username:
                        chat_title = f"@{chat.username}"

                    # Try to cache chat avatar if chat has a profile photo
                    if (
                        event.chat_id is not None
                        and hasattr(chat, "photo")
                        and chat.photo
                    ):
                        try:
                            chat_avatar_url = await _cache_avatar(
                                client, event.chat_id, chat.photo, r
                            )
                        except Exception as chat_avatar_err:
                            log.debug(
                                "Could not cache chat avatar: %s", chat_avatar_err
                            )

                    # Cache chat type for better UI display
                    try:
                        from telethon.tl.types import Channel, Chat as TgChat

                        chat_type = "unknown"
                        if isinstance(chat, Channel):
                            if getattr(chat, "broadcast", False):
                                chat_type = "channel"
                            elif getattr(chat, "megagroup", False):
                                chat_type = "supergroup"
                            elif getattr(chat, "gigagroup", False):
                                chat_type = "gigagroup"
                            else:
                                chat_type = "channel"  # Default for Channel type
                        elif isinstance(chat, TgChat):
                            chat_type = "group"

                        # Cache for 24 hours (chat type doesn't change often)
                        cache_key = f"tgsentinel:chat_type:{event.chat_id}"
                        r.setex(cache_key, 86400, chat_type)
                        log.debug(
                            "Cached chat type for %s: %s", event.chat_id, chat_type
                        )
                    except Exception as type_err:
                        log.debug("Could not cache chat type: %s", type_err)
            except Exception as chat_err:
                log.debug("Could not fetch chat: %s", chat_err)

            payload = {
                "chat_id": event.chat_id,
                "chat_title": chat_title,
                "msg_id": m.id,
                "sender_id": m.sender_id,
                "sender_name": sender_name,
                "mentioned": bool(m.mentioned),
                "text": (m.message or ""),
                "replies": int(m.replies.replies if m.replies is not None else 0),
                "reactions": _reaction_count(m),
                "timestamp": m.date.isoformat() if m.date else None,
                # Provide avatar URLs directly for UI as a best-effort hint
                # UI will still fall back to Redis lookup if missing
                "avatar_url": sender_avatar_url or chat_avatar_url,
                "chat_avatar_url": chat_avatar_url,
                # Enhanced metadata for comprehensive heuristics
                "is_reply": bool(getattr(m, "is_reply", False)),
                "reply_to_msg_id": _safe_get_reply_to_id(m),
                "has_media": bool(getattr(m, "media", None))
                and not hasattr(getattr(m, "media", None), "_mock_name"),
                "media_type": _safe_get_media_type(m),
                "is_pinned": bool(getattr(m, "pinned", False)),
                "has_forward": bool(getattr(m, "forward", None))
                and not hasattr(getattr(m, "forward", None), "_mock_name"),
                "forward_from": _safe_get_forward_from(m),
            }

            # Filter out messages from the current user (don't track own messages)
            try:
                current_user_str = r.get("tgsentinel:user_info")
                if current_user_str:
                    # Ensure we have a string
                    if isinstance(current_user_str, bytes):
                        current_user_str = current_user_str.decode()
                    current_user = json.loads(str(current_user_str))
                    current_user_id = current_user.get("user_id")
                    if current_user_id and m.sender_id == current_user_id:
                        log.debug("Skipping own message in chat %s", event.chat_id)
                        return
            except Exception as filter_err:
                log.debug("Could not filter own messages: %s", filter_err)

            # Check if this is a private chat (positive chat_id)
            # and filter based on monitored_users list
            if event.chat_id > 0:
                # This is a private chat - check if user is in monitored list
                monitored_ids = {u.id for u in cfg.monitored_users}
                if monitored_ids and event.chat_id not in monitored_ids:
                    log.debug(
                        "Skipping private message from unmonitored user %s",
                        event.chat_id,
                    )
                    return

            r.xadd(
                stream, {"json": json.dumps(payload)}, maxlen=100000, approximate=True
            )
        except Exception as e:
            log.exception("ingest_error: %s", e)

    on_method = getattr(client, "on", None)
    registered = False
    if callable(on_method):
        decorator = on_method(listener)
        if callable(decorator):
            decorator(handler)
            registered = True
        elif asyncio.iscoroutine(decorator):
            decorator.close()

    if not registered:
        add_handler = getattr(client, "add_event_handler", None)
        if callable(add_handler):
            maybe_coro = add_handler(handler, listener)
            if asyncio.iscoroutine(maybe_coro):
                maybe_coro.close()
            registered = True

    if not registered:
        raise RuntimeError("Could not register ingestion handler")
