import asyncio
import json
import logging
import os
from pathlib import Path

from redis import Redis
from telethon import TelegramClient, events

from .config import AppCfg

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
        # Use format that matches UI expectations: tgsentinel:{prefix}_avatar:{entity_id}
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

                # Check if we got any data
                avatar_bytes.seek(0)
                avatar_data = avatar_bytes.read()
                if not avatar_data or len(avatar_data) == 0:
                    log.debug("Empty profile photo data for %s %s", prefix, entity_id)
                    return None

                # Encode as base64 and store in Redis
                avatar_b64 = base64.b64encode(avatar_data).decode("utf-8")
                r.set(cache_key, avatar_b64)  # No TTL
                log.info(
                    "✓ Cached avatar in Redis for %s %s (%d bytes)",
                    prefix,
                    entity_id,
                    len(avatar_data),
                )

            except Exception as photo_err:
                log.warning(
                    "Could not download profile photo for %s %s: %s",
                    prefix,
                    entity_id,
                    photo_err,
                )
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
            getattr(event, "chat_id", None),
            getattr(m, "sender_id", None),
            getattr(m, "id", None),
        )

        # Defaults that keep ingestion resilient even when Telethon-specific
        # lookups fail (important for tests using simple MagicMock events).
        sender_name = ""
        sender_id = getattr(m, "sender_id", None)
        chat_title = ""
        sender_avatar_url = None
        chat_avatar_url = None

        # Best-effort enrichment; failures must not prevent ingestion.
        try:
            # Fetch sender entity
            get_sender = getattr(event, "get_sender", None)
            sender = None
            if callable(get_sender):
                # Support both async and sync implementations
                if asyncio.iscoroutinefunction(get_sender):
                    sender = await get_sender()
                else:
                    sender = get_sender()

            if sender:
                if hasattr(sender, "first_name"):
                    name_parts: list[str] = []
                    if getattr(sender, "first_name", None):
                        name_parts.append(sender.first_name)  # type: ignore[attr-defined]
                    if getattr(sender, "last_name", None):
                        name_parts.append(sender.last_name)  # type: ignore[attr-defined]
                    sender_name = " ".join(name_parts)
                elif hasattr(sender, "title"):
                    sender_name = getattr(sender, "title", "") or ""
                elif hasattr(sender, "username"):
                    username = getattr(sender, "username", "") or ""
                    sender_name = f"@{username}" if username else ""

                # Try to cache avatar URL if user has a profile photo
                if (
                    sender_id is not None
                    and hasattr(sender, "photo")
                    and getattr(sender, "photo")
                ):
                    try:
                        avatar_url = await _cache_avatar(
                            client, sender_id, sender.photo, r  # type: ignore[arg-type]
                        )
                        sender_avatar_url = avatar_url or sender_avatar_url
                    except Exception as avatar_err:
                        log.debug("Could not cache avatar: %s", avatar_err)

            # Fallback: if sender_name is still empty and we have sender_id, try getting entity directly
            if not sender_name and sender_id:
                try:
                    entity = await client.get_entity(sender_id)
                    if entity:
                        if hasattr(entity, "first_name"):
                            name_parts: list[str] = []
                            if getattr(entity, "first_name", None):
                                name_parts.append(entity.first_name)  # type: ignore[attr-defined]
                            if getattr(entity, "last_name", None):
                                name_parts.append(entity.last_name)  # type: ignore[attr-defined]
                            sender_name = " ".join(name_parts)
                        elif hasattr(entity, "title"):
                            sender_name = getattr(entity, "title", "") or ""
                        elif hasattr(entity, "username"):
                            username = getattr(entity, "username", "") or ""
                            sender_name = f"@{username}" if username else ""
                except Exception as entity_err:
                    log.debug(
                        "Could not fetch entity by ID %s: %s", sender_id, entity_err
                    )

            # Last resort: check Redis participant cache
            if not sender_name and sender_id and getattr(event, "chat_id", None):
                try:
                    cache_key = f"tgsentinel:participant:{event.chat_id}:{sender_id}"
                    cached = r.get(cache_key)
                    if cached:
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
            get_chat = getattr(event, "get_chat", None)
            chat = None
            if callable(get_chat):
                try:
                    if asyncio.iscoroutinefunction(get_chat):
                        chat = await get_chat()
                    else:
                        chat = get_chat()
                except Exception as chat_err:
                    log.debug("Could not fetch chat: %s", chat_err)

            if chat:
                if getattr(chat, "title", None):
                    chat_title = chat.title  # type: ignore[attr-defined]
                elif getattr(chat, "first_name", None):
                    chat_title = chat.first_name  # type: ignore[attr-defined]
                    if getattr(chat, "last_name", None):
                        chat_title += f" {chat.last_name}"  # type: ignore[attr-defined]
                elif getattr(chat, "username", None):
                    chat_title = f"@{chat.username}"  # type: ignore[attr-defined]

                # Try to cache chat avatar if chat has a profile photo
                if (
                    getattr(event, "chat_id", None) is not None
                    and hasattr(chat, "photo")
                    and getattr(chat, "photo")
                ):
                    try:
                        chat_avatar_url = await _cache_avatar(
                            client, event.chat_id, chat.photo, r  # type: ignore[arg-type]
                        )
                    except Exception as chat_avatar_err:
                        log.debug("Could not cache chat avatar: %s", chat_avatar_err)

                # Cache chat type for better UI display
                try:
                    from telethon.tl.types import Channel
                    from telethon.tl.types import Chat as TgChat

                    chat_type = "unknown"
                    if isinstance(chat, Channel):
                        if getattr(chat, "broadcast", False):
                            chat_type = "channel"
                        elif getattr(chat, "megagroup", False):
                            chat_type = "supergroup"
                        elif getattr(chat, "gigagroup", False):
                            chat_type = "gigagroup"
                        else:
                            chat_type = "channel"
                    elif isinstance(chat, TgChat):
                        chat_type = "group"

                    cache_key = f"tgsentinel:chat_type:{event.chat_id}"
                    r.setex(cache_key, 86400, chat_type)
                    log.debug("Cached chat type for %s: %s", event.chat_id, chat_type)
                except Exception as type_err:
                    log.debug("Could not cache chat type: %s", type_err)

            # Fallback: if chat_title is still empty, try getting entity directly
            if not chat_title and getattr(event, "chat_id", None):
                try:
                    entity = await client.get_entity(event.chat_id)
                    if entity:
                        if getattr(entity, "title", None):
                            chat_title = entity.title  # type: ignore[attr-defined]
                        elif getattr(entity, "first_name", None):
                            chat_title = entity.first_name  # type: ignore[attr-defined]
                            if getattr(entity, "last_name", None):
                                chat_title += f" {entity.last_name}"  # type: ignore[attr-defined]
                        elif hasattr(entity, "username"):
                            username = getattr(entity, "username", "") or ""
                            chat_title = f"@{username}" if username else chat_title
                except Exception as entity_err:
                    log.debug(
                        "Could not fetch chat entity by ID %s: %s",
                        event.chat_id,
                        entity_err,
                    )

            # For private chats, if chat_title is still empty, use sender_name as fallback
            if not chat_title and getattr(event, "chat_id", 0) > 0 and sender_name:
                chat_title = sender_name
        except Exception as enrich_err:
            # Never let enrichment errors break ingestion
            log.debug("Ingestion enrichment failed: %s", enrich_err)

        # Build minimal, JSON-serializable payload using safe defaults
        try:
            # Extract timestamp safely
            msg_date = getattr(m, "date", None)
            timestamp = msg_date.isoformat() if msg_date is not None else None

            payload = {
                "chat_id": getattr(event, "chat_id", None),
                "chat_title": chat_title,
                "msg_id": getattr(m, "id", None),
                "sender_id": getattr(m, "sender_id", None),
                "sender_name": sender_name,
                "mentioned": bool(getattr(m, "mentioned", False)),
                "text": getattr(m, "message", "") or "",
                "replies": int(getattr(getattr(m, "replies", None), "replies", 0) or 0),
                "reactions": _reaction_count(m),
                "timestamp": timestamp,
                "avatar_url": sender_avatar_url or chat_avatar_url,
                "chat_avatar_url": chat_avatar_url,
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
        except Exception as payload_err:
            log.exception("ingest_error: could not build payload: %s", payload_err)
            return

        # Filter out messages from the current user (don't track own messages)
        try:
            current_user_str = r.get("tgsentinel:user_info")
            if current_user_str:
                if isinstance(current_user_str, bytes):
                    current_user_str = current_user_str.decode()
                current_user = json.loads(str(current_user_str))
                current_user_id = current_user.get("user_id")
                if current_user_id and payload["sender_id"] == current_user_id:
                    log.debug("Skipping own message in chat %s", payload["chat_id"])
                    return
        except Exception as filter_err:
            log.debug("Could not filter own messages: %s", filter_err)

        # Private chat filtering based on monitored_users
        try:
            chat_id_val = payload["chat_id"]
            if isinstance(chat_id_val, int) and chat_id_val > 0:
                monitored_ids = {u.id for u in cfg.monitored_users}
                log.info(
                    "Private chat check: chat_id=%s, monitored_ids=%s",
                    chat_id_val,
                    monitored_ids,
                )
                if monitored_ids and chat_id_val not in monitored_ids:
                    log.info(
                        "Skipping private message from unmonitored user %s (not in %s)",
                        chat_id_val,
                        monitored_ids,
                    )
                    return
                log.info("Private message ALLOWED from monitored user %s", chat_id_val)
        except Exception as private_err:
            log.debug("Private chat filter failed: %s", private_err)

        # Finally, push to Redis stream
        try:
            r.xadd(
                stream, {"json": json.dumps(payload)}, maxlen=100000, approximate=True
            )
            log.info(
                "Message ingested: chat=%s, sender=%s (%s)",
                payload["chat_title"] or payload["chat_id"],
                payload["sender_name"] or payload["sender_id"],
                payload["msg_id"],
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
