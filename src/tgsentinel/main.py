import asyncio
import json
import logging
import os

from redis import Redis
from telethon import TelegramClient

from .client import make_client, start_ingestion
from .config import load_config
from .digest import send_digest
from .logging_setup import setup_logging
from .metrics import dump
from .store import init_db
from .worker import process_loop


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
                    "username": getattr(user, "username", None),
                    "phone": getattr(user, "phone", None),
                    "bot": getattr(user, "bot", False),
                    "verified": bool(getattr(user, "verified", False)),
                    "scam": bool(getattr(user, "scam", False)),
                    "fake": bool(getattr(user, "fake", False)),
                    "support": bool(getattr(user, "support", False)),
                    "premium": bool(getattr(user, "premium", False)),
                    "restricted": bool(getattr(user, "restricted", False)),
                    "lang_code": getattr(user, "lang_code", None),
                    "access_hash": getattr(user, "access_hash", None),
                }

                # Try to get user avatar
                try:
                    if hasattr(user, "photo") and user.photo:  # type: ignore[attr-defined]
                        # User has a photo, cache it
                        from .client import _cache_avatar

                        user_avatar_url = await _cache_avatar(client, user_id, user.photo, r)  # type: ignore[arg-type]
                        if user_avatar_url:
                            info["user"]["avatar_url"] = user_avatar_url
                except Exception as e:
                    log.debug("Failed to fetch user avatar: %s", e)

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

    client = make_client(cfg)
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)

    start_ingestion(cfg, client, r)

    await client.start()  # type: ignore[misc]  # interactive login on first run

    # Get and store current user info in Redis for UI access
    try:
        me = await client.get_me()  # type: ignore[misc]

        # Download user avatar if available
        avatar_path = None
        try:
            photos = await client.get_profile_photos("me", limit=1)  # type: ignore[misc]
            if photos:
                avatar_filename = "user_avatar.jpg"
                # Save to shared data directory that both containers can access
                avatar_path = f"/app/data/{avatar_filename}"
                await client.download_profile_photo("me", file=avatar_path)  # type: ignore[misc]
                log.info("Downloaded user avatar to %s", avatar_path)
                # Store relative path for UI (will be served from /data endpoint)
                avatar_path = f"/data/{avatar_filename}"
        except Exception as avatar_err:
            log.warning("Could not download user avatar: %s", avatar_err)

        user_info = {
            "username": getattr(me, "username", None)
            or getattr(me, "first_name", "Unknown"),
            "first_name": getattr(me, "first_name", ""),
            "last_name": getattr(me, "last_name", ""),
            "phone": getattr(me, "phone", ""),
            "user_id": getattr(me, "id", None),
            "avatar": avatar_path or "/static/images/logo.png",
        }
        r.set("tgsentinel:user_info", json.dumps(user_info))
        log.info("Stored user info in Redis: %s", user_info.get("username"))
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
        await process_loop(cfg, client, engine)

    async def periodic():
        # hourly digest
        while True:
            await asyncio.sleep(3600)
            if cfg.alerts.digest.hourly:
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

    async def participant_info_handler():
        """Handle participant info requests from UI."""
        while True:
            try:
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
        """Handle Telegram chats discovery requests from UI."""
        while True:
            try:
                await asyncio.sleep(1)

                # Scan for chat discovery requests
                for key in r.scan_iter("tgsentinel:telegram_chats_request:*"):
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

                        log.debug("Processing Telegram chats request: %s", request_id)

                        # Fetch dialogs
                        from telethon.tl.types import Channel, Chat

                        dialogs = await client.get_dialogs()  # type: ignore[misc]
                        chats = []

                        for dialog in dialogs:
                            entity = dialog.entity

                            # Only include channels, groups, and supergroups
                            if isinstance(entity, (Channel, Chat)):
                                chat_type = "channel"
                                if isinstance(entity, Channel):
                                    if entity.broadcast:
                                        chat_type = "channel"
                                    elif entity.megagroup:
                                        chat_type = "supergroup"
                                    else:
                                        chat_type = "group"
                                elif isinstance(entity, Chat):
                                    chat_type = "group"

                                chats.append(
                                    {
                                        "id": entity.id,
                                        "name": getattr(entity, "title", "Unknown"),
                                        "type": chat_type,
                                        "username": getattr(entity, "username", None),
                                    }
                                )

                        # Send response
                        response_key = (
                            f"tgsentinel:telegram_chats_response:{request_id}"
                        )
                        response_data = {"status": "ok", "chats": chats}
                        r.setex(response_key, 60, json.dumps(response_data))

                        # Delete the request key
                        r.delete(key)

                        log.debug(
                            "Completed Telegram chats request: %s (%d chats)",
                            request_id,
                            len(chats),
                        )

                    except Exception as e:
                        log.error("Error processing chats request %s: %s", key, e)
                        # Send error response
                        try:
                            if "request_id" in locals():
                                response_key = f"tgsentinel:telegram_chats_response:{request_id}"  # type: ignore[possibly-undefined]
                                error_data = {
                                    "status": "error",
                                    "message": str(e),
                                }
                                r.setex(response_key, 60, json.dumps(error_data))
                        except Exception:
                            pass
                        r.delete(key)  # Clean up failed request

            except Exception as e:
                log.error("Telegram chats handler error: %s", e)
                await asyncio.sleep(5)

    async def telegram_users_handler():
        """Handle Telegram users discovery requests from UI."""
        log.info("Telegram users handler started")
        while True:
            try:
                await asyncio.sleep(1)

                # Scan for user discovery requests
                keys_found = list(r.scan_iter("tgsentinel:telegram_users_request:*"))
                if keys_found:
                    log.info("Found %d user request keys", len(keys_found))

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

                        log.info("Processing Telegram users request: %s", request_id)

                        # Fetch dialogs
                        log.info("Fetching dialogs from Telegram...")
                        from telethon.tl.types import User

                        dialogs = await client.get_dialogs()  # type: ignore[misc]
                        log.info("Successfully fetched %d dialogs", len(dialogs))
                        users = []

                        for dialog in dialogs:
                            entity = dialog.entity

                            # Include private chats with users (bots included for discovery)
                            if isinstance(entity, User):
                                # Get user name
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
                                        if hasattr(entity, "username")
                                        and entity.username
                                        else f"User {entity.id}"
                                    )
                                )

                                users.append(
                                    {
                                        "id": entity.id,
                                        "name": display_name,
                                        "username": getattr(entity, "username", None),
                                        "phone": getattr(entity, "phone", None),
                                        "bot": getattr(entity, "bot", False),
                                    }
                                )

                        # Send response
                        response_key = (
                            f"tgsentinel:telegram_users_response:{request_id}"
                        )
                        response_data = {"status": "ok", "users": users}
                        r.setex(response_key, 60, json.dumps(response_data))

                        # Delete the request key
                        r.delete(key)

                        log.debug(
                            "Completed Telegram users request %s: %d users",
                            request_id,
                            len(users),
                        )
                        log.info(
                            "Returning %d users for request %s", len(users), request_id
                        )

                    except Exception as e:
                        log.error("Error processing users request %s: %s", key, e)
                        # Send error response
                        try:
                            if "request_id" in locals():
                                response_key = f"tgsentinel:telegram_users_response:{request_id}"  # type: ignore[possibly-undefined]
                                error_data = {
                                    "status": "error",
                                    "message": str(e),
                                }
                                r.setex(response_key, 60, json.dumps(error_data))
                        except Exception:
                            pass
                        r.delete(key)  # Clean up failed request

            except Exception as e:
                log.error("Telegram users handler error: %s", e)
                await asyncio.sleep(5)

    log.info("Starting all handlers with asyncio.gather...")
    await asyncio.gather(
        worker(),
        periodic(),
        daily(),
        metrics_logger(),
        participant_info_handler(),
        telegram_chats_handler(),
        telegram_users_handler(),
    )


if __name__ == "__main__":
    asyncio.run(_run())
