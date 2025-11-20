import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from redis import Redis
from telethon import TelegramClient

from .config import AppCfg, ChannelRule, load_config
from .heuristics import run_heuristics
from .metrics import inc
from .notifier import notify_channel, notify_dm
from .profile_resolver import ProfileResolver
from .semantic import load_interests, score_text
from .store import mark_alerted, upsert_message

log = logging.getLogger(__name__)

StreamEntry = Tuple[str, Dict[str, str]]
StreamResponse = List[Tuple[str, List[StreamEntry]]]


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        return int(value)
    raise TypeError(f"Unsupported value type: {type(value)!r}")


def load_rules(cfg: AppCfg):
    rule_by_chat = {}
    for c in cfg.channels:
        rule_by_chat[c.id] = c
    return rule_by_chat


async def process_stream_message(
    cfg: AppCfg,
    client: TelegramClient,
    engine,
    rules: Dict[int, ChannelRule],
    payload: Dict[str, Any],
    our_user_id: int | None = None,
    profile_resolver: Optional[ProfileResolver] = None,
) -> bool:
    rid = _to_int(payload["chat_id"])
    rule = rules.get(rid)
    vip = set(rule.vip_senders) if rule else set()
    msg_id = _to_int(payload["msg_id"])

    # Resolve profiles for this channel (if using two-layer architecture)
    resolved_profile = None
    if rule and profile_resolver:
        resolved_profile = profile_resolver.resolve_for_channel(rule)

    # Use resolved keywords or fallback to legacy rule keywords
    if resolved_profile:
        keywords = resolved_profile.keywords
        action_keywords = resolved_profile.action_keywords
        decision_keywords = resolved_profile.decision_keywords
        urgency_keywords = resolved_profile.urgency_keywords
        importance_keywords = resolved_profile.importance_keywords
        release_keywords = resolved_profile.release_keywords
        security_keywords = resolved_profile.security_keywords
        risk_keywords = resolved_profile.risk_keywords
        opportunity_keywords = resolved_profile.opportunity_keywords
        detect_codes = resolved_profile.detect_codes
        detect_documents = resolved_profile.detect_documents
        prioritize_pinned = resolved_profile.prioritize_pinned
        prioritize_admin = resolved_profile.prioritize_admin
        detect_polls = resolved_profile.detect_polls
    else:
        # Fallback to legacy rule keywords (backward compatibility)
        keywords = rule.keywords if rule else []
        action_keywords = rule.action_keywords if rule else None
        decision_keywords = rule.decision_keywords if rule else None
        urgency_keywords = rule.urgency_keywords if rule else None
        importance_keywords = rule.importance_keywords if rule else None
        release_keywords = rule.release_keywords if rule else None
        security_keywords = rule.security_keywords if rule else None
        risk_keywords = rule.risk_keywords if rule else None
        opportunity_keywords = rule.opportunity_keywords if rule else None
        detect_codes = rule.detect_codes if rule else True
        detect_documents = rule.detect_documents if rule else True
        prioritize_pinned = rule.prioritize_pinned if rule else True
        prioritize_admin = rule.prioritize_admin if rule else True
        detect_polls = rule.detect_polls if rule else True

    # Detect if this is a private chat (positive ID) or reply to current user
    is_private = rid > 0
    is_reply_to_user = False

    # Check if this is a reply to one of our messages
    if payload.get("is_reply") and payload.get("reply_to_msg_id") and our_user_id:
        try:
            # Fetch the replied-to message to check if it was sent by us
            reply_to_msg_id = _to_int(payload.get("reply_to_msg_id"))
            replied_msg = await client.get_messages(rid, ids=reply_to_msg_id)

            # Handle both single message and list response
            if isinstance(replied_msg, list):
                replied_msg = replied_msg[0] if replied_msg else None

            if replied_msg:
                # Check if the replied-to message was sent by us (using cached our_user_id)
                replied_sender_id = getattr(replied_msg, "sender_id", None) or getattr(
                    getattr(replied_msg, "sender", None), "id", None
                )

                if replied_sender_id == our_user_id:
                    is_reply_to_user = True

        except Exception as e:
            log.warning(
                "Failed to fetch replied-to message for chat %s, msg %s: %s. "
                "Falling back to heuristic.",
                rid,
                msg_id,
                e,
            )
            # Fall back to simplified heuristic: in groups/channels, reply + mention often means reply to us
            if not is_private and payload.get("mentioned"):
                is_reply_to_user = True

    # Detect if sender is admin (would need chat member info, simplified for now)
    sender_is_admin = False  # Could be enhanced with chat.get_permissions() check

    hr = run_heuristics(
        text=str(payload["text"]),
        sender_id=_to_int(payload.get("sender_id"), 0),
        mentioned=bool(payload.get("mentioned", False)),
        reactions=_to_int(payload.get("reactions", 0)),
        replies=_to_int(payload.get("replies", 0)),
        vip=vip,
        keywords=keywords,
        react_thr=(rule.reaction_threshold if rule else 0),
        reply_thr=(rule.reply_threshold if rule else 0),
        # Enhanced metadata
        is_private=is_private,
        is_reply_to_user=is_reply_to_user,
        has_media=bool(payload.get("has_media", False)),
        media_type=payload.get("media_type"),
        is_pinned=bool(payload.get("is_pinned", False)),
        is_poll=payload.get("media_type") == "MessageMediaPoll",
        sender_is_admin=sender_is_admin,
        has_forward=bool(payload.get("has_forward", False)),
        # Category-specific keywords (from resolved profile or legacy rule)
        action_keywords=action_keywords,
        decision_keywords=decision_keywords,
        urgency_keywords=urgency_keywords,
        importance_keywords=importance_keywords,
        release_keywords=release_keywords,
        security_keywords=security_keywords,
        risk_keywords=risk_keywords,
        opportunity_keywords=opportunity_keywords,
        # Detection flags (from resolved profile or legacy rule)
        detect_codes=detect_codes,
        detect_documents=detect_documents,
        prioritize_pinned=prioritize_pinned,
        prioritize_admin=prioritize_admin,
        detect_polls=detect_polls,
    )

    score = hr.pre_score
    sem = score_text(str(payload["text"]))
    if sem is not None:
        score += sem

    chat_title = str(payload.get("chat_title", ""))
    sender_name = str(payload.get("sender_name", ""))
    message_text = str(payload.get("text", ""))
    triggers = ", ".join(hr.reasons) if hr.reasons else ""
    sender_id = _to_int(payload.get("sender_id"), 0)

    # Serialize trigger_annotations to JSON for storage
    trigger_annotations_json = (
        json.dumps(hr.trigger_annotations) if hr.trigger_annotations else ""
    )

    upsert_message(
        engine,
        rid,
        msg_id,
        hr.content_hash,
        score,
        chat_title,
        sender_name,
        message_text,
        triggers,
        sender_id,
        trigger_annotations_json,
    )

    important = hr.important or (sem is not None and sem >= cfg.similarity_threshold)
    if important:
        title = chat_title or f"chat {rid}"
        preview = str(payload["text"] or "").strip().replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "…"
        if cfg.alerts.mode in ("dm", "both"):
            await notify_dm(client, title, preview)
        if cfg.alerts.mode in ("channel", "both") and cfg.alerts.target_channel:
            await notify_channel(client, cfg.alerts.target_channel, title, preview)
        mark_alerted(engine, rid, msg_id)
        inc("alerts_total", chat=rid)

    return important


async def process_loop(
    cfg: AppCfg,
    client: TelegramClient,
    engine,
    handshake_gate: Optional[asyncio.Event] = None,
):
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)
    stream = cfg.redis["stream"]
    group = cfg.redis["group"]
    consumer = cfg.redis["consumer"]

    async def _wait_ready():
        """Wait for handshake gate to be set before processing.

        This gate is used for pause/resume semantics during re-login operations.
        When a re-login handshake is initiated, the gate is cleared to pause
        message processing, then set again once the handshake completes.

        This check must remain in the main loop to support dynamic pausing.
        When the gate is set, wait() returns immediately with no blocking overhead.
        """
        if handshake_gate is None:
            return
        await handshake_gate.wait()

    try:
        r.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception:
        pass  # already exists

    rules = load_rules(cfg)
    load_interests(cfg.interests)

    # Initialize ProfileResolver with global profiles (two-layer architecture)
    profile_resolver = (
        ProfileResolver(cfg.global_profiles) if cfg.global_profiles else None
    )
    if profile_resolver:
        log.info(
            f"ProfileResolver initialized with {len(cfg.global_profiles)} global profiles"
        )
    else:
        log.info(
            "ProfileResolver not initialized (no global profiles found, using legacy keywords)"
        )

    # Cache our user ID once at startup to avoid calling get_me() per message
    our_user_id: int | None = None
    try:
        me = await client.get_me()  # type: ignore[misc]
        our_user_id = getattr(me, "id", None)
        if our_user_id:
            log.debug("Cached our user ID: %s", our_user_id)
        else:
            log.warning("Could not determine our user ID")
    except Exception as e:
        log.warning("Failed to fetch our user ID at startup: %s", e)

    reload_marker = Path("/app/data/.reload_config")
    last_cfg_check = 0
    cfg_check_interval = 5  # Check every 5 seconds

    while True:
        # Pause message processing during re-login handshakes (gate cleared/set dynamically)
        await _wait_ready()
        # Check for config reload marker periodically
        current_time = asyncio.get_event_loop().time()
        if current_time - last_cfg_check > cfg_check_interval:
            last_cfg_check = current_time
            if reload_marker.exists():
                try:
                    log.info("Config reload requested, reloading configuration...")
                    new_cfg = load_config()
                    cfg = new_cfg
                    rules = load_rules(cfg)
                    load_interests(cfg.interests)
                    # Reinitialize ProfileResolver with new global profiles
                    profile_resolver = (
                        ProfileResolver(cfg.global_profiles)
                        if cfg.global_profiles
                        else None
                    )
                    if profile_resolver:
                        log.info(
                            f"ProfileResolver reinitialized with {len(cfg.global_profiles)} global profiles"
                        )
                    # Reconnect Telegram client to pick up a newly authenticated session
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    try:
                        await client.connect()  # type: ignore[misc]
                        # Ensure authorization; start() will use existing session without interaction
                        try:
                            is_auth = await client.is_user_authorized()  # type: ignore[misc]
                        except Exception:
                            is_auth = False
                        if not is_auth:
                            try:
                                await client.start()  # type: ignore[misc]
                            except Exception as start_err:
                                log.warning(
                                    "Client start after reload failed: %s", start_err
                                )
                        # Refresh user info + avatar for UI
                        try:
                            me = await client.get_me()  # type: ignore[misc]
                            # Update cached our_user_id after reconnect
                            our_user_id = getattr(me, "id", None)
                            if our_user_id:
                                log.debug(
                                    "Refreshed our_user_id after reconnect: %s",
                                    our_user_id,
                                )
                            else:
                                log.warning(
                                    "Could not determine our_user_id after reconnect"
                                )
                            # Download user avatar if available and store in Redis
                            avatar_url = "/static/images/logo.png"
                            try:
                                photos = await client.get_profile_photos("me", limit=1)  # type: ignore[misc]
                                if photos:
                                    # Download avatar to memory instead of disk
                                    avatar_bytes = io.BytesIO()
                                    try:
                                        await client.download_profile_photo("me", file=avatar_bytes)  # type: ignore[misc]
                                        avatar_bytes.seek(0)
                                        avatar_data = avatar_bytes.read()
                                        if not avatar_data:
                                            log.debug(
                                                "Avatar download returned empty data"
                                            )
                                            continue

                                        avatar_b64 = base64.b64encode(
                                            avatar_data
                                        ).decode("utf-8")

                                        # Store in Redis with user_id key
                                        if our_user_id and r:
                                            redis_key = (
                                                f"tgsentinel:user_avatar:{our_user_id}"
                                            )
                                            r.set(
                                                redis_key, avatar_b64, ex=3600
                                            )  # 1 hour TTL
                                            avatar_url = (
                                                f"/api/avatar/user/{our_user_id}"
                                            )
                                            log.info(
                                                f"Stored user avatar in Redis: {redis_key}"
                                            )
                                    except Exception as avatar_dl_err:
                                        log.debug(
                                            "Could not download user avatar: %s",
                                            avatar_dl_err,
                                        )
                            except Exception as avatar_err:
                                log.debug(
                                    "Could not refresh user avatar: %s", avatar_err
                                )
                            ui = {
                                "username": getattr(me, "username", None)
                                or getattr(me, "first_name", "Unknown"),
                                "first_name": getattr(me, "first_name", ""),
                                "last_name": getattr(me, "last_name", ""),
                                "phone": getattr(me, "phone", ""),
                                "user_id": getattr(me, "id", None),
                                "avatar": avatar_url,
                            }
                            r.set("tgsentinel:user_info", json.dumps(ui))
                        except Exception as me_err:
                            log.debug(
                                "Could not refresh user info after reload: %s", me_err
                            )
                    except Exception as conn_err:
                        log.error("Client reconnect after reload failed: %s", conn_err)
                    reload_marker.unlink()
                    log.info(
                        "Configuration reloaded successfully with %d channels",
                        len(cfg.channels),
                    )
                    for ch in cfg.channels:
                        log.info("  • %s (id: %d)", ch.name, ch.id)
                except Exception as reload_exc:
                    log.error("Failed to reload configuration: %s", reload_exc)
                    # Remove marker even on failure to prevent infinite retry
                    try:
                        reload_marker.unlink()
                    except Exception:
                        pass

        resp = cast(
            StreamResponse,
            r.xreadgroup(group, consumer, streams={stream: ">"}, count=50, block=5000),
        )
        if not resp:
            await asyncio.sleep(0.1)
            continue
        for _, messages in resp:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields["json"])
                    important = await process_stream_message(
                        cfg,
                        client,
                        engine,
                        rules,
                        payload,
                        our_user_id,
                        profile_resolver,
                    )
                    r.xack(stream, group, msg_id)
                    inc("processed_total", important=important)
                except Exception as e:
                    inc("errors_total")
                    log.exception("worker_error: %s", e)
                    # do not ack; will be retried
