import asyncio
import json
import logging
import os

from redis import Redis
from telethon import TelegramClient, events

from .config import AppCfg
from pathlib import Path

log = logging.getLogger(__name__)


def _resolve_session_path(cfg: AppCfg) -> str:
    """Resolve a usable session file path for the worker.

    Priority:
    - TG_SESSION_PATH env (validated for writable parent directory)
    - /app/data/tgsentinel.session (shared volume in containers, if parent dir exists)
    - repo data fallback: ../data/tgsentinel.session (if it exists)
    - cfg.telegram_session
    """
    # Env override - validate parent directory exists and is writable
    env_path = os.getenv("TG_SESSION_PATH")
    if env_path:
        # Expand and normalize the path
        normalized_path = os.path.abspath(os.path.expanduser(env_path))
        parent_dir = os.path.dirname(normalized_path)

        # Validate parent directory exists and is writable
        if not os.path.exists(parent_dir):
            log.error(
                "TG_SESSION_PATH parent directory does not exist: %s (falling back to container path)",
                parent_dir,
            )
        elif not os.access(parent_dir, os.W_OK):
            log.error(
                "TG_SESSION_PATH parent directory is not writable: %s (falling back to container path)",
                parent_dir,
            )
        else:
            # Valid path with writable parent directory
            return normalized_path

    # Common container path (prefer this if parent directory exists)
    container_path = "/app/data/tgsentinel.session"
    container_dir = os.path.dirname(container_path)
    if os.path.exists(container_dir):
        return container_path

    # Repo data fallback (when running locally)
    try:
        repo_fallback = (
            Path(__file__).resolve().parents[2] / "data" / "tgsentinel.session"
        )
        if os.path.exists(os.path.dirname(str(repo_fallback))):
            return str(repo_fallback)
    except IndexError as exc:
        # parents[2] might not exist if __file__ path is too shallow
        log.debug(
            "Could not resolve repo fallback path (insufficient parent directories): %s",
            exc,
        )
    except (OSError, TypeError) as exc:
        # Filesystem or path type errors
        log.debug(
            "Could not resolve repo fallback path (filesystem or type error): %s",
            exc,
        )
    except Exception as exc:
        # Catch-all for unexpected issues
        log.debug(
            "Unexpected error resolving repo fallback path: %s",
            exc,
        )

    return cfg.telegram_session


_logged_session_once = False


def make_client(cfg: AppCfg) -> TelegramClient:
    session_path = _resolve_session_path(cfg)
    global _logged_session_once

    if not _logged_session_once:
        log.info("Using session file: %s | api_id=%s", session_path, cfg.api_id)
        _logged_session_once = True
    else:
        log.debug("Using session file: %s | api_id=%s", session_path, cfg.api_id)

    client = TelegramClient(session_path, cfg.api_id, cfg.api_hash)

    # Enable WAL mode for session database to prevent corruption during concurrent operations
    # WAL (Write-Ahead Logging) allows multiple readers and one writer without blocking
    try:
        import sqlite3

        session_file = Path(session_path)
        if session_file.exists():
            # Set permissions for multi-container access
            os.chmod(session_file, 0o660)
            # Enable WAL mode
            conn = sqlite3.connect(str(session_file))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "PRAGMA synchronous=NORMAL"
            )  # Balance between safety and performance
            conn.close()
            log.debug("Enabled WAL mode for session database: %s", session_path)
    except Exception as exc:
        log.warning("Could not enable WAL mode for session: %s", exc)

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
    import base64
    import io

    if not photo:
        return None

    try:
        # Determine if this is a chat (negative ID) or user (positive ID)
        is_chat = entity_id < 0
        prefix = "chat" if is_chat else "user"
        cache_key = f"tgsentinel:{prefix}_avatar:{entity_id}"
        avatar_url = f"/api/avatar/{prefix}/{abs(entity_id)}"

        # Check if avatar is already cached in Redis
        redis_cached = r.exists(cache_key)

        # Download if not in Redis
        if not redis_cached:
            try:
                # Download to memory instead of filesystem
                avatar_bytes = io.BytesIO()
                await client.download_profile_photo(entity_id, file=avatar_bytes)

                # Encode as base64 and store in Redis
                avatar_bytes.seek(0)
                avatar_b64 = base64.b64encode(avatar_bytes.read()).decode("utf-8")
                r.set(cache_key, avatar_b64)  # No TTL

                # For private chats (positive IDs), also expose under chat_avatar key
                if not is_chat:
                    r.set(f"tgsentinel:chat_avatar:{entity_id}", avatar_b64)  # No TTL

                log.debug("Cached avatar in Redis for %s %s", prefix, entity_id)

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
    log.info("Starting message ingestion handler (stream=%s)", stream)

    # Listen for INCOMING messages (not outgoing)
    listener = events.NewMessage(incoming=True, outgoing=False)

    async def handler(event):
        m = event.message
        log.info(
            "Received new message: chat_id=%s, sender_id=%s, msg_id=%s",
            event.chat_id,
            m.sender_id,
            m.id,
        )
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

            # Fallback: if sender_name is still empty and we have sender_id, try getting entity directly
            if not sender_name and sender_id:
                try:
                    # Try to get entity from client cache or fetch it
                    entity = await client.get_entity(sender_id)
                    if entity:
                        if hasattr(entity, "first_name"):
                            name_parts = []
                            if entity.first_name:
                                name_parts.append(entity.first_name)
                            if hasattr(entity, "last_name") and entity.last_name:
                                name_parts.append(entity.last_name)
                            sender_name = " ".join(name_parts)
                        elif hasattr(entity, "title"):
                            sender_name = entity.title or ""
                        elif hasattr(entity, "username"):
                            sender_name = (
                                f"@{entity.username}" if entity.username else ""
                            )
                except Exception as entity_err:
                    log.debug(
                        "Could not fetch entity by ID %s: %s", sender_id, entity_err
                    )

            # Last resort: check Redis participant cache
            if not sender_name and sender_id and event.chat_id:
                try:
                    cache_key = f"tgsentinel:participant:{event.chat_id}:{sender_id}"
                    cached = r.get(cache_key)
                    if cached:
                        # Ensure we have a string for json.loads
                        cached_str: str
                        if isinstance(cached, bytes):
                            cached_str = cached.decode("utf-8")
                        elif isinstance(cached, str):
                            cached_str = cached
                        else:
                            cached_str = str(cached)
                        participant_info = json.loads(cached_str)
                        sender_name = participant_info.get("name", "")
                except Exception as cache_err:
                    log.debug("Could not get sender name from cache: %s", cache_err)

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

            # Fallback: if chat_title is still empty, try getting entity directly
            if not chat_title and event.chat_id:
                try:
                    # Try to get entity from client cache or fetch it
                    entity = await client.get_entity(event.chat_id)
                    if entity:
                        if hasattr(entity, "title") and entity.title:
                            chat_title = entity.title
                        elif hasattr(entity, "first_name") and entity.first_name:
                            chat_title = entity.first_name
                            if hasattr(entity, "last_name") and entity.last_name:
                                chat_title += f" {entity.last_name}"
                        elif hasattr(entity, "username"):
                            chat_title = (
                                f"@{entity.username}" if entity.username else ""
                            )
                except Exception as entity_err:
                    log.debug(
                        "Could not fetch chat entity by ID %s: %s",
                        event.chat_id,
                        entity_err,
                    )

            # For private chats, if chat_title is still empty, use sender_name as fallback
            if not chat_title and event.chat_id > 0 and sender_name:
                chat_title = sender_name

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
                log.info(
                    "Private chat check: chat_id=%s, monitored_ids=%s",
                    event.chat_id,
                    monitored_ids,
                )
                if monitored_ids and event.chat_id not in monitored_ids:
                    log.info(
                        "Skipping private message from unmonitored user %s (not in %s)",
                        event.chat_id,
                        monitored_ids,
                    )
                    return
                log.info(
                    "Private message ALLOWED from monitored user %s", event.chat_id
                )

            r.xadd(
                stream, {"json": json.dumps(payload)}, maxlen=100000, approximate=True
            )
            log.info(
                "Message ingested: chat=%s, sender=%s (%s)",
                chat_title or event.chat_id,
                sender_name or m.sender_id,
                m.id,
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

    log.info("Message ingestion handler registered successfully")
