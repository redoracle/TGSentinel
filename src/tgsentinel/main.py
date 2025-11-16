# Set umask to ensure files created are world-readable/writable (for container multi-access)
# MUST be at the very top before any imports or file operations
import os as _os_early

_os_early.umask(0o022)

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timedelta, timezone

from redis import Redis
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import SQLiteSession

from .client import make_client, start_ingestion
from .config import load_config
from .digest import send_digest
from .logging_setup import setup_logging
from .metrics import dump
from .store import init_db
from .worker import process_loop
from .api import start_api_server, set_sentinel_state, set_redis_client, set_config


AUTH_QUEUE_KEY = "tgsentinel:auth_queue"
AUTH_RESPONSE_HASH = "tgsentinel:auth_responses"


def _extract_banned_rights(rights):
    """Extract banned rights from a Telegram rights object.

    Args:
        rights: Telegram banned rights object or None

    Returns:
        Dict of boolean flags and until_date if rights exist, None otherwise
    """
    if not rights:
        return None

    return {
        "view_messages": bool(getattr(rights, "view_messages", False)),
        "send_messages": bool(getattr(rights, "send_messages", False)),
        "send_media": bool(getattr(rights, "send_media", False)),
        "send_stickers": bool(getattr(rights, "send_stickers", False)),
        "send_gifs": bool(getattr(rights, "send_gifs", False)),
        "send_games": bool(getattr(rights, "send_games", False)),
        "send_inline": bool(getattr(rights, "send_inline", False)),
        "embed_links": bool(getattr(rights, "embed_links", False)),
        "send_polls": bool(getattr(rights, "send_polls", False)),
        "change_info": bool(getattr(rights, "change_info", False)),
        "invite_users": bool(getattr(rights, "invite_users", False)),
        "pin_messages": bool(getattr(rights, "pin_messages", False)),
        "until_date": getattr(rights, "until_date", None),
    }


def _extract_admin_rights(rights):
    """Extract admin rights from a Telegram rights object.

    Args:
        rights: Telegram admin rights object or None

    Returns:
        Dict of boolean flags if rights exist, None otherwise
    """
    if not rights:
        return None

    return {
        "change_info": bool(getattr(rights, "change_info", False)),
        "post_messages": bool(getattr(rights, "post_messages", False)),
        "edit_messages": bool(getattr(rights, "edit_messages", False)),
        "delete_messages": bool(getattr(rights, "delete_messages", False)),
        "ban_users": bool(getattr(rights, "ban_users", False)),
        "invite_users": bool(getattr(rights, "invite_users", False)),
        "pin_messages": bool(getattr(rights, "pin_messages", False)),
        "add_admins": bool(getattr(rights, "add_admins", False)),
        "manage_call": bool(getattr(rights, "manage_call", False)),
        "manage_topics": bool(getattr(rights, "manage_topics", False)),
        "anonymous": bool(getattr(rights, "anonymous", False)),
        "other": bool(getattr(rights, "other", False)),
    }


async def _fetch_participant_info(
    client: TelegramClient, chat_id: int, user_id: int | None, log, r
):
    """Fetch participant information from Telegram."""
    info = {}

    try:
        # Get chat/channel information
        chat = await client.get_entity(chat_id)  # type: ignore[misc]

        # Determine chat type with proper attribute checks
        chat_type = "chat"
        if hasattr(chat, "broadcast") and getattr(chat, "broadcast", False):
            chat_type = "channel"
        elif hasattr(chat, "megagroup") and getattr(chat, "megagroup", False):
            chat_type = "supergroup"
        elif hasattr(chat, "creator"):
            chat_type = "group"

        info["chat"] = {
            "id": chat_id,
            "title": getattr(chat, "title", None)
            or getattr(chat, "first_name", "Unknown"),
            "type": chat_type,
            "username": getattr(chat, "username", None),
            "participants_count": getattr(chat, "participants_count", None),
            # Common chat flags if available
            "broadcast": bool(getattr(chat, "broadcast", False)),
            "megagroup": bool(getattr(chat, "megagroup", False)),
            "gigagroup": bool(getattr(chat, "gigagroup", False)),
            "forum": bool(getattr(chat, "forum", False)),
            "noforwards": bool(getattr(chat, "noforwards", False)),
            "verified": bool(getattr(chat, "verified", False)),
            "scam": bool(getattr(chat, "scam", False)),
            "fake": bool(getattr(chat, "fake", False)),
            "created_date": getattr(chat, "date", None),
            "access_hash": getattr(chat, "access_hash", None),
        }

        # Try to get chat avatar
        try:
            if hasattr(chat, "photo") and chat.photo:  # type: ignore[attr-defined]
                # Chat has a photo, cache it
                from .client import _cache_avatar

                avatar_url = await _cache_avatar(client, chat_id, chat.photo, r)  # type: ignore[arg-type]
                if avatar_url:
                    info["chat"]["avatar_url"] = avatar_url
        except Exception as e:
            log.debug("Failed to fetch chat avatar: %s", e)

        # Try to fetch extended chat information (description, permissions, pinned message)
        try:
            if info["chat"]["type"] in {"channel", "supergroup"}:
                from telethon.tl.functions.channels import GetFullChannelRequest  # type: ignore

                full = await client(
                    GetFullChannelRequest(channel=chat)  # type: ignore[arg-type]
                )
                if full:
                    ch_full = getattr(full, "full_chat", full)
                    info["chat"]["description"] = getattr(ch_full, "about", None)
                    info["chat"]["pinned_msg_id"] = getattr(
                        ch_full, "pinned_msg_id", None
                    )
                    info["chat"]["participants_count"] = info["chat"].get(
                        "participants_count"
                    ) or getattr(ch_full, "participants_count", None)
                    # Default banned rights (default permissions)
                    dbr = getattr(ch_full, "default_banned_rights", None)
                    extracted_rights = _extract_banned_rights(dbr)
                    if extracted_rights is not None:
                        info["chat"]["default_banned_rights"] = extracted_rights
                    # Exported invite
                    inv = getattr(ch_full, "exported_invite", None)
                    if inv is not None:
                        info["chat"]["invite_link"] = getattr(inv, "link", None)
            elif info["chat"]["type"] in {"group", "chat"}:
                from telethon.tl.functions.messages import GetFullChatRequest  # type: ignore

                full = await client(GetFullChatRequest(chat_id))
                if full:
                    ch_full = getattr(full, "full_chat", full)
                    info["chat"]["description"] = getattr(ch_full, "about", None)
                    info["chat"]["pinned_msg_id"] = getattr(
                        ch_full, "pinned_msg_id", None
                    )
                    info["chat"]["participants_count"] = info["chat"].get(
                        "participants_count"
                    ) or getattr(ch_full, "participants_count", None)
                    dbr = getattr(ch_full, "default_banned_rights", None)
                    extracted_rights = _extract_banned_rights(dbr)
                    if extracted_rights is not None:
                        info["chat"]["default_banned_rights"] = extracted_rights
                    inv = getattr(ch_full, "exported_invite", None)
                    if inv is not None:
                        info["chat"]["invite_link"] = getattr(inv, "link", None)
        except Exception as full_err:
            log.debug("Could not fetch full chat info: %s", full_err)

        # Populate admin/default rights if available on the base chat entity
        try:
            ar = getattr(chat, "admin_rights", None)
            extracted_rights = _extract_admin_rights(ar)
            if extracted_rights is not None:
                info["chat"]["admin_rights"] = extracted_rights
        except Exception:
            pass

        # Get user-specific information if user_id is provided
        if user_id:
            try:
                user = await client.get_entity(user_id)  # type: ignore[misc]
                name_parts = []
                first_name = getattr(user, "first_name", None)
                last_name = getattr(user, "last_name", None)

                if first_name:
                    name_parts.append(first_name)
                if last_name:
                    name_parts.append(last_name)

                info["user"] = {
                    "id": user_id,
                    "name": (
                        " ".join(name_parts)
                        if name_parts
                        else getattr(user, "username", f"User {user_id}")
                    ),
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": getattr(user, "username", None),
                    "phone": getattr(user, "phone", None),
                    "bot": getattr(user, "bot", False),
                    "verified": bool(getattr(user, "verified", False)),
                    "scam": bool(getattr(user, "scam", False)),
                    "fake": bool(getattr(user, "fake", False)),
                    "support": bool(getattr(user, "support", False)),
                    "premium": bool(getattr(user, "premium", False)),
                    "restricted": bool(getattr(user, "restricted", False)),
                    "deleted": bool(getattr(user, "deleted", False)),
                    "lang_code": getattr(user, "lang_code", None),
                    "access_hash": getattr(user, "access_hash", None),
                    # Identity & Relationship flags
                    "is_self": bool(getattr(user, "is_self", False)),
                    "contact": bool(getattr(user, "contact", False)),
                    "mutual_contact": bool(getattr(user, "mutual_contact", False)),
                    # Profile photo metadata
                    "photo_id": None,
                    "photo_dc_id": None,
                }

                # Try to get user avatar
                try:
                    if hasattr(user, "photo") and user.photo:  # type: ignore[attr-defined]
                        # Extract photo metadata
                        info["user"]["photo_id"] = getattr(user.photo, "photo_id", None)  # type: ignore[attr-defined]
                        info["user"]["photo_dc_id"] = getattr(user.photo, "dc_id", None)  # type: ignore[attr-defined]

                        # User has a photo, cache it
                        from .client import _cache_avatar

                        user_avatar_url = await _cache_avatar(client, user_id, user.photo, r)  # type: ignore[arg-type]
                        if user_avatar_url:
                            info["user"]["avatar_url"] = user_avatar_url
                except Exception as e:
                    log.debug("Failed to fetch user avatar: %s", e)

                # Extract status information
                try:
                    if hasattr(user, "status") and user.status:  # type: ignore[attr-defined]
                        status = user.status  # type: ignore[attr-defined]
                        status_type = type(status).__name__

                        if "Online" in status_type:
                            info["user"]["status_type"] = "online"
                            info["user"]["status_expires"] = getattr(
                                status, "expires", None
                            )
                        elif "Offline" in status_type:
                            info["user"]["status_type"] = "offline"
                            info["user"]["was_online"] = getattr(
                                status, "was_online", None
                            )
                        elif "Recently" in status_type:
                            info["user"]["status_type"] = "recently"
                        elif "LastWeek" in status_type:
                            info["user"]["status_type"] = "last_week"
                        elif "LastMonth" in status_type:
                            info["user"]["status_type"] = "last_month"
                        else:
                            info["user"]["status_type"] = "unknown"
                except Exception as e:
                    log.debug("Failed to extract status: %s", e)

                # Fetch full user info for additional details (about/bio, common chats, etc.)
                try:
                    from telethon.tl.functions.users import GetFullUserRequest  # type: ignore

                    full_user = await client(GetFullUserRequest(user))  # type: ignore[arg-type]
                    if full_user:
                        uf = getattr(full_user, "full_user", full_user)
                        info["user"]["about"] = getattr(uf, "about", None)
                        info["user"]["common_chats_count"] = getattr(
                            uf, "common_chats_count", None
                        )
                        # Some versions expose emojis/status
                        if hasattr(uf, "stories_max_id"):
                            info["user"]["stories_max_id"] = getattr(
                                uf, "stories_max_id", None
                            )
                        if hasattr(uf, "stories_unavailable"):
                            info["user"]["stories_unavailable"] = getattr(
                                uf, "stories_unavailable", None
                            )
                except Exception as fu_err:
                    log.debug("Could not fetch full user info: %s", fu_err)

                # Try to get participant-specific info (role, join date, etc.)
                # This only works for groups/supergroups/channels where we have permission
                try:
                    from telethon.tl.functions.channels import GetParticipantRequest  # type: ignore

                    participant_result = await client(
                        GetParticipantRequest(  # type: ignore[misc,arg-type]
                            channel=chat_id, participant=user_id  # type: ignore[arg-type]
                        )
                    )

                    if participant_result and hasattr(
                        participant_result, "participant"
                    ):
                        p = participant_result.participant  # type: ignore[attr-defined]
                        participant_data = {
                            "join_date": getattr(p, "date", None),
                            "inviter_id": getattr(p, "inviter_id", None),
                            "invited_by": getattr(p, "invited_by", None),
                        }

                        # Determine role
                        type_name = type(p).__name__
                        if "Creator" in type_name:
                            participant_data["role"] = "creator"
                            # Creators may have rank/custom_title
                            participant_data["rank"] = getattr(p, "rank", None)
                        elif "Admin" in type_name:
                            participant_data["role"] = "admin"
                            participant_data["rank"] = getattr(p, "rank", None)
                            participant_data["custom_title"] = getattr(
                                p, "custom_title", None
                            )
                            participant_data["promoted_by"] = getattr(
                                p, "promoted_by", None
                            )
                            if hasattr(p, "admin_rights"):
                                extracted_rights = _extract_admin_rights(p.admin_rights)
                                if extracted_rights is not None:
                                    participant_data["admin_rights"] = extracted_rights
                        elif "Banned" in type_name or "Kicked" in type_name:
                            participant_data["role"] = (
                                "banned" if "Banned" in type_name else "kicked"
                            )
                            participant_data["kicked_by"] = getattr(
                                p, "kicked_by", None
                            )
                            # Add banned rights/restrictions
                            if hasattr(p, "banned_rights"):
                                extracted_rights = _extract_banned_rights(
                                    p.banned_rights
                                )
                                if extracted_rights is not None:
                                    participant_data["banned_rights"] = extracted_rights
                        elif "Left" in type_name:
                            participant_data["role"] = "left"
                            participant_data["left"] = True
                        else:
                            participant_data["role"] = "member"

                        info["participant"] = participant_data

                except Exception as part_err:
                    log.debug("Could not fetch participant details: %s", part_err)

            except Exception as user_err:
                log.debug("Could not fetch user entity: %s", user_err)

    except Exception as e:
        log.error(
            "Failed to fetch participant info for chat %s, user %s: %s",
            chat_id,
            user_id,
            e,
        )
        info["error"] = str(e)

    return info


async def _run():
    setup_logging()
    log = logging.getLogger("tgsentinel")

    cfg = load_config()
    engine = init_db(cfg.db_uri)

    client: TelegramClient = make_client(cfg)
    session_file_path = Path(cfg.telegram_session or "/app/data/tgsentinel.session")

    # Initialize Redis early so helpers can use it
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)

    def _close_session_binding() -> None:
        sess = getattr(client, "session", None)
        closer = getattr(sess, "close", None)
        if callable(closer):
            try:
                closer()
                log.debug("Closed Telegram session binding")
            except Exception as close_exc:
                log.debug("Failed to close Telegram session binding: %s", close_exc)

    def _rebind_session_binding() -> None:
        """Reload session from disk by explicitly calling session.load().

        This forces Telethon to re-read the auth_key from SQLite without
        creating a new client instance.
        """
        try:
            if hasattr(client.session, "load"):
                client.session.load()  # type: ignore[attr-defined]
                log.debug("Reloaded Telegram session from %s", session_file_path)
            else:
                log.warning("Session does not support reload; attempting reconnection")
        except Exception as rebind_exc:
            log.error("Failed to reload Telegram session: %s", rebind_exc)

    async def _refresh_user_identity_cache(user_obj=None) -> None:
        me = user_obj
        if me is None:
            try:
                me = await client.get_me()  # type: ignore[misc]
            except Exception as info_exc:
                log.debug("Could not fetch user info for cache refresh: %s", info_exc)
                return

        if not me:
            log.warning("get_me() returned None; skipping user cache refresh")
            return

        avatar_url = "/static/images/logo.png"
        try:
            photos = await client.get_profile_photos("me", limit=1)  # type: ignore[misc]
            if photos:
                import base64
                import io

                # Download avatar to memory instead of disk
                avatar_bytes = io.BytesIO()
                try:
                    await client.download_profile_photo("me", file=avatar_bytes)  # type: ignore[misc]
                    avatar_bytes.seek(0)
                    avatar_b64 = base64.b64encode(avatar_bytes.read()).decode("utf-8")

                    # Store in Redis with user_id key
                    user_id = getattr(me, "id", None)
                    if user_id:
                        redis_key = f"tgsentinel:user_avatar:{user_id}"
                        r.set(redis_key, avatar_b64)  # No TTL
                        avatar_url = f"/api/avatar/user/{user_id}"
                        log.info(f"Stored user avatar in Redis: {redis_key}")
                except Exception as avatar_exc:
                    log.debug("Could not download user avatar: %s", avatar_exc)
        except Exception as photo_exc:
            log.debug("Could not refresh profile photos: %s", photo_exc)

        # Don't call _mark_authorized here as it would overwrite avatar with default
        # Build and store complete user info with actual avatar path
        try:
            ui = {
                "username": getattr(me, "username", None)
                or getattr(me, "first_name", "Unknown"),
                "first_name": getattr(me, "first_name", ""),
                "last_name": getattr(me, "last_name", ""),
                "phone": getattr(me, "phone", ""),
                "user_id": getattr(me, "id", None),
                "avatar": avatar_url,
            }
            r.set("tgsentinel:user_info", json.dumps(ui))  # No TTL
            log.info(
                "Stored user info in Redis after refresh: %s (avatar: %s)",
                ui.get("username"),
                avatar_url,
            )
        except Exception as cache_exc:
            log.error(
                "Could not store refreshed user info: %s", cache_exc, exc_info=True
            )

    # Ensure session file and directory have proper permissions for authentication
    try:
        session_dir = session_file_path.parent

        # Ensure data directory exists and is writable
        session_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(session_dir, 0o777)

        # If session file exists, make it writable
        if session_file_path.exists():
            os.chmod(session_file_path, 0o660)
            log.info("Set session file permissions to 0o660: %s", session_file_path)
    except Exception as exc:
        log.warning("Failed to set initial session permissions: %s", exc)

    handshake_gate = asyncio.Event()
    handshake_gate.set()
    client_lock = asyncio.Lock()
    dialogs_cache: tuple[datetime, list] | None = None
    dialogs_cache_lock = asyncio.Lock()
    dialogs_cache_ttl = timedelta(seconds=45)
    authorized = False
    auth_event = asyncio.Event()

    def _credential_fingerprint() -> dict[str, str] | None:
        try:
            api_id = str(cfg.api_id)
            api_hash = cfg.api_hash
            digest = hashlib.sha256(api_hash.encode("utf-8")).hexdigest()
            return {"api_id": api_id, "api_hash_sha256": digest}
        except Exception as exc:
            log.warning("Could not compute credential fingerprint: %s", exc)
            return None

    def _publish_and_check_credentials() -> None:
        fingerprint = _credential_fingerprint()
        if not fingerprint:
            return
        payload = {
            "fingerprint": fingerprint,
            "source": "sentinel",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            r.set("tgsentinel:credentials:sentinel", json.dumps(payload), ex=3600)
        except Exception as exc:
            log.debug("Could not store sentinel credential fingerprint: %s", exc)
            return
        try:
            raw = r.get("tgsentinel:credentials:ui")
            if not raw:
                log.warning(
                    "UI credential fingerprint not found in Redis; ensure UI container is running"
                )
                return
            if isinstance(raw, bytes):
                raw = raw.decode()
            ui_payload = json.loads(str(raw))
            ui_fp = ui_payload.get("fingerprint") or {}
            if ui_fp != fingerprint:
                log.error(
                    "TG credential mismatch detected. UI=%s sentinel=%s",
                    ui_fp,
                    fingerprint,
                )
                try:
                    r.setex(
                        "tgsentinel:worker_status",
                        60,
                        json.dumps(
                            {
                                "authorized": False,
                                "status": "credential_mismatch",
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                except Exception:
                    pass
                raise SystemExit("Credential mismatch between UI and sentinel")
        except SystemExit:
            raise
        except Exception as exc:
            log.warning("Could not verify credential parity: %s", exc)

    _publish_and_check_credentials()

    # Defer ingestion until after authorization

    def _mark_authorized(user=None):
        nonlocal authorized
        authorized = True
        auth_event.set()
        log.info(
            "[AUTH] Marking as authorized, user=%s",
            getattr(user, "id", None) if user else "unknown",
        )

        # Update API state
        from .api import set_sentinel_state

        set_sentinel_state("authorized", True)
        set_sentinel_state("connected", True)

        try:
            # Store with longer TTL (1 hour) so it persists across checks
            r.setex(
                "tgsentinel:worker_status",
                3600,
                json.dumps(
                    {
                        "authorized": True,
                        "status": "authorized",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
        except Exception as exc:
            log.warning("[AUTH] Failed to update worker status: %s", exc)
        if not user:
            return
        try:
            ui_info = {
                "username": getattr(user, "username", None)
                or getattr(user, "first_name", "Analyst"),
                "first_name": getattr(user, "first_name", ""),
                "last_name": getattr(user, "last_name", ""),
                "phone": getattr(user, "phone", None),
                "user_id": getattr(user, "id", None),
                "avatar": "/static/images/logo.png",
            }
            r.set("tgsentinel:user_info", json.dumps(ui_info))  # No TTL
            set_sentinel_state("user_info", ui_info)
            log.debug("[AUTH] User info stored in Redis")
        except Exception as exc:
            log.warning("[AUTH] Failed to store user info: %s", exc)

    async def _ensure_client_connected():
        if not client.is_connected():  # type: ignore[misc]
            await client.connect()  # type: ignore[misc]

    def _set_auth_response(request_id: str, payload: Dict[str, Any]) -> None:
        try:
            r.hset(AUTH_RESPONSE_HASH, request_id, json.dumps(payload))
            r.expire(AUTH_RESPONSE_HASH, 120)
        except Exception as exc:
            log.warning("Failed to store auth response: %s", exc)

    async def _get_cached_dialogs(force_refresh: bool = False):
        """Fetch dialogs once and reuse them to avoid duplicate Telethon calls."""

        nonlocal dialogs_cache
        async with dialogs_cache_lock:
            now = datetime.now(timezone.utc)
            if (
                not force_refresh
                and dialogs_cache
                and now - dialogs_cache[0] < dialogs_cache_ttl
            ):
                cached_len = len(dialogs_cache[1]) if dialogs_cache[1] else 0
                log.debug("Using cached dialogs (%d entries)", cached_len)
                return dialogs_cache[1]

            log.debug("Fetching dialogs from Telegram (cache expired)")
            try:
                # Ensure client is connected before making API call
                await _ensure_client_connected()
                dialogs = await client.get_dialogs()  # type: ignore[misc]
                dialogs_cache = (now, dialogs)
                log.info("Fetched %d dialogs from Telegram", len(dialogs))
                return dialogs
            except Exception as e:
                log.error(
                    "Failed to fetch dialogs: %s. Clearing cache and retrying once.",
                    e,
                    exc_info=True,
                )
                # Clear cache and retry once
                dialogs_cache = None
                if not force_refresh:
                    # Retry with force refresh
                    return await _get_cached_dialogs(force_refresh=True)
                raise

    async def channels_users_cache_refresher():
        """Background task to refresh channels and users cache every 10 minutes.

        This prevents UI timeouts by maintaining fresh Redis cache that can be
        served instantly. Uses differential updates to add new entries and remove
        channels/groups we're no longer part of.

        Also listens for session_updated events to trigger immediate refresh
        when a new user logs in.
        """
        log.info(
            "[CACHE-REFRESHER] Starting channels/users cache refresher (10min interval)"
        )

        CACHE_INTERVAL = 600  # 10 minutes
        REDIS_CHANNELS_KEY = "tgsentinel:cached_channels"
        REDIS_USERS_KEY = "tgsentinel:cached_users"
        REDIS_CACHE_READY_KEY = "tgsentinel:cache_ready"
        CACHE_TTL = 900  # 15 minutes (longer than refresh interval for safety)

        # Subscribe to session update events for immediate refresh on user switch
        pubsub = r.pubsub()
        await asyncio.to_thread(pubsub.subscribe, "tgsentinel:session_updated")
        log.info("[CACHE-REFRESHER] Subscribed to session_updated events")

        async def perform_cache_refresh():
            """Perform the actual cache refresh operation."""
            try:
                from telethon.tl.types import Channel, Chat as TgChat, User

                # Fetch fresh dialogs (fast - ~20 seconds for 365 dialogs)
                dialogs = await _get_cached_dialogs(force_refresh=True)
                # Note: _get_cached_dialogs() already logs "Fetched X dialogs"

                # Process channels/groups metadata (fast - in-memory only)
                channels_list = []
                users_list = []
                entities_with_photos = (
                    []
                )  # Collect entities for parallel avatar caching

                for dialog in dialogs:
                    entity = dialog.entity

                    # Process channels and groups
                    if isinstance(entity, (Channel, TgChat)):
                        chat_type = "group"
                        if isinstance(entity, Channel):
                            if getattr(entity, "broadcast", False):
                                chat_type = "channel"
                            elif getattr(entity, "megagroup", False):
                                chat_type = "supergroup"
                            else:
                                chat_type = "group"

                        name = getattr(entity, "title", None) or getattr(
                            entity, "first_name", "Unknown"
                        )
                        entity_id = getattr(entity, "id", 0)

                        # Collect entity for parallel avatar caching later
                        photo = getattr(entity, "photo", None)
                        if photo and entity_id:
                            entities_with_photos.append((entity_id, photo, name))

                        channels_list.append(
                            {
                                "id": entity_id,
                                "name": name,
                                "type": chat_type,
                                "username": getattr(entity, "username", None),
                            }
                        )

                    # Process users
                    elif isinstance(entity, User):
                        name_parts = []
                        if hasattr(entity, "first_name") and entity.first_name:
                            name_parts.append(entity.first_name)
                        if hasattr(entity, "last_name") and entity.last_name:
                            name_parts.append(entity.last_name)

                        display_name = (
                            " ".join(name_parts)
                            if name_parts
                            else (
                                entity.username
                                if hasattr(entity, "username") and entity.username
                                else f"User {entity.id}"
                            )
                        )

                        # Collect user for parallel avatar caching later
                        user_id = getattr(entity, "id", 0)
                        photo = getattr(entity, "photo", None)
                        if photo and user_id:
                            entities_with_photos.append((user_id, photo, display_name))

                        users_list.append(
                            {
                                "id": entity.id,
                                "name": display_name,
                                "username": getattr(entity, "username", None),
                                "phone": getattr(entity, "phone", None),
                                "bot": getattr(entity, "bot", False),
                            }
                        )

                # Store metadata in Redis with TTL (fast - milliseconds)
                r.setex(REDIS_CHANNELS_KEY, CACHE_TTL, json.dumps(channels_list))
                r.setex(REDIS_USERS_KEY, CACHE_TTL, json.dumps(users_list))

                # Mark cache as ready
                r.setex(REDIS_CACHE_READY_KEY, CACHE_TTL, "1")

                # Publish notification that cache is ready
                r.publish(
                    "tgsentinel:cache_ready_event",
                    json.dumps(
                        {
                            "event": "cache_updated",
                            "channels_count": len(channels_list),
                            "users_count": len(users_list),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )

                log.info(
                    f"[CACHE-REFRESHER] ✓ Updated cache: {len(channels_list)} channels, "
                    f"{len(users_list)} users"
                )

                # PUBLISH LOGIN COMPLETION NOW (before avatar caching)
                # This allows UI to become responsive immediately
                try:
                    login_progress_exists = r.exists("tgsentinel:login_progress")
                    if login_progress_exists:
                        r.set(
                            "tgsentinel:login_progress",
                            json.dumps(
                                {
                                    "stage": "completed",
                                    "percent": 100,
                                    "message": f"Login complete! {len(channels_list)} channels, {len(users_list)} users available. Avatars loading in background...",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )
                        log.info(
                            "[CACHE-REFRESHER] Published login completion (no TTL)"
                        )
                except Exception as progress_exc:
                    log.debug(
                        "[CACHE-REFRESHER] Failed to publish progress: %s",
                        progress_exc,
                    )

                # NOW cache avatars in parallel batches (slow - but non-blocking for login)
                if entities_with_photos:
                    log.info(
                        f"[CACHE-REFRESHER] Starting parallel avatar caching for {len(entities_with_photos)} entities..."
                    )
                    await _cache_avatars_parallel(entities_with_photos, client, r)
                    log.info(
                        f"[CACHE-REFRESHER] ✓ Completed avatar caching (next refresh in {CACHE_INTERVAL}s)"
                    )

            except Exception as refresh_err:
                log.error(
                    "[CACHE-REFRESHER] Failed to refresh cache: %s",
                    refresh_err,
                    exc_info=True,
                )

        while True:
            try:
                await handshake_gate.wait()

                # Check if authorized before processing
                if not authorized:
                    log.debug("[CACHE-REFRESHER] Not authorized, waiting...")
                    # Wait for auth_event instead of sleeping blindly
                    try:
                        await asyncio.wait_for(auth_event.wait(), timeout=30)
                        log.info(
                            "[CACHE-REFRESHER] Authorization detected, proceeding to cache refresh"
                        )
                        # Don't continue here - fall through to refresh immediately
                    except asyncio.TimeoutError:
                        continue  # Check again

                # Check for session update event (non-blocking with short timeout)
                session_updated = False
                try:
                    message = await asyncio.to_thread(pubsub.get_message, timeout=0.1)
                    if message and message.get("type") == "message":
                        try:
                            msg_data = message["data"]
                            if isinstance(msg_data, bytes):
                                msg_data = msg_data.decode("utf-8")
                            data = json.loads(msg_data)
                            event_type = data.get("event")
                            log.debug(
                                "[CACHE-REFRESHER] Received event: %s", event_type
                            )
                            if event_type == "session_authorized":
                                session_updated = True
                                log.info(
                                    "[CACHE-REFRESHER] Session authorization detected, triggering immediate refresh"
                                )
                        except Exception as parse_exc:
                            log.warning(
                                "[CACHE-REFRESHER] Failed to parse session update message: %s",
                                parse_exc,
                            )
                except Exception as msg_exc:
                    log.debug(
                        "[CACHE-REFRESHER] No message in initial check: %s", msg_exc
                    )

                # Perform refresh if authorized
                log.info("[CACHE-REFRESHER] Fetching dialogs for cache refresh...")

                await perform_cache_refresh()

                # Wait for next refresh interval (but check for session updates periodically)
                if not session_updated:
                    # Normal periodic refresh - wait full interval
                    log.debug(
                        "[CACHE-REFRESHER] Entering wait loop for %d seconds",
                        CACHE_INTERVAL,
                    )
                    for i in range(CACHE_INTERVAL):
                        # Check for session updates every second during wait
                        try:
                            message = await asyncio.to_thread(
                                pubsub.get_message, timeout=1.0
                            )
                            if message and message.get("type") == "message":
                                try:
                                    msg_data = message["data"]
                                    if isinstance(msg_data, bytes):
                                        msg_data = msg_data.decode("utf-8")
                                    data = json.loads(msg_data)
                                    event_type = data.get("event")
                                    log.info(
                                        "[CACHE-REFRESHER] Received event during wait (iteration %d): %s",
                                        i,
                                        event_type,
                                    )
                                    if event_type == "session_authorized":
                                        log.info(
                                            "[CACHE-REFRESHER] Session authorization during wait period, breaking early"
                                        )
                                        break  # Break out of wait loop to refresh immediately
                                except Exception as parse_exc:
                                    log.warning(
                                        "[CACHE-REFRESHER] Failed to parse message during wait: %s",
                                        parse_exc,
                                    )
                        except Exception:
                            pass  # Continue waiting on any error
                else:
                    # Session was just updated, wait a bit before next check
                    log.debug("[CACHE-REFRESHER] Session just updated, short sleep")
                    await asyncio.sleep(5)

            except Exception as e:
                log.error(
                    "[CACHE-REFRESHER] Cache refresher error: %s", e, exc_info=True
                )
                await asyncio.sleep(60)  # Wait 1 minute on error before retrying

    async def _cache_avatars_parallel(
        entities_with_photos: list, client: TelegramClient, r: Redis
    ):
        """Cache avatars for entities in parallel batches to respect rate limits.

        Args:
            entities_with_photos: List of tuples (entity_id, photo, display_name)
            client: Telethon client
            r: Redis client
        """
        from .client import _cache_avatar

        BATCH_SIZE = 10  # Download 10 avatars concurrently
        BATCH_DELAY = 1  # Wait 1 second between batches to respect rate limits

        avatar_tasks = []
        total_cached = 0
        total_skipped = 0
        total_errors = 0

        for entity_id, photo, display_name in entities_with_photos:
            # Create task for avatar caching
            task = _cache_avatar(client, entity_id, photo, r)
            avatar_tasks.append((task, entity_id, display_name))

            # Process batch when full
            if len(avatar_tasks) >= BATCH_SIZE:
                tasks_only = [t[0] for t in avatar_tasks]
                results = await asyncio.gather(*tasks_only, return_exceptions=True)

                # Count results
                for idx, result in enumerate(results):
                    entity_id = avatar_tasks[idx][1]
                    if isinstance(result, Exception):
                        total_errors += 1
                        log.debug(
                            f"[CACHE-REFRESHER] Avatar error for {entity_id}: {result}"
                        )
                    elif result:  # Avatar URL returned = cached
                        total_cached += 1
                    else:  # None returned = already cached or no photo
                        total_skipped += 1

                if total_cached > 0 or total_errors > 0:
                    log.info(
                        f"[CACHE-REFRESHER] Cached batch: {total_cached} new, "
                        f"{total_skipped} skipped, {total_errors} errors"
                    )

                avatar_tasks = []
                await asyncio.sleep(BATCH_DELAY)

        # Process remaining tasks
        if avatar_tasks:
            tasks_only = [t[0] for t in avatar_tasks]
            results = await asyncio.gather(*tasks_only, return_exceptions=True)
            for idx, result in enumerate(results):
                entity_id = avatar_tasks[idx][1]
                if isinstance(result, Exception):
                    total_errors += 1
                    log.debug(
                        f"[CACHE-REFRESHER] Avatar error for {entity_id}: {result}"
                    )
                elif result:
                    total_cached += 1
                else:
                    total_skipped += 1

        log.info(
            f"[CACHE-REFRESHER] ✓ Avatar caching complete: {total_cached} cached, "
            f"{total_skipped} skipped, {total_errors} errors"
        )

    def _extract_retry_after_seconds(exc: Exception) -> int | None:
        for attr in ("seconds", "wait_seconds", "retry_after", "duration"):
            val = getattr(exc, attr, None)
            if val is None:
                continue
            try:
                return max(int(val), 0)
            except Exception:
                continue
        msg = str(exc).lower()
        # Heuristic: "wait of N seconds"
        import re

        m = re.search(r"wait of\s+(\d+)\s+seconds", msg)
        if m:
            try:
                return max(int(m.group(1)), 0)
            except Exception:
                return None
        return None

    def _normalize_auth_error(exc: Exception) -> Dict[str, Any]:
        msg = str(exc)
        retry_after = _extract_retry_after_seconds(exc)
        reason = "server_error"
        # Flood or rate-limit
        if retry_after and retry_after > 0:
            reason = "flood_wait"
        # Resend exhausted or unavailable
        low = msg.lower()
        if "resend" in low or "available options" in low:
            reason = "resend_unavailable"
            # Provide a small backoff even if server didn't supply one
            if not retry_after:
                retry_after = 60
        payload: Dict[str, Any] = {
            "status": "error",
            "message": msg,
            "reason": reason,
        }
        if retry_after is not None:
            payload["retry_after"] = retry_after
        return payload

    def _check_rate_limit(action: str, phone: str | None = None) -> tuple[bool, int]:
        """Check if action is rate limited. Returns (is_allowed, wait_seconds)."""
        try:
            # Check global rate limit key
            rate_limit_key = f"tgsentinel:rate_limit:{action}"
            if phone:
                rate_limit_key += f":{phone}"

            ttl = r.ttl(rate_limit_key)  # type: ignore[misc]
            ttl_int = int(ttl) if ttl else 0  # type: ignore[arg-type]
            if ttl_int > 0:
                log.warning(
                    "[AUTH] Rate limit active for %s: %d seconds remaining",
                    action,
                    ttl_int,
                )
                return False, ttl_int
            return True, 0
        except Exception as exc:
            log.debug("[AUTH] Rate limit check failed: %s", exc)
            return True, 0  # Allow on check failure

    def _set_rate_limit(
        action: str, wait_seconds: int, phone: str | None = None
    ) -> None:
        """Set rate limit for an action."""
        try:
            rate_limit_key = f"tgsentinel:rate_limit:{action}"
            if phone:
                rate_limit_key += f":{phone}"

            r.setex(rate_limit_key, wait_seconds, "1")
            log.warning(
                "[AUTH] Rate limit set for %s: %d seconds", action, wait_seconds
            )

            # Also store in worker status for UI visibility
            try:
                r.setex(
                    "tgsentinel:worker_status",
                    min(wait_seconds, 3600),
                    json.dumps(
                        {
                            "authorized": False,
                            "status": "rate_limited",
                            "rate_limit_action": action,
                            "rate_limit_wait": wait_seconds,
                            "rate_limit_until": (
                                datetime.now(timezone.utc)
                                + timedelta(seconds=wait_seconds)
                            ).isoformat(),
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )
            except Exception:
                pass
        except Exception as exc:
            log.error("[AUTH] Failed to set rate limit: %s", exc)

    async def _handle_auth_request(data: Dict[str, Any]) -> None:
        nonlocal authorized
        action = data.get("action")
        request_id = data.get("request_id")
        log.info(
            "[AUTH] Processing auth request: action=%s, request_id=%s, authorized=%s",
            action,
            request_id,
            authorized,
        )
        if not request_id:
            log.warning("[AUTH] Missing request_id in auth request data")
            return
        if authorized and action != "status":
            log.warning("[AUTH] Rejecting %s request - already authorized", action)
            _set_auth_response(
                request_id,
                {"status": "error", "message": "Already authorized"},
            )
            return

        try:
            if action == "start":
                phone = str(data.get("phone", "")).strip()
                if not phone:
                    raise ValueError("Phone is required")

                # Check rate limit before attempting
                is_allowed, wait_seconds = _check_rate_limit("send_code", phone)
                if not is_allowed:
                    log.warning(
                        "[AUTH] Start: rate limited, %d seconds remaining", wait_seconds
                    )
                    _set_auth_response(
                        request_id,
                        {
                            "status": "error",
                            "message": f"Rate limited. Please wait {wait_seconds} seconds before trying again.",
                            "reason": "flood_wait",
                            "retry_after": wait_seconds,
                        },
                    )
                    return

                log.info("[AUTH] Start: sending code to phone=%s", phone)
                async with client_lock:
                    await _ensure_client_connected()
                    log.debug(
                        "[AUTH] Start: client connected, calling send_code_request"
                    )
                    sent = await client.send_code_request(phone)  # type: ignore[misc]
                    log.info(
                        "[AUTH] Start: code sent successfully, phone_code_hash=%s",
                        getattr(sent, "phone_code_hash", None),
                    )
                response = {
                    "status": "ok",
                    "message": "Code sent",
                    "phone_code_hash": getattr(sent, "phone_code_hash", None),
                    "timeout": getattr(sent, "timeout", None),
                    "type": getattr(
                        getattr(sent, "type", None), "__class__", type("", (), {})
                    ).__name__,
                }
                _set_auth_response(request_id, response)
                log.debug("[AUTH] Start: response sent to UI via Redis")

            elif action == "resend":
                phone = str(data.get("phone", "")).strip()
                if not phone:
                    raise ValueError("Phone is required")

                # Check rate limit before resending
                is_allowed, wait_seconds = _check_rate_limit("resend_code", phone)
                if not is_allowed:
                    log.warning(
                        "[AUTH] Resend: rate limited, %d seconds remaining",
                        wait_seconds,
                    )
                    _set_auth_response(
                        request_id,
                        {
                            "status": "error",
                            "message": f"Rate limited. Please wait {wait_seconds} seconds before trying again.",
                            "reason": "flood_wait",
                            "retry_after": wait_seconds,
                        },
                    )
                    return
                async with client_lock:
                    await _ensure_client_connected()
                    sent = await client.send_code_request(  # type: ignore[misc]
                        phone, force_sms=True
                    )
                response = {
                    "status": "ok",
                    "message": "Code resent",
                    "phone_code_hash": getattr(sent, "phone_code_hash", None),
                    "timeout": getattr(sent, "timeout", None),
                }
                _set_auth_response(request_id, response)

            elif action == "verify":
                phone = str(data.get("phone", "")).strip()
                code = str(data.get("code", "")).strip()
                phone_code_hash = data.get("phone_code_hash")
                password = data.get("password")
                log.info(
                    "[AUTH] Verify: phone=%s, code=%s, has_password=%s",
                    phone,
                    code[:2] + "***" if code else None,
                    bool(password),
                )
                if not phone or not code:
                    raise ValueError("Phone and code are required")
                async with client_lock:
                    # Ensure session file is writable before sign_in (Telethon needs to write during auth)
                    try:
                        session_path = Path(
                            cfg.telegram_session or "/app/data/tgsentinel.session"
                        )
                        if session_path.exists():
                            os.chmod(session_path, 0o666)
                            log.debug(
                                "[AUTH] Verify: session file permissions set to 0o666"
                            )
                    except Exception as perm_exc:
                        log.warning(
                            "[AUTH] Verify: failed to set session permissions: %s",
                            perm_exc,
                        )

                    try:
                        await _ensure_client_connected()
                        log.debug("[AUTH] Verify: calling client.sign_in with code")
                        await client.sign_in(  # type: ignore[misc]
                            phone=phone,
                            code=code,
                            phone_code_hash=str(phone_code_hash or ""),
                        )
                        log.info("[AUTH] Verify: sign_in succeeded (no 2FA)")
                    except SessionPasswordNeededError:
                        log.info("[AUTH] Verify: 2FA password required")
                        if not password:
                            raise ValueError("Password required for 2FA")

                        # Ensure permissions again before 2FA sign_in
                        try:
                            session_path = Path(
                                cfg.telegram_session or "/app/data/tgsentinel.session"
                            )
                            if session_path.exists():
                                os.chmod(session_path, 0o666)
                        except Exception:
                            pass

                        log.debug("[AUTH] Verify: calling client.sign_in with password")
                        await client.sign_in(password=password)  # type: ignore[misc]
                        log.info("[AUTH] Verify: 2FA sign_in succeeded")

                    # Fix permissions after successful sign_in
                    try:
                        session_path = Path(
                            cfg.telegram_session or "/app/data/tgsentinel.session"
                        )
                        if session_path.exists():
                            os.chmod(session_path, 0o666)
                    except Exception:
                        pass

                log.debug("[AUTH] Verify: fetching user info with get_me()")
                me = await client.get_me()  # type: ignore[misc]
                if me:
                    log.info(
                        "[AUTH] Verify: authentication successful for user_id=%s, username=%s",
                        getattr(me, "id", None),
                        getattr(me, "username", None),
                    )

                    # Explicitly save session to ensure persistence
                    try:
                        if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                            client.session.save()  # type: ignore[misc]
                            log.info("[AUTH] Verify: session saved to disk")

                        # Verify session file exists and is readable
                        session_path = Path(
                            cfg.telegram_session or "/app/data/tgsentinel.session"
                        )
                        if session_path.exists():
                            size = session_path.stat().st_size
                            log.info(
                                "[AUTH] Verify: session file confirmed, size=%d bytes",
                                size,
                            )
                        else:
                            log.warning(
                                "[AUTH] Verify: session file not found after save!"
                            )
                    except Exception as save_exc:
                        log.error(
                            "[AUTH] Verify: failed to save session: %s",
                            save_exc,
                            exc_info=True,
                        )

                    # Refresh user identity cache to store avatar and full user info
                    try:
                        await _refresh_user_identity_cache(me)
                        log.debug("[AUTH] Verify: cached user identity with avatar")
                    except Exception as cache_exc:
                        log.warning(
                            "[AUTH] Verify: cache refresh failed: %s", cache_exc
                        )
                        # Fallback to basic auth marking (stores minimal user info)
                        _mark_authorized(me)
                    else:
                        # Cache refresh succeeded – ensure worker advertises authorized
                        # status without overwriting cached avatar/user_info.
                        _mark_authorized()

                    _set_auth_response(
                        request_id,
                        {
                            "status": "ok",
                            "message": "Authenticated",
                        },
                    )
                    log.debug("[AUTH] Verify: success response sent to UI")
                else:
                    log.error("[AUTH] Verify: get_me() returned None after sign_in")
                    raise ValueError("Verification failed; account not authorized")

            else:
                _set_auth_response(
                    request_id,
                    {"status": "error", "message": f"Unknown action: {action}"},
                )
        except Exception as exc:
            log.error(
                "[AUTH] Request failed: action=%s, error=%s", action, exc, exc_info=True
            )
            error_response = _normalize_auth_error(exc)

            # Store rate limit if present
            retry_after = error_response.get("retry_after")
            if retry_after and retry_after > 0:
                phone = data.get("phone")
                if action == "start":
                    _set_rate_limit("send_code", retry_after, phone)
                elif action == "resend":
                    _set_rate_limit("resend_code", retry_after, phone)
                elif action == "verify":
                    _set_rate_limit("sign_in", retry_after, phone)

                log.error(
                    "[AUTH] Rate limit detected: action=%s, wait=%d seconds (~%.1f hours)",
                    action,
                    retry_after,
                    retry_after / 3600.0,
                )

            log.debug("[AUTH] Sending error response: %s", error_response)
            _set_auth_response(request_id, error_response)

    async def auth_queue_worker():
        log.info(
            "[AUTH-WORKER] Starting auth queue worker (listening on %s)", AUTH_QUEUE_KEY
        )
        loop = asyncio.get_running_loop()
        while not shutdown_event.is_set():
            try:
                result = await loop.run_in_executor(
                    None, lambda: r.blpop([AUTH_QUEUE_KEY], timeout=5)
                )
            except Exception as exc:
                log.debug("[AUTH-WORKER] Queue poll failed: %s", exc)
                await asyncio.sleep(1)
                continue

            if not result:
                continue

            _, payload = result  # type: ignore[misc]
            log.debug(
                "[AUTH-WORKER] Received auth request from queue: %s bytes",
                len(payload) if payload else 0,
            )
            try:
                data = json.loads(
                    payload.decode() if isinstance(payload, bytes) else payload
                )
                log.debug(
                    "[AUTH-WORKER] Parsed request: action=%s, request_id=%s",
                    data.get("action"),
                    data.get("request_id"),
                )
            except Exception as exc:
                log.warning("[AUTH-WORKER] Invalid auth queue payload: %s", exc)
                continue

            await _handle_auth_request(data)
        log.info("[AUTH-WORKER] Auth queue worker stopped")

    async def relogin_coordinator():
        """Pause Telegram client while UI replaces the session file."""
        nonlocal client  # Must declare at function start before any nested usage
        key = "tgsentinel:relogin"
        active_request_id: str | None = None
        deadline: datetime | None = None

        while True:
            await asyncio.sleep(0.5)
            try:
                raw = r.get(key)
            except Exception as redis_err:
                log.debug("Could not read re-login marker: %s", redis_err)
                continue

            state = None
            if raw:
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    state = json.loads(str(raw))
                except Exception as parse_err:
                    log.debug("Invalid re-login marker payload: %s", parse_err)
                    state = None

            if not state:
                if (
                    active_request_id
                    and deadline
                    and datetime.now(timezone.utc) > deadline
                ):
                    log.warning(
                        "Re-login handshake %s timed out (marker disappeared); resuming worker",
                        active_request_id,
                    )
                    try:
                        await client.connect()
                    except Exception as exc:
                        log.debug("Reconnect after timeout failed: %s", exc)
                    handshake_gate.set()
                    active_request_id = None
                    deadline = None
                continue

            status = state.get("status")
            request_id = state.get("request_id")

            if status == "request" and request_id:
                if active_request_id and request_id != active_request_id:
                    # Another request arrived while one is active - wait for completion
                    continue
                if active_request_id == request_id and not handshake_gate.is_set():
                    # Already processing this request
                    continue

                active_request_id = request_id
                deadline = datetime.now(timezone.utc) + timedelta(seconds=180)
                handshake_gate.clear()
                log.info(
                    "Re-login handshake %s requested; disconnecting Telegram client",
                    request_id,
                )
                try:
                    # Explicitly save session before disconnect
                    try:
                        if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                            client.session.save()  # type: ignore[misc]
                    except Exception:
                        pass

                    await client.disconnect()  # type: ignore[misc]
                    _close_session_binding()
                except Exception as exc:
                    log.debug("Client disconnect during handshake failed: %s", exc)
                try:
                    r.set(
                        key,
                        json.dumps(
                            {
                                "status": "worker_detached",
                                "request_id": request_id,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                        ex=180,
                    )
                except Exception:
                    pass
                try:
                    r.setex(
                        "tgsentinel:worker_status",
                        30,
                        json.dumps(
                            {
                                "authorized": False,
                                "status": "relogin_paused",
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                except Exception:
                    pass
                continue

            if (
                active_request_id
                and request_id == active_request_id
                and status in {"promotion_complete", "cancelled"}
            ):
                log.info(
                    "Re-login handshake %s acknowledged with status %s; reconnecting",
                    request_id,
                    status,
                )
                try:
                    log.info("[RELOGIN] Step 1: Recreating client with new session")
                    # Telethon caches session state in memory - we must create a new client
                    # instance to pick up the newly written session file
                    old_client = client
                    try:
                        # Remove all event handlers before disconnect to prevent duplicate processing
                        try:
                            handlers = old_client.list_event_handlers()
                            for callback, event in handlers:
                                old_client.remove_event_handler(callback, event)
                            log.debug(
                                "[RELOGIN] Removed %d event handler(s) from old client",
                                len(handlers),
                            )
                        except Exception as handler_exc:
                            log.debug("Failed to remove handlers: %s", handler_exc)

                        # Telethon's disconnect() is synchronous and returns None
                        old_client.disconnect()  # type: ignore[misc]
                        _close_session_binding()
                        # Ensure session file locks are released
                        await asyncio.sleep(0.5)
                        log.debug(
                            "[RELOGIN] Old client disconnected and session closed"
                        )
                    except Exception as disc_exc:
                        log.debug("Disconnect during relogin: %s", disc_exc)

                    # Explicitly delete old client reference to release resources
                    del old_client
                    await asyncio.sleep(0.3)  # Additional time for SQLite lock release

                    # Create fresh client with updated session file
                    log.debug("[RELOGIN] Creating new client instance")
                    client = make_client(cfg)

                    # Try connection with retries
                    log.info("[RELOGIN] Step 2: Connecting to Telegram")
                    connection_success = False
                    for attempt in range(3):
                        try:
                            await asyncio.wait_for(client.connect(), timeout=30)
                            log.info(
                                "[RELOGIN] Step 2a: Connection established (attempt %d)",
                                attempt + 1,
                            )
                            connection_success = True
                            break
                        except asyncio.TimeoutError:
                            if attempt < 2:
                                log.warning(
                                    "[RELOGIN] Connection timeout (attempt %d/3), retrying...",
                                    attempt + 1,
                                )
                                await asyncio.sleep(2)
                            else:
                                log.error(
                                    "[RELOGIN] Connection failed after 3 attempts"
                                )
                        except Exception as conn_exc:
                            log.warning(
                                "[RELOGIN] Connection error (attempt %d/3): %s",
                                attempt + 1,
                                conn_exc,
                            )
                            if attempt < 2:
                                await asyncio.sleep(2)

                    if not connection_success:
                        log.error(
                            "[RELOGIN] Failed to connect after retries; handshake failed"
                        )
                        raise RuntimeError("Connection failed after retries")

                    log.info("[RELOGIN] Step 3: Checking authorization")
                    auth_check_success = False
                    try:
                        authorized = await asyncio.wait_for(
                            client.is_user_authorized(), timeout=20
                        )
                        auth_check_success = True
                        log.info(
                            "[RELOGIN] Step 3a: Authorization check result: %s",
                            authorized,
                        )
                    except asyncio.TimeoutError:
                        log.warning(
                            "[RELOGIN] Authorization check timed out, assuming not authorized"
                        )
                        authorized = False
                    except Exception as auth_check_exc:
                        log.warning(
                            "[RELOGIN] Authorization check failed: %s", auth_check_exc
                        )
                        authorized = False

                    if authorized:
                        log.info("[RELOGIN] Step 4: Getting user info")
                        try:
                            me = await asyncio.wait_for(
                                client.get_me(), timeout=20  # type: ignore[misc]
                            )
                            log.info(
                                "[RELOGIN] Step 4a: Got user: %s",
                                getattr(me, "username", None) if me else None,
                            )
                        except asyncio.TimeoutError:
                            log.warning("[RELOGIN] get_me() timed out")
                            me = None
                        except Exception as getme_exc:
                            log.warning("[RELOGIN] get_me() failed: %s", getme_exc)
                            me = None

                        log.info("[RELOGIN] Step 5: Refreshing identity cache")
                        try:
                            await _refresh_user_identity_cache(me)
                            log.info("[RELOGIN] Step 5a: Cache refresh completed")
                        except Exception as cache_exc:
                            log.warning("[RELOGIN] Cache refresh failed: %s", cache_exc)

                        # Mark authorized and trigger auth_event to unblock startup wait loop
                        # Don't pass user object to avoid overwriting avatar with default
                        log.info("[RELOGIN] Step 6: Marking as authorized")
                        _mark_authorized()
                        log.info(
                            "Re-login handshake %s completed; client authorized and ready",
                            request_id,
                        )
                    else:
                        log.warning(
                            "[RELOGIN] Client not authorized after session promotion"
                        )
                        log.info(
                            "[RELOGIN] Session file may be invalid or expired - waiting for new auth"
                        )
                except Exception as exc:
                    log.error("[RELOGIN] Reconnect failed: %s", exc, exc_info=True)
                handshake_gate.set()
                active_request_id = None
                deadline = None
                try:
                    r.set(
                        key,
                        json.dumps(
                            {
                                "status": "worker_resumed",
                                "request_id": request_id,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                        ex=60,
                    )
                except Exception:
                    pass
                continue

            if active_request_id and deadline and datetime.now(timezone.utc) > deadline:
                log.warning(
                    "Re-login handshake %s timed out waiting for completion; resuming worker",
                    active_request_id,
                )
                try:
                    await client.connect()
                except Exception as exc:
                    log.debug("Reconnect after handshake timeout failed: %s", exc)
                handshake_gate.set()
                try:
                    r.set(
                        key,
                        json.dumps(
                            {
                                "status": "timeout",
                                "request_id": active_request_id,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                        ex=60,
                    )
                except Exception:
                    pass
                active_request_id = None
                deadline = None

    # Setup graceful shutdown: disconnect client to flush session state
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    async def _graceful_shutdown():
        try:
            log.info("Shutting down; disconnecting Telegram client to flush session...")
            try:
                # Explicitly save session before disconnect
                try:
                    if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                        client.session.save()  # type: ignore[misc]
                except Exception:
                    pass

                await asyncio.wait_for(
                    client.disconnect(), timeout=15  # type: ignore[misc]
                )
            except asyncio.TimeoutError:
                log.warning(
                    "Client disconnect timed out after 15s; proceeding with shutdown"
                )
            except Exception:
                pass
        except Exception:
            pass

    def _signal_handler():
        """Signal handler that safely triggers shutdown from signal context."""
        loop.call_soon_threadsafe(shutdown_event.set)

    try:
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
    except NotImplementedError:
        # Signals not available (e.g., on Windows); ignore
        pass

    # Clear any stale auth requests from previous sessions before starting worker
    try:
        cleared = 0
        while r.lpop(AUTH_QUEUE_KEY):
            cleared += 1
        if cleared > 0:
            log.info(
                "[AUTH] Cleared %d stale auth request(s) from previous session", cleared
            )

        # Also clear stale auth responses
        auth_response_pattern = "tgsentinel:auth_responses"
        try:
            r.delete(auth_response_pattern)
            log.debug("[AUTH] Cleared stale auth responses hash")
        except Exception:
            pass

        # Clear any stale relogin handshake markers from previous sessions
        relogin_key = "tgsentinel:relogin"
        try:
            stale = r.get(relogin_key)
            if stale:
                r.delete(relogin_key)
                log.info("[STARTUP] Cleared stale relogin handshake marker")
        except Exception:
            pass
    except Exception as exc:
        log.warning("[AUTH] Failed to clear stale auth queue: %s", exc)

    # Start auth_worker early so UI can trigger auth requests
    auth_worker_task = asyncio.create_task(auth_queue_worker())
    log.info("[STARTUP] Auth queue worker started")

    # Start HTTP API server for UI communication
    api_port = int(os.getenv("SENTINEL_API_PORT", "8080"))
    set_config(cfg)
    set_redis_client(r)
    set_sentinel_state("session_path", str(session_file_path))
    start_api_server(host="0.0.0.0", port=api_port)
    log.info("[STARTUP] HTTP API server started on port %d", api_port)

    # Non-interactive startup: do not prompt for phone in headless envs
    log.info("[STARTUP] Connecting to Telegram...")
    await client.connect()  # type: ignore[misc]
    log.info("[STARTUP] Connected to Telegram")

    # Now start relogin_coordinator after initial connection is established
    # This prevents race conditions during the initial connect phase
    relogin_coordinator_task = asyncio.create_task(relogin_coordinator())
    log.info("[STARTUP] Relogin coordinator started")

    # Start session update monitor immediately to catch uploaded sessions
    async def session_monitor():
        """Monitor for uploaded session files and logout requests."""
        nonlocal authorized, client
        pubsub = r.pubsub()
        try:
            await asyncio.to_thread(pubsub.subscribe, "tgsentinel:session_updated")
            log.info(
                "[SESSION-MONITOR] Listening for session uploads and logout events"
            )

            while True:
                message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if message and message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        event_type = data.get("event")

                        if event_type == "session_imported":
                            log.info(
                                "[SESSION-MONITOR] Session upload detected, recreating client with new session"
                            )
                            # Telethon caches session state in memory - we must create a new client
                            # instance to pick up the newly uploaded session file
                            try:
                                old_client = client
                                try:
                                    if old_client.is_connected():
                                        # Remove all event handlers before disconnect to prevent duplicate processing
                                        try:
                                            handlers = old_client.list_event_handlers()
                                            for callback, event in handlers:
                                                old_client.remove_event_handler(
                                                    callback, event
                                                )
                                            log.debug(
                                                "[SESSION-MONITOR] Removed %d event handler(s) from old client",
                                                len(handlers),
                                            )
                                        except Exception as handler_exc:
                                            log.debug(
                                                "Failed to remove handlers: %s",
                                                handler_exc,
                                            )

                                        # Telethon's disconnect() is synchronous and returns None
                                        old_client.disconnect()
                                    _close_session_binding()
                                    # Ensure session file locks are released
                                    await asyncio.sleep(0.5)
                                    log.debug(
                                        "[SESSION-MONITOR] Old client disconnected and session closed"
                                    )
                                except Exception as disc_exc:
                                    log.debug(
                                        "Disconnect during session reload: %s", disc_exc
                                    )

                                # Explicitly delete old client reference to release resources
                                del old_client
                                await asyncio.sleep(
                                    0.3
                                )  # Additional time for SQLite lock release

                                # Publish login progress: creating client
                                try:
                                    r.set(
                                        "tgsentinel:login_progress",
                                        json.dumps(
                                            {
                                                "stage": "creating_client",
                                                "percent": 20,
                                                "message": "Creating Telegram client...",
                                                "timestamp": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )
                                except Exception:
                                    pass

                                # Create fresh client with uploaded session file
                                log.info(
                                    "[SESSION-MONITOR] Creating new client instance"
                                )
                                client = make_client(cfg)

                                # Publish login progress: connecting
                                try:
                                    r.set(
                                        "tgsentinel:login_progress",
                                        json.dumps(
                                            {
                                                "stage": "connecting",
                                                "percent": 40,
                                                "message": "Connecting to Telegram...",
                                                "timestamp": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )
                                except Exception:
                                    pass

                                # Connect to Telegram
                                log.info(
                                    "[SESSION-MONITOR] Connecting to Telegram with new session"
                                )
                                await asyncio.wait_for(client.connect(), timeout=30)
                                log.info(
                                    "[SESSION-MONITOR] Connection established, checking authorization"
                                )

                                # Publish login progress: verifying
                                try:
                                    r.set(
                                        "tgsentinel:login_progress",
                                        json.dumps(
                                            {
                                                "stage": "verifying",
                                                "percent": 60,
                                                "message": "Verifying authorization...",
                                                "timestamp": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )
                                except Exception:
                                    pass

                                # Check authorization
                                me = await asyncio.wait_for(client.get_me(), timeout=10)
                                log.info("[SESSION-MONITOR] get_me() returned: %s", me)

                                if me:
                                    # Type assertion: get_me() returns User when authorized
                                    from telethon.tl.types import User as TgUser

                                    assert isinstance(
                                        me, TgUser
                                    ), "Expected User object from get_me()"
                                    log.info(
                                        "[SESSION-MONITOR] ✓ New session authorized: @%s (ID: %s)",
                                        me.username or "no_username",
                                        me.id,
                                    )
                                    authorized = True
                                    auth_event.set()
                                    handshake_gate.set()  # Unblock CACHE-REFRESHER and other handlers

                                    # Update worker status in Redis
                                    try:
                                        r.set(
                                            "tgsentinel:worker_status",
                                            json.dumps(
                                                {
                                                    "authorized": True,
                                                    "status": "authorized",
                                                    "ts": datetime.now(
                                                        timezone.utc
                                                    ).isoformat(),
                                                }
                                            ),
                                        )  # No TTL
                                        # Publish login progress: downloading avatar
                                        try:
                                            r.set(
                                                "tgsentinel:login_progress",
                                                json.dumps(
                                                    {
                                                        "stage": "avatar",
                                                        "percent": 70,
                                                        "message": "Downloading user avatar...",
                                                        "timestamp": datetime.now(
                                                            timezone.utc
                                                        ).isoformat(),
                                                    }
                                                ),
                                            )
                                        except Exception:
                                            pass

                                        # Store user info in Redis
                                        user_info = {
                                            "username": me.username,
                                            "first_name": me.first_name,
                                            "last_name": me.last_name,
                                            "phone": me.phone,
                                            "user_id": me.id,
                                        }

                                        # Cache user avatar if available (MUST happen before login completion)
                                        if hasattr(me, "photo") and me.photo:
                                            try:
                                                from .client import _cache_avatar

                                                avatar_url = await _cache_avatar(
                                                    client, me.id, me.photo, r
                                                )
                                                if avatar_url:
                                                    user_info["avatar"] = avatar_url
                                                    log.info(
                                                        "[SESSION-MONITOR] ✓ Cached user avatar: %s",
                                                        avatar_url,
                                                    )
                                            except Exception as avatar_exc:
                                                log.warning(
                                                    "[SESSION-MONITOR] Failed to cache avatar: %s",
                                                    avatar_exc,
                                                )
                                        else:
                                            # No avatar available, use default
                                            user_info["avatar"] = (
                                                "/static/images/logo.png"
                                            )
                                            log.debug(
                                                "[SESSION-MONITOR] No avatar photo, using default"
                                            )

                                        user_info_json = json.dumps(user_info)
                                        log.info(
                                            "[SESSION-MONITOR] About to write user_info to Redis: %s",
                                            user_info_json,
                                        )
                                        r.set(
                                            "tgsentinel:user_info",
                                            user_info_json,
                                        )  # No TTL
                                        log.info(
                                            "[SESSION-MONITOR] ✓ Updated Redis with user info (avatar: %s)",
                                            user_info.get("avatar", "not set"),
                                        )
                                        # Verify
                                        verify_ui = r.get("tgsentinel:user_info")
                                        if verify_ui:
                                            log.info(
                                                "[SESSION-MONITOR] Verified user_info in Redis"
                                            )
                                        else:
                                            log.error(
                                                "[SESSION-MONITOR] Failed to verify user_info in Redis!"
                                            )

                                        # Publish login progress: fetching dialogs (80%)
                                        try:
                                            r.set(
                                                "tgsentinel:login_progress",
                                                json.dumps(
                                                    {
                                                        "stage": "fetching_dialogs",
                                                        "percent": 80,
                                                        "message": "Loading channels and contacts...",
                                                        "timestamp": datetime.now(
                                                            timezone.utc
                                                        ).isoformat(),
                                                    }
                                                ),
                                            )
                                        except Exception:
                                            pass
                                    except Exception as redis_exc:
                                        log.debug(
                                            "Failed to update Redis after auth: %s",
                                            redis_exc,
                                        )

                                    # Re-register message ingestion handler after session reload
                                    try:
                                        from .client import start_ingestion

                                        start_ingestion(cfg, client, r)
                                        log.info(
                                            "[SESSION-MONITOR] ✓ Message ingestion handler re-registered"
                                        )
                                    except Exception as ingestion_exc:
                                        log.error(
                                            "[SESSION-MONITOR] Failed to re-register ingestion handler: %s",
                                            ingestion_exc,
                                            exc_info=True,
                                        )

                                    # Clear dialogs cache to force refresh with new session
                                    try:
                                        nonlocal dialogs_cache
                                        dialogs_cache = None
                                        log.info(
                                            "[SESSION-MONITOR] ✓ Cleared dialogs cache"
                                        )
                                    except Exception as cache_exc:
                                        log.debug(
                                            "[SESSION-MONITOR] Failed to clear dialogs cache: %s",
                                            cache_exc,
                                        )

                                    # Publish session_updated event to trigger CACHE-REFRESHER
                                    try:
                                        r.publish(
                                            "tgsentinel:session_updated",
                                            json.dumps(
                                                {
                                                    "event": "session_authorized",
                                                    "user_id": me.id,
                                                    "timestamp": datetime.now(
                                                        timezone.utc
                                                    ).isoformat(),
                                                }
                                            ),
                                        )
                                        log.info(
                                            "[SESSION-MONITOR] ✓ Published session_updated event to trigger cache refresh"
                                        )
                                    except Exception as pub_exc:
                                        log.error(
                                            "[SESSION-MONITOR] Failed to publish session_updated: %s",
                                            pub_exc,
                                        )

                                    # Publish login completion (100%) - no TTL
                                    # Cache refresher will run in background and update this if needed
                                    try:
                                        r.set(
                                            "tgsentinel:login_progress",
                                            json.dumps(
                                                {
                                                    "stage": "completed",
                                                    "percent": 100,
                                                    "message": "Session switch complete! Loading channels and contacts...",
                                                    "timestamp": datetime.now(
                                                        timezone.utc
                                                    ).isoformat(),
                                                }
                                            ),
                                        )
                                        log.info(
                                            "[SESSION-MONITOR] Published login completion (100%)"
                                        )
                                    except Exception as completion_exc:
                                        log.debug(
                                            "[SESSION-MONITOR] Failed to publish login completion: %s",
                                            completion_exc,
                                        )
                                else:
                                    log.warning(
                                        "[SESSION-MONITOR] get_me() returned None - session file appears invalid or expired"
                                    )
                                    log.warning(
                                        "[SESSION-MONITOR] The session may need phone number verification or the credentials don't match"
                                    )
                            except asyncio.TimeoutError:
                                log.error(
                                    "[SESSION-MONITOR] Connection or auth check timed out"
                                )
                            except Exception as e:
                                log.error(
                                    "[SESSION-MONITOR] Client recreation failed: %s",
                                    e,
                                    exc_info=True,
                                )

                        elif event_type == "session_logout":
                            log.info(
                                "[SESSION-MONITOR] Logout request detected, disconnecting..."
                            )

                            # Publish initial logout progress (20%) - no TTL
                            try:
                                r.set(
                                    "tgsentinel:logout_progress",
                                    json.dumps(
                                        {
                                            "stage": "disconnecting",
                                            "percent": 20,
                                            "message": "Disconnecting from Telegram...",
                                            "timestamp": datetime.now(
                                                timezone.utc
                                            ).isoformat(),
                                        }
                                    ),
                                )
                                log.info(
                                    "[SESSION-MONITOR] Published logout progress (20%)"
                                )
                            except Exception:
                                pass

                            try:
                                # Disconnect from Telegram - wait for graceful disconnect
                                if client.is_connected():
                                    log.debug(
                                        "[SESSION-MONITOR] Disconnecting client gracefully..."
                                    )

                                    # Cancel all pending Telethon tasks before disconnect
                                    # This prevents "Task was destroyed but it is pending" errors
                                    all_tasks = asyncio.all_tasks()
                                    telethon_tasks = [
                                        t
                                        for t in all_tasks
                                        if not t.done()
                                        and any(
                                            name in str(t.get_coro())
                                            for name in [
                                                "_recv_loop",
                                                "_send_loop",
                                                "MTProtoSender",
                                                "Connection",
                                            ]
                                        )
                                    ]
                                    if telethon_tasks:
                                        log.info(
                                            f"[SESSION-MONITOR] Cancelling {len(telethon_tasks)} pending Telethon tasks"
                                        )
                                        for task in telethon_tasks:
                                            task.cancel()
                                        # Wait for tasks to be cancelled with timeout
                                        await asyncio.wait(telethon_tasks, timeout=2.0)
                                        log.info(
                                            "[SESSION-MONITOR] Telethon tasks cancelled"
                                        )

                                    # Remove all event handlers before disconnect to prevent stale processing
                                    try:
                                        handlers = client.list_event_handlers()
                                        for callback, event in handlers:
                                            client.remove_event_handler(callback, event)
                                        log.debug(
                                            "[SESSION-MONITOR] Removed %d event handler(s)",
                                            len(handlers),
                                        )
                                    except Exception as handler_exc:
                                        log.debug(
                                            "Failed to remove handlers: %s", handler_exc
                                        )

                                    # Explicitly save session before disconnect
                                    try:
                                        if hasattr(client, "session") and hasattr(
                                            client.session, "save"
                                        ):  # type: ignore[misc]
                                            client.session.save()  # type: ignore[misc]
                                    except Exception:
                                        pass

                                    # Telethon's disconnect() is synchronous and returns None
                                    client.disconnect()
                                    _close_session_binding()

                                    # Wait for Telethon to complete its cleanup
                                    # This prevents "no such table: entities" errors
                                    await asyncio.sleep(1.5)
                                    log.info(
                                        "[SESSION-MONITOR] ✓ Disconnected from Telegram"
                                    )

                                # Update progress: disconnected
                                try:
                                    r.set(
                                        "tgsentinel:logout_progress",
                                        json.dumps(
                                            {
                                                "stage": "disconnected",
                                                "percent": 50,
                                                "message": "Disconnected from Telegram",
                                                "timestamp": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )
                                except Exception:
                                    pass

                                # Delete session files to ensure clean logout
                                try:
                                    await asyncio.sleep(0.5)  # Extra safety margin

                                    # Update progress: deleting files
                                    try:
                                        r.set(
                                            "tgsentinel:logout_progress",
                                            json.dumps(
                                                {
                                                    "stage": "deleting_files",
                                                    "percent": 70,
                                                    "message": "Removing session files...",
                                                    "timestamp": datetime.now(
                                                        timezone.utc
                                                    ).isoformat(),
                                                }
                                            ),
                                        )
                                    except Exception:
                                        pass

                                    for suffix in ["", "-shm", "-wal", "-journal"]:
                                        session_file = Path(
                                            str(session_file_path) + suffix
                                        )
                                        if session_file.exists():
                                            session_file.unlink()
                                            log.info(
                                                "[SESSION-MONITOR] Deleted session file: %s",
                                                session_file.name,
                                            )
                                except Exception as file_exc:
                                    log.warning(
                                        "[SESSION-MONITOR] Failed to delete session files: %s",
                                        file_exc,
                                    )

                                # Mark as not authorized
                                authorized = False
                                auth_event.clear()

                                # Clear worker status and all cache keys in Redis
                                try:
                                    # Update progress: clearing Redis
                                    r.set(
                                        "tgsentinel:logout_progress",
                                        json.dumps(
                                            {
                                                "stage": "clearing_redis",
                                                "percent": 85,
                                                "message": "Clearing authentication and cache...",
                                                "timestamp": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )

                                    r.set(
                                        "tgsentinel:worker_status",
                                        json.dumps(
                                            {
                                                "authorized": False,
                                                "status": "logged_out",
                                                "ts": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )  # No TTL - persist until next login

                                    # Clear user info from Redis
                                    r.delete("tgsentinel:user_info")

                                    # Clear cache keys (channels, users, cache_ready flag)
                                    r.delete("tgsentinel:cached_channels")
                                    r.delete("tgsentinel:cached_users")
                                    r.delete("tgsentinel:cache_ready")

                                    # Clear any progress tracking keys
                                    r.delete("tgsentinel:login_progress")
                                    # logout_progress will be cleaned up after completion

                                    # Clear avatar cache (pattern-based deletion)
                                    try:
                                        # Clear user and channel avatar patterns
                                        for pattern in [
                                            "tgsentinel:user_avatar:*",
                                            "tgsentinel:channel_avatar:*",
                                        ]:
                                            cursor = "0"
                                            keys_to_delete = []
                                            while cursor != 0:
                                                cursor, keys = r.scan(cursor, match=pattern, count=100)  # type: ignore[assignment]
                                                if keys:
                                                    keys_to_delete.extend(keys)
                                            if keys_to_delete:
                                                r.delete(*keys_to_delete)
                                        log.info(
                                            "[SESSION-MONITOR] Cleared avatar cache"
                                        )
                                    except Exception as avatar_exc:
                                        log.debug(
                                            "[SESSION-MONITOR] Failed to clear avatar cache: %s",
                                            avatar_exc,
                                        )

                                    # Clear message ingestion stream to remove stale/duplicate messages
                                    try:
                                        stream_key = cfg.redis["stream"]
                                        r.delete(stream_key)
                                        log.info(
                                            "[SESSION-MONITOR] Cleared message stream: %s",
                                            stream_key,
                                        )
                                    except Exception as stream_exc:
                                        log.debug(
                                            "[SESSION-MONITOR] Failed to clear message stream: %s",
                                            stream_exc,
                                        )

                                    log.info(
                                        "[SESSION-MONITOR] Cleared user info, cache, and progress keys from Redis"
                                    )
                                except Exception as redis_exc:
                                    log.warning(
                                        "[SESSION-MONITOR] Failed to clear Redis data: %s",
                                        redis_exc,
                                    )

                                # Clear config files on logout
                                try:
                                    import glob

                                    config_files = glob.glob(
                                        "config/*.yml"
                                    ) + glob.glob("config/*.yaml")
                                    for config_file in config_files:
                                        try:
                                            os.remove(config_file)
                                            log.info(
                                                "[SESSION-MONITOR] Deleted config file: %s",
                                                config_file,
                                            )
                                        except Exception as file_exc:
                                            log.debug(
                                                "[SESSION-MONITOR] Failed to delete config file %s: %s",
                                                config_file,
                                                file_exc,
                                            )
                                except Exception as config_exc:
                                    log.debug(
                                        "[SESSION-MONITOR] Failed to clear config files: %s",
                                        config_exc,
                                    )

                                # ALL cleanup complete - now log and publish 100%
                                log.info(
                                    "[SESSION-MONITOR] Logout completed, session files deleted, waiting for new session"
                                )

                                # Final progress: completed (100%) with redirect trigger - no TTL
                                try:
                                    r.set(
                                        "tgsentinel:logout_progress",
                                        json.dumps(
                                            {
                                                "stage": "completed",
                                                "percent": 100,
                                                "message": "Logout complete! Redirecting...",
                                                "redirect": "/logout",
                                                "timestamp": datetime.now(
                                                    timezone.utc
                                                ).isoformat(),
                                            }
                                        ),
                                    )
                                    log.info(
                                        "[SESSION-MONITOR] Published logout completion (100%) with redirect"
                                    )
                                except Exception:
                                    pass
                            except Exception as e:
                                log.error("[SESSION-MONITOR] Logout failed: %s", e)

                    except Exception as e:
                        log.debug("[SESSION-MONITOR] Parse error: %s", e)
                await asyncio.sleep(0.1)
        except Exception as e:
            log.error("[SESSION-MONITOR] Fatal error: %s", e)
        finally:
            try:
                await asyncio.to_thread(pubsub.close)
            except:
                pass

    session_monitor_task = asyncio.create_task(session_monitor())
    log.info("[STARTUP] Session monitor started")

    # Fix session file permissions after connect (Telethon may create it here)
    try:
        session_path = Path(cfg.telegram_session or "/app/data/tgsentinel.session")
        if session_path.exists():
            size = session_path.stat().st_size
            os.chmod(session_path, 0o666)
            log.debug(
                "[STARTUP] Session file exists: %s (%d bytes, permissions fixed)",
                session_path,
                size,
            )
        else:
            log.info("[STARTUP] No existing session file found")
    except Exception as perm_exc:
        log.warning("[STARTUP] Session file check failed: %s", perm_exc)

    try:
        # Try to load session from database by calling get_me()
        # This forces Telethon to deserialize the auth key from SQLite
        log.info("[STARTUP] Checking existing session...")
        try:
            me = await asyncio.wait_for(
                client.get_me(), timeout=15  # type: ignore[misc]
            )
            user_id = getattr(me, "id", None) if me else None
            username = getattr(me, "username", None) if me else None

            if me:
                log.info(
                    "[STARTUP] ✓ Session restored successfully: user_id=%s, username=%s",
                    user_id,
                    username,
                )
                _mark_authorized(me)
            else:
                log.info(
                    "[STARTUP] get_me() returned None - session file exists but not authorized"
                )
                authorized = False
        except asyncio.TimeoutError:
            log.warning(
                "[STARTUP] get_me() timed out after 15s, checking authorization..."
            )
            try:
                authorized = await asyncio.wait_for(
                    client.is_user_authorized(), timeout=10  # type: ignore[misc]
                )
                if authorized:
                    log.info("[STARTUP] ✓ Client is authorized (direct check)")
                    _mark_authorized()
                else:
                    log.info("[STARTUP] ✗ Client is not authorized")
            except asyncio.TimeoutError:
                log.warning("[STARTUP] Authorization check timed out")
                authorized = False
            except Exception as auth_exc:
                log.warning("[STARTUP] Authorization check failed: %s", auth_exc)
                authorized = False
        except Exception as getme_err:
            # Not authorized, check directly
            log.info("[STARTUP] get_me() failed: %s", getme_err)
            log.info("[STARTUP] Checking authorization status directly...")
            try:
                authorized = await asyncio.wait_for(
                    client.is_user_authorized(), timeout=10  # type: ignore[misc]
                )
                if authorized:
                    log.info("[STARTUP] ✓ Client is authorized (direct check)")
                    _mark_authorized()
                else:
                    log.info("[STARTUP] ✗ Client is not authorized")
            except asyncio.TimeoutError:
                log.warning("[STARTUP] Authorization check timed out")
                authorized = False
            except Exception as auth_exc:
                log.warning("[STARTUP] Authorization check failed: %s", auth_exc)
                authorized = False
    except Exception as auth_exc:
        log.error("[STARTUP] Authorization check failed: %s", auth_exc, exc_info=True)
        authorized = False

    if not authorized:
        log.warning("[STARTUP] ✗ No valid session found - authentication required")
        wait_total = int(os.getenv("SESSION_WAIT_SECS", "300"))
        interval = 3
        waited = 0

        log.warning(
            "No Telegram session found. Waiting up to %ss for UI login at http://localhost:5001",
            wait_total,
        )
        log.info("Complete the login in the UI, sentinel will detect it automatically")
        try:
            r.setex(
                "tgsentinel:worker_status",
                30,
                json.dumps(
                    {
                        "authorized": False,
                        "status": "waiting",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
        except Exception:
            pass

        while waited < wait_total and not authorized:
            try:
                await asyncio.wait_for(auth_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                waited += interval
                if waited % 30 == 0:
                    log.info("Still waiting for login... (%ss/%ss)", waited, wait_total)
                continue

        if not authorized:
            log.error("Session not available after %ss. Please:", wait_total)
            log.error("  1. Go to http://localhost:5001")
            log.error("  2. Complete the Telegram login")
            log.error("  3. Run: docker compose restart sentinel")
            try:
                r.setex(
                    "tgsentinel:worker_status",
                    60,
                    json.dumps(
                        {
                            "authorized": False,
                            "status": "unauthorized",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )
            except Exception:
                pass
            return

    # Get and store current user info in Redis for UI access
    try:
        me = await client.get_me()  # type: ignore[misc]
        if me:
            await _refresh_user_identity_cache(me)
        else:
            log.warning("get_me() returned None during startup cache refresh")
    except asyncio.CancelledError:
        # Relogin coordinator may disconnect during this operation; that's okay
        log.debug("User info cache refresh cancelled (likely due to relogin)")
    except Exception as e:
        log.warning("Failed to fetch/store user info: %s", e)

    log.info("Sentinel started - monitoring %d channels", len(cfg.channels))
    for ch in cfg.channels:
        log.info("  • %s (id: %d)", ch.name, ch.id)

    # Send a test digest on startup if TEST_DIGEST env var is set
    if os.getenv("TEST_DIGEST", "").lower() in ("1", "true", "yes"):
        log.info("TEST_DIGEST enabled, sending digest on startup...")
        await send_digest(
            engine,
            client,
            since_hours=24,
            top_n=cfg.alerts.digest.top_n,
            mode=cfg.alerts.mode,
            channel=cfg.alerts.target_channel,
            channels_config=cfg.channels,
        )
        log.info("Test digest sent!")

    # Start ingestion once authorized
    start_ingestion(cfg, client, r)

    # Send initial digests on startup if enabled
    if cfg.alerts.digest.hourly:
        log.info("Sending initial hourly digest on startup...")
        await send_digest(
            engine,
            client,
            since_hours=1,
            top_n=cfg.alerts.digest.top_n,
            mode=cfg.alerts.mode,
            channel=cfg.alerts.target_channel,
            channels_config=cfg.channels,
        )

    if cfg.alerts.digest.daily:
        log.info("Sending initial daily digest on startup...")
        await send_digest(
            engine,
            client,
            since_hours=24,
            top_n=cfg.alerts.digest.top_n,
            mode=cfg.alerts.mode,
            channel=cfg.alerts.target_channel,
            channels_config=cfg.channels,
        )

    async def worker():
        await process_loop(cfg, client, engine, handshake_gate)

    async def periodic():
        # hourly digest
        while True:
            await asyncio.sleep(3600)
            if cfg.alerts.digest.hourly:
                await handshake_gate.wait()
                log.info("Sending hourly digest...")
                await send_digest(
                    engine,
                    client,
                    since_hours=1,
                    top_n=cfg.alerts.digest.top_n,
                    mode=cfg.alerts.mode,
                    channel=cfg.alerts.target_channel,
                    channels_config=cfg.channels,
                )  # noqa

    async def daily():
        while True:
            await asyncio.sleep(86400)
            if cfg.alerts.digest.daily:
                await handshake_gate.wait()
                log.info("Sending daily digest...")
                await send_digest(
                    engine,
                    client,
                    since_hours=24,
                    top_n=cfg.alerts.digest.top_n,
                    mode=cfg.alerts.mode,
                    channel=cfg.alerts.target_channel,
                    channels_config=cfg.channels,
                )

    async def metrics_logger():
        while True:
            await asyncio.sleep(300)
            log.info("Sentinel heartbeat - monitoring active")
            dump()

    async def worker_status_refresher():
        """Periodically refresh worker status in Redis to ensure it doesn't expire."""
        while True:
            await asyncio.sleep(600)  # Every 10 minutes
            if authorized:
                try:
                    r.setex(
                        "tgsentinel:worker_status",
                        3600,
                        json.dumps(
                            {
                                "authorized": True,
                                "status": "authorized",
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                    log.debug("[HEARTBEAT] Worker status refreshed in Redis")
                except Exception as exc:
                    log.warning("[HEARTBEAT] Failed to refresh worker status: %s", exc)

    async def participant_info_handler():
        """Handle participant info requests from UI."""
        while True:
            try:
                await handshake_gate.wait()
                # Check for pending requests frequently to reduce UI latency
                await asyncio.sleep(1)

                # Scan for request keys using non-blocking scan_iter
                for key in r.scan_iter("tgsentinel:participant_request:*"):
                    try:
                        # Decode key if it's bytes
                        if isinstance(key, bytes):
                            key = key.decode()

                        request_data = r.get(key)
                        if not request_data:
                            continue

                        # Ensure request_data is a string
                        if isinstance(request_data, bytes):
                            request_data = request_data.decode()

                        req = json.loads(str(request_data))

                        chat_id = req.get("chat_id")
                        user_id = req.get("user_id")

                        # Fetch participant info
                        participant_info = await _fetch_participant_info(
                            client, chat_id, user_id, log, r
                        )

                        # Cache the result (30 minute TTL)
                        cache_key = f"tgsentinel:participant:{chat_id}:{user_id if user_id else 'chat'}"
                        r.setex(cache_key, 1800, json.dumps(participant_info))

                        # Delete the request key
                        r.delete(key)

                    except Exception as e:
                        log.debug("Error processing participant request %s: %s", key, e)
                        r.delete(key)  # Clean up failed request

            except Exception as e:
                log.error("Participant info handler error: %s", e)
                await asyncio.sleep(5)

    async def telegram_chats_handler():
        """Handle Telegram chats discovery requests from UI.

        Serves from Redis cache instantly if available, otherwise indicates cache is building.
        """
        log.info("[CHATS-HANDLER] Starting chats handler (serving from Redis cache)")

        REDIS_CHANNELS_KEY = "tgsentinel:cached_channels"
        REDIS_CACHE_READY_KEY = "tgsentinel:cache_ready"

        while True:
            try:
                await handshake_gate.wait()
                await asyncio.sleep(1)

                # Check if authorized before processing
                if not authorized:
                    log.debug("[CHATS-HANDLER] Not authorized, skipping scan")
                    continue

                # Scan for chat discovery requests
                keys_found = list(r.scan_iter("tgsentinel:telegram_chats_request:*"))
                if keys_found:
                    log.info(
                        "[CHATS-HANDLER] Found %d chat request(s)", len(keys_found)
                    )

                for key in keys_found:
                    try:
                        # Decode key if it's bytes
                        if isinstance(key, bytes):
                            key = key.decode()

                        request_data = r.get(key)
                        if not request_data:
                            continue

                        # Ensure request_data is a string
                        if isinstance(request_data, bytes):
                            request_data = request_data.decode()

                        req = json.loads(str(request_data))
                        request_id = req.get("request_id")

                        log.info("[CHATS-HANDLER] Processing request_id=%s", request_id)

                        # Check if cache is ready
                        cache_ready = r.get(REDIS_CACHE_READY_KEY)

                        if cache_ready:
                            # Serve from cache instantly
                            log.debug("[CHATS-HANDLER] Serving from Redis cache")
                            cached_channels = r.get(REDIS_CHANNELS_KEY)

                            if cached_channels:
                                if isinstance(cached_channels, bytes):
                                    cached_channels = cached_channels.decode()
                                # Type cast: Redis returns str after decode
                                chats = json.loads(str(cached_channels))
                                log.info(
                                    "[CHATS-HANDLER] \u2713 Served %d channels from cache",
                                    len(chats),
                                )
                            else:
                                # Cache key exists but no data (edge case)
                                log.warning(
                                    "[CHATS-HANDLER] Cache ready but no data found"
                                )
                                chats = []
                        else:
                            # Cache not ready yet - return status message
                            log.info(
                                "[CHATS-HANDLER] Cache not ready, returning building status"
                            )
                            chats = []

                            # Send a status response indicating cache is building
                            response_key = (
                                f"tgsentinel:telegram_chats_response:{request_id}"
                            )
                            r.setex(
                                response_key,
                                30,
                                json.dumps(
                                    {
                                        "status": "cache_building",
                                        "message": "Channel cache is being built. Please wait...",
                                        "chats": [],
                                    }
                                ),
                            )

                            # Delete the request key
                            r.delete(key)
                            continue

                        # Send response
                        response_key = (
                            f"tgsentinel:telegram_chats_response:{request_id}"
                        )
                        response_data = {"status": "ok", "chats": chats}
                        r.setex(response_key, 60, json.dumps(response_data))
                        log.debug(
                            "[CHATS-HANDLER] Stored response at key=%s", response_key
                        )

                        # Delete the request key
                        r.delete(key)

                        log.info(
                            "[CHATS-HANDLER] Completed request_id=%s: %d chats returned",
                            request_id,
                            len(chats),
                        )

                    except Exception as e:
                        log.error(
                            "[CHATS-HANDLER] Error processing request key=%s: %s",
                            key,
                            e,
                            exc_info=True,
                        )
                        # Send error response
                        try:
                            if "request_id" in locals():
                                response_key = f"tgsentinel:telegram_chats_response:{request_id}"  # type: ignore[possibly-undefined]
                                error_data = {
                                    "status": "error",
                                    "message": str(e),
                                }
                                r.setex(response_key, 60, json.dumps(error_data))
                                log.debug("[CHATS-HANDLER] Stored error response")
                        except Exception:
                            pass
                        r.delete(key)  # Clean up failed request

            except Exception as e:
                log.error("[CHATS-HANDLER] Handler loop error: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def telegram_dialogs_handler():
        """Handle Telegram dialogs (chats) requests from UI."""
        log.info(
            "[DIALOGS-HANDLER] Starting dialogs handler (pattern: tgsentinel:request:get_dialogs:*)"
        )
        while True:
            try:
                await handshake_gate.wait()
                await asyncio.sleep(1)

                # Check if authorized before processing
                if not authorized:
                    log.debug("[DIALOGS-HANDLER] Not authorized, skipping scan")
                    continue

                # Scan for dialogs requests
                keys_found = list(r.scan_iter("tgsentinel:request:get_dialogs:*"))
                if keys_found:
                    log.info(
                        "[DIALOGS-HANDLER] Found %d dialogs request(s)", len(keys_found)
                    )

                for key in keys_found:
                    try:
                        # Decode key if it's bytes
                        if isinstance(key, bytes):
                            key = key.decode()

                        request_data = r.get(key)
                        if not request_data:
                            continue

                        # Ensure request_data is a string
                        if isinstance(request_data, bytes):
                            request_data = request_data.decode()

                        req = json.loads(str(request_data))
                        request_id = req.get("request_id")

                        log.info(
                            "[DIALOGS-HANDLER] Processing request_id=%s", request_id
                        )

                        # Use Redis cache populated by CACHE-REFRESHER (instant response)
                        REDIS_CHANNELS_KEY = "tgsentinel:cached_channels"
                        REDIS_CACHE_READY_KEY = "tgsentinel:cache_ready"

                        chats = []

                        # Check if cache is ready
                        cache_ready = r.get(REDIS_CACHE_READY_KEY)

                        if cache_ready:
                            # Get channels from Redis cache (fast!)
                            cached_channels = r.get(REDIS_CHANNELS_KEY)
                            if cached_channels:
                                # Decode if bytes
                                if isinstance(cached_channels, bytes):
                                    cached_channels = cached_channels.decode("utf-8")
                                # Parse JSON (cached_channels is now str)
                                channels_list = json.loads(str(cached_channels))
                                chats = channels_list
                                log.info(
                                    "[DIALOGS-HANDLER] Served %d channels from Redis cache",
                                    len(chats),
                                )
                            else:
                                log.warning(
                                    "[DIALOGS-HANDLER] Cache ready but no channels data found"
                                )
                        else:
                            # Cache not ready yet - return empty with status
                            log.info(
                                "[DIALOGS-HANDLER] Cache not ready, returning building status"
                            )

                        # Send response
                        response_key = f"tgsentinel:response:get_dialogs:{request_id}"
                        response_data = {"status": "ok", "chats": chats}
                        r.setex(response_key, 60, json.dumps(response_data))
                        log.debug(
                            "[DIALOGS-HANDLER] Stored response at key=%s", response_key
                        )

                        # Delete the request key
                        r.delete(key)

                        log.info(
                            "[DIALOGS-HANDLER] Completed request_id=%s: %d chats returned",
                            request_id,
                            len(chats),
                        )

                    except Exception as e:
                        log.error(
                            "[DIALOGS-HANDLER] Error processing request key=%s: %s",
                            key,
                            e,
                            exc_info=True,
                        )
                        # Send error response
                        try:
                            if "request_id" in locals():
                                response_key = f"tgsentinel:response:get_dialogs:{request_id}"  # type: ignore[possibly-undefined]
                                error_data = {
                                    "status": "error",
                                    "error": str(e),
                                }
                                r.setex(response_key, 60, json.dumps(error_data))
                                log.debug("[DIALOGS-HANDLER] Sent error response for request_id=%s", request_id)  # type: ignore[possibly-undefined]
                        except Exception:
                            pass
                        r.delete(key)  # Clean up failed request

            except Exception as e:
                log.error("[DIALOGS-HANDLER] Handler error: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def telegram_users_handler():
        """Handle Telegram users discovery requests from UI.

        Serves from Redis cache instantly if available, otherwise indicates cache is building.
        """
        log.info("[USERS-HANDLER] Starting users handler (serving from Redis cache)")

        REDIS_USERS_KEY = "tgsentinel:cached_users"
        REDIS_CACHE_READY_KEY = "tgsentinel:cache_ready"

        while True:
            try:
                await handshake_gate.wait()
                await asyncio.sleep(1)

                # Check if authorized before processing
                if not authorized:
                    log.debug("[USERS-HANDLER] Not authorized, skipping scan")
                    continue

                # Scan for user discovery requests
                keys_found = list(r.scan_iter("tgsentinel:telegram_users_request:*"))
                if keys_found:
                    log.info(
                        "[USERS-HANDLER] Found %d user request(s)", len(keys_found)
                    )

                for key in keys_found:
                    try:
                        # Decode key if it's bytes
                        if isinstance(key, bytes):
                            key = key.decode()

                        request_data = r.get(key)
                        if not request_data:
                            continue

                        # Ensure request_data is a string
                        if isinstance(request_data, bytes):
                            request_data = request_data.decode()

                        req = json.loads(str(request_data))
                        request_id = req.get("request_id")

                        log.info("[USERS-HANDLER] Processing request_id=%s", request_id)

                        # Check if cache is ready
                        cache_ready = r.get(REDIS_CACHE_READY_KEY)

                        if cache_ready:
                            # Serve from cache instantly
                            log.debug("[USERS-HANDLER] Serving from Redis cache")
                            cached_users = r.get(REDIS_USERS_KEY)

                            if cached_users:
                                if isinstance(cached_users, bytes):
                                    cached_users = cached_users.decode()
                                # Type cast: Redis returns str after decode
                                users = json.loads(str(cached_users))
                                log.info(
                                    "[USERS-HANDLER] \u2713 Served %d users from cache",
                                    len(users),
                                )
                            else:
                                # Cache key exists but no data (edge case)
                                log.warning(
                                    "[USERS-HANDLER] Cache ready but no data found"
                                )
                                users = []
                        else:
                            # Cache not ready yet - return status message
                            log.info(
                                "[USERS-HANDLER] Cache not ready, returning building status"
                            )
                            users = []

                            # Send a status response indicating cache is building
                            response_key = (
                                f"tgsentinel:telegram_users_response:{request_id}"
                            )
                            r.setex(
                                response_key,
                                30,
                                json.dumps(
                                    {
                                        "status": "cache_building",
                                        "message": "Users cache is being built. Please wait...",
                                        "users": [],
                                    }
                                ),
                            )

                            # Delete the request key
                            r.delete(key)
                            continue

                        # Send response
                        response_key = (
                            f"tgsentinel:telegram_users_response:{request_id}"
                        )
                        response_data = {"status": "ok", "users": users}
                        r.setex(response_key, 60, json.dumps(response_data))
                        log.debug(
                            "[USERS-HANDLER] Stored response at key=%s", response_key
                        )

                        # Delete the request key
                        r.delete(key)

                        log.debug(
                            "[USERS-HANDLER] Completed request_id=%s: %d users",
                            request_id,
                            len(users),
                        )
                        log.info(
                            "[USERS-HANDLER] Returning %d users for request_id=%s",
                            len(users),
                            request_id,
                        )

                    except Exception as e:
                        log.error(
                            "[USERS-HANDLER] Error processing request key=%s: %s",
                            key,
                            e,
                            exc_info=True,
                        )
                        # Send error response
                        try:
                            if "request_id" in locals():
                                response_key = f"tgsentinel:telegram_users_response:{request_id}"  # type: ignore[possibly-undefined]
                                error_data = {
                                    "status": "error",
                                    "message": str(e),
                                }
                                r.setex(response_key, 60, json.dumps(error_data))
                                log.debug("[USERS-HANDLER] Sent error response for request_id=%s", request_id)  # type: ignore[possibly-undefined]
                        except Exception:
                            pass
                        r.delete(key)  # Clean up failed request

            except Exception as e:
                log.error("[USERS-HANDLER] Handler error: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def session_persistence_handler():
        """Periodically save session to disk for durability.

        This ensures that the authenticated session is always persisted to disk,
        even if the process crashes. Critical for avoiding re-authentication.
        """
        log.info("Session persistence handler started")
        while True:
            try:
                await asyncio.sleep(60)  # Save every 60 seconds

                # Only save session if we're authorized (avoid recreating files after logout)
                if not authorized:
                    log.debug("Session persistence skipped (not authorized)")
                    continue

                # Explicitly save session to SQLite
                try:
                    if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                        client.session.save()  # type: ignore[misc]
                        log.debug("Session persisted to disk")
                except Exception as save_exc:
                    log.debug("Could not persist session: %s", save_exc)
            except asyncio.CancelledError:
                # Handle shutdown gracefully
                try:
                    if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                        client.session.save()  # type: ignore[misc]
                        log.debug("Session persisted during shutdown")
                except Exception:
                    pass
                raise
            except Exception as exc:
                log.debug("Session persistence handler error: %s", exc)

    log.info("Starting all handlers with asyncio.gather...")

    # Create a task for all the background workers
    # Note: auth_queue_worker and relogin_coordinator are already started early
    async def run_workers():
        worker_names = [
            "worker",
            "periodic",
            "daily",
            "metrics_logger",
            "worker_status_refresher",
            "participant_info_handler",
            "telegram_chats_handler",
            "telegram_dialogs_handler",
            "telegram_users_handler",
            "session_persistence_handler",
            "channels_users_cache_refresher",
        ]

        results = await asyncio.gather(
            worker(),
            periodic(),
            daily(),
            metrics_logger(),
            worker_status_refresher(),
            participant_info_handler(),
            telegram_chats_handler(),
            telegram_dialogs_handler(),
            telegram_users_handler(),
            session_persistence_handler(),
            channels_users_cache_refresher(),
            return_exceptions=True,
        )

        # Check for exceptions in worker results
        for worker_name, result in zip(worker_names, results):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                log.error(
                    "Worker '%s' failed with exception: %s",
                    worker_name,
                    result,
                    exc_info=result,
                )

    workers_task = asyncio.create_task(run_workers())

    # Wait for either the workers to complete or shutdown signal
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    done, pending = await asyncio.wait(
        [workers_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If shutdown was triggered, cancel workers and perform graceful shutdown
    if shutdown_task in done:
        log.info("Shutdown signal received, cancelling workers...")
        workers_task.cancel()
        # Also cancel early-started background tasks
        auth_worker_task.cancel()
        relogin_coordinator_task.cancel()
        try:
            await workers_task
        except asyncio.CancelledError:
            pass
        try:
            await auth_worker_task
        except asyncio.CancelledError:
            pass
        try:
            await relogin_coordinator_task
        except asyncio.CancelledError:
            pass
        await _graceful_shutdown()
    else:
        # Workers completed (shouldn't happen normally), cancel shutdown task
        shutdown_task.cancel()


if __name__ == "__main__":
    asyncio.run(_run())
