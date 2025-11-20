"""Telegram participant information extraction and caching.

This module provides functions to fetch and extract detailed information about:
- Telegram chats/channels/groups
- Individual users
- Participant roles and permissions

Respects the dual-database architecture: only accesses data through Telethon client.
"""

import logging
from typing import Any, Dict

from telethon import TelegramClient

logger = logging.getLogger(__name__)


def extract_banned_rights(rights) -> Dict[str, Any] | None:
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


def extract_admin_rights(rights) -> Dict[str, Any] | None:
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


async def fetch_participant_info(
    client: TelegramClient, chat_id: int, user_id: int | None, redis_client, log=None
) -> Dict[str, Any]:
    """Fetch comprehensive participant information from Telegram.

    Retrieves detailed information about a chat/channel and optionally a specific
    user within that chat, including:
    - Chat metadata (title, type, permissions, description)
    - User profile information (name, status, bio)
    - Participant role and permissions in the chat
    - Avatars (cached to Redis)

    Args:
        client: Authenticated Telethon client
        chat_id: Telegram chat/channel/group ID
        user_id: Optional user ID to fetch participant-specific info
        redis_client: Redis client for avatar caching
        log: Optional logger instance

    Returns:
        Dictionary containing chat, user, and participant information
        Structure: {"chat": {...}, "user": {...}, "participant": {...}}
    """
    if log is None:
        log = logger

    info: Dict[str, Any] = {}

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

                avatar_url = await _cache_avatar(client, chat_id, chat.photo, redis_client)  # type: ignore[arg-type]
                if avatar_url:
                    info["chat"]["avatar_url"] = avatar_url
        except Exception as e:
            log.debug("Failed to fetch chat avatar: %s", e)

        # Try to fetch extended chat information (description, permissions, pinned message)
        try:
            if info["chat"]["type"] in {"channel", "supergroup"}:
                from telethon.tl.functions.channels import (
                    GetFullChannelRequest,  # type: ignore
                )

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
                    extracted_rights = extract_banned_rights(dbr)
                    if extracted_rights is not None:
                        info["chat"]["default_banned_rights"] = extracted_rights
                    # Exported invite
                    inv = getattr(ch_full, "exported_invite", None)
                    if inv is not None:
                        info["chat"]["invite_link"] = getattr(inv, "link", None)
            elif info["chat"]["type"] in {"group", "chat"}:
                from telethon.tl.functions.messages import (
                    GetFullChatRequest,  # type: ignore
                )

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
                    extracted_rights = extract_banned_rights(dbr)
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
            extracted_rights = extract_admin_rights(ar)
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

                        user_avatar_url = await _cache_avatar(client, user_id, user.photo, redis_client)  # type: ignore[arg-type]
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
                    from telethon.tl.functions.users import (
                        GetFullUserRequest,  # type: ignore
                    )

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
                    from telethon.tl.functions.channels import (
                        GetParticipantRequest,  # type: ignore
                    )

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
                                extracted_rights = extract_admin_rights(p.admin_rights)
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
                                extracted_rights = extract_banned_rights(
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
