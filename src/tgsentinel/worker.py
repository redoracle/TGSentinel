import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from redis import Redis
from telethon import TelegramClient

from .config import (
    AppCfg,
    ChannelRule,
    DigestSchedule,
    ProfileDigestConfig,
    load_config,
)
from .heuristics import run_heuristics
from .metrics import inc
from .notifier import notify_channel, notify_dm, notify_webhook
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


def get_primary_digest_schedule(
    digest_config: Optional[ProfileDigestConfig],
) -> str:
    """Get the primary (most frequent) digest schedule from config.

    Returns the schedule with highest frequency for digest assignment.
    Priority order: hourly > every_4h > every_6h > every_12h > daily > weekly > none

    Args:
        digest_config: Resolved digest configuration (may be None)

    Returns:
        Schedule name as string (e.g., "hourly", "daily") or empty string if no digest
    """
    if not digest_config or not digest_config.schedules:
        return ""  # No digest config or no schedules

    # Priority order (most frequent first)
    priority = [
        DigestSchedule.HOURLY,
        DigestSchedule.EVERY_4H,
        DigestSchedule.EVERY_6H,
        DigestSchedule.EVERY_12H,
        DigestSchedule.DAILY,
        DigestSchedule.WEEKLY,
        DigestSchedule.NONE,
    ]

    # Find first matching enabled schedule in priority order
    enabled_schedules = {s.schedule for s in digest_config.schedules if s.enabled}

    for sched in priority:
        if sched in enabled_schedules:
            return sched.value

    # If no enabled schedules found, return empty (instant alerts only)
    return ""


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
    log.info("[WORKER] process_stream_message: chat_id=%s, checking rules...", rid)
    rule = rules.get(rid)
    log.info("[WORKER] Rule lookup result: rule=%s", "found" if rule else "not found")

    # Check if this is a monitored user (DM) without explicit channel rule
    # If so, create a default rule to allow persistence
    is_monitored_user = False
    if not rule and rid > 0:  # Positive IDs are users/DMs
        log.info("[WORKER] No rule found, checking monitored_users for chat_id=%s", rid)
        # Check if this user is in monitored_users list
        monitored_user_ids = {user.id for user in cfg.monitored_users if user.enabled}
        log.info(
            "[WORKER] Monitored user IDs: %s", list(monitored_user_ids)[:10]
        )  # Log first 10
        if rid in monitored_user_ids:
            is_monitored_user = True
            log.info(
                "[WORKER] chat_id=%s IS in monitored_users, creating default rule", rid
            )
            # Create a default rule for monitored users (allows persistence with minimal scoring)
            rule = ChannelRule(
                id=rid,
                name=payload.get("chat_title", f"User {rid}"),
                vip_senders=[],
                keywords=[],
                action_keywords=[],
                decision_keywords=[],
                urgency_keywords=[],
                importance_keywords=[],
                release_keywords=[],
                security_keywords=[],
                risk_keywords=[],
                opportunity_keywords=[],
                detect_codes=False,
                detect_documents=False,
                prioritize_pinned=False,
                prioritize_admin=False,
                detect_polls=False,
                reaction_threshold=5,
                reply_threshold=3,
                rate_limit_per_hour=10,
            )
            log.info(
                "[WORKER] Created default rule for monitored user %s (%s)",
                rid,
                rule.name,
            )

    # Check if global profiles apply to this entity (even if no explicit rule)
    # This enables the "empty bindings = apply to all" feature
    has_global_profiles = False
    if not rule and profile_resolver:
        entity_type = "user" if rid > 0 else "channel"
        has_global_profiles = profile_resolver.has_applicable_profiles(entity_type, rid)
        if has_global_profiles:
            log.info(
                "[WORKER] No explicit rule for %s %s, but global profiles apply (empty bindings = all). Creating default rule.",
                entity_type,
                rid,
            )
            # Create a minimal rule to allow profile resolution
            rule = ChannelRule(
                id=rid,
                name=payload.get("chat_title", f"Chat {rid}"),
                vip_senders=[],
                keywords=[],
                action_keywords=[],
                decision_keywords=[],
                urgency_keywords=[],
                importance_keywords=[],
                release_keywords=[],
                security_keywords=[],
                risk_keywords=[],
                opportunity_keywords=[],
                detect_codes=False,
                detect_documents=False,
                prioritize_pinned=False,
                prioritize_admin=False,
                detect_polls=False,
                reaction_threshold=5,
                reply_threshold=3,
                rate_limit_per_hour=10,
                profiles=[],  # Will be auto-populated by profile resolver
            )

    # Early exit: Skip processing only if no rule AND no applicable global profiles
    if not rule:
        log.info(
            "[WORKER] Skipping message from unmonitored chat %s (no rule configured, not a monitored user, no applicable global profiles)",
            rid,
        )
        return False

    vip = set(rule.vip_senders)
    msg_id = _to_int(payload["msg_id"])
    sender_id = _to_int(payload.get("sender_id"), 0)

    # Resolve profiles for this channel (if using two-layer architecture)
    resolved_profile = None
    if profile_resolver:
        resolved_profile = profile_resolver.resolve_for_channel(rule)

    # Check if sender is in excluded_users list (blacklist)
    excluded_users_set = set()
    if resolved_profile:
        excluded_users_set = set(resolved_profile.excluded_users)
    else:
        excluded_users_set = set(rule.excluded_users)

    if sender_id in excluded_users_set:
        log.info(
            "[WORKER] Skipping message from excluded user %s in chat %s (blacklisted)",
            sender_id,
            rid,
        )
        return False

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
        detect_links = resolved_profile.detect_links
        require_forwarded = resolved_profile.require_forwarded
        prioritize_pinned = resolved_profile.prioritize_pinned
        prioritize_admin = resolved_profile.prioritize_admin
        detect_polls = resolved_profile.detect_polls
    else:
        # Fallback to legacy rule keywords (backward compatibility)
        # Note: rule is guaranteed to exist at this point due to early exit above
        keywords = rule.keywords
        action_keywords = rule.action_keywords
        decision_keywords = rule.decision_keywords
        urgency_keywords = rule.urgency_keywords
        importance_keywords = rule.importance_keywords
        release_keywords = rule.release_keywords
        security_keywords = rule.security_keywords
        risk_keywords = rule.risk_keywords
        opportunity_keywords = rule.opportunity_keywords
        detect_codes = rule.detect_codes
        detect_documents = rule.detect_documents
        detect_links = rule.detect_links
        require_forwarded = rule.require_forwarded
        prioritize_pinned = rule.prioritize_pinned
        prioritize_admin = rule.prioritize_admin
        detect_polls = rule.detect_polls

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
        react_thr=rule.reaction_threshold,
        reply_thr=rule.reply_threshold,
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
        detect_links=detect_links,
        require_forwarded=require_forwarded,
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

    # Prepare matched_profiles (Phase 2: for digest deduplication)
    matched_profiles_json = ""
    digest_schedule = ""
    if resolved_profile:
        matched_profiles_json = (
            json.dumps(resolved_profile.matched_profile_ids)
            if resolved_profile.matched_profile_ids
            else ""
        )
        digest_schedule = get_primary_digest_schedule(resolved_profile.digest)

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
        matched_profiles_json,  # Phase 2: JSON array of profile IDs
        digest_schedule,  # Phase 2: Primary schedule for this message
    )

    # Check if message meets minimum score threshold for instant alerts
    # hr.important indicates if any heuristics triggered, but we must also
    # check that the final score meets the configured min_score threshold
    meets_threshold = score >= cfg.alerts.min_score
    has_semantic_match = sem is not None and sem >= cfg.similarity_threshold

    # CRITICAL: Only alert if THIS SPECIFIC channel/profile has keywords configured
    # Check if this channel has keywords (legacy rule) OR bound profiles with keywords
    has_keywords_for_this_channel = False
    if resolved_profile:
        # Check if resolved profile has ANY keywords configured
        has_keywords_for_this_channel = any(
            [
                resolved_profile.keywords,
                resolved_profile.action_keywords,
                resolved_profile.decision_keywords,
                resolved_profile.urgency_keywords,
                resolved_profile.importance_keywords,
                resolved_profile.release_keywords,
                resolved_profile.security_keywords,
                resolved_profile.risk_keywords,
                resolved_profile.opportunity_keywords,
            ]
        )
    elif rule.keywords or rule.profiles:
        # Legacy rule has keywords or profiles bound
        has_keywords_for_this_channel = True

    important = (
        has_keywords_for_this_channel
        and meets_threshold
        and (hr.important or has_semantic_match)
    )

    # Debug logging for alert decision
    if important or score > 0.5:  # Log all potential alerts
        log.info(
            f"[WORKER] Alert decision for chat={rid}, msg={msg_id}: "
            f"score={score:.2f}, threshold={cfg.alerts.min_score:.2f}, "
            f"has_keywords={has_keywords_for_this_channel}, "
            f"hr.important={hr.important}, semantic={has_semantic_match}, "
            f"alert={important}, triggers={triggers}"
        )

    if important:
        title = chat_title or f"chat {rid}"
        preview = str(payload["text"] or "").strip().replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "…"

        # Send Telegram notifications
        if cfg.alerts.mode in ("dm", "both"):
            await notify_dm(client, title, preview)
        if cfg.alerts.mode in ("channel", "both") and cfg.alerts.target_channel:
            await notify_channel(client, cfg.alerts.target_channel, title, preview)

        # Send webhook notifications if profile has webhooks configured
        webhook_services = []
        if resolved_profile and hasattr(resolved_profile, "webhooks"):
            webhook_services = resolved_profile.webhooks or []
        elif rule and hasattr(rule, "webhooks"):
            webhook_services = rule.webhooks or []

        if webhook_services:
            # Build n8n-compatible JSON payload
            webhook_payload = {
                "event": "alert_triggered",
                "timestamp": payload.get("created_at", ""),
                "profile_id": resolved_profile.id if resolved_profile else None,
                "profile_name": (
                    resolved_profile.name
                    if resolved_profile
                    else rule.name if rule else "Unknown"
                ),
                "chat_id": rid,
                "chat_name": chat_title or "",
                "message_id": msg_id,
                "sender_id": sender_id,
                "sender_name": sender_name or "",
                "message_text": message_text or "",
                "score": float(score),
                "triggers": triggers or "",
                "matched_profiles": (
                    resolved_profile.matched_profile_ids if resolved_profile else []
                ),
            }

            try:
                webhook_results = await notify_webhook(
                    webhook_services,
                    webhook_payload,
                    db_engine=engine,  # Pass engine for delivery history tracking
                )

                # Defensive validation: ensure webhook_results is a dict with expected structure
                if not isinstance(webhook_results, dict):
                    log.error(
                        f"[WORKER] notify_webhook returned invalid type ({type(webhook_results).__name__}) "
                        f"for chat={rid}, msg={msg_id}, treating as failed delivery"
                    )
                    webhook_results = {"success": [], "failed": webhook_services or []}

                # Safely extract success/failed with defaults
                success_results = webhook_results.get("success", [])
                failed_results = webhook_results.get("failed", [])

                # Ensure failed_results is iterable (convert non-list to list)
                if not isinstance(failed_results, (list, tuple)):
                    log.warning(
                        f"[WORKER] webhook_results['failed'] is not iterable ({type(failed_results).__name__}), "
                        f"converting to list"
                    )
                    failed_results = [failed_results] if failed_results else []

                # Log results using safe values
                if success_results:
                    log.info(
                        f"[WORKER] Webhooks delivered for chat={rid}, msg={msg_id}: "
                        f"success={success_results}"
                    )
                if failed_results:
                    log.warning(
                        f"[WORKER] Webhook delivery failed for chat={rid}, msg={msg_id}: "
                        f"failed={failed_results}"
                    )
            except Exception as webhook_exc:
                log.error(
                    f"[WORKER] Webhook delivery exception for chat={rid}, msg={msg_id}: {webhook_exc}",
                    exc_info=True,
                )

        mark_alerted(engine, rid, msg_id)
        inc("alerts_total", chat=rid)

    return important


async def process_loop(
    cfg: AppCfg,
    client: TelegramClient,
    engine,
    handshake_gate: Optional[asyncio.Event] = None,
):
    log.info("[WORKER] process_loop started - entering main message processing loop")
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)
    stream = cfg.redis["stream"]
    group = cfg.redis["group"]
    consumer = cfg.redis["consumer"]

    log.info(
        "[WORKER] Redis config: stream=%s, group=%s, consumer=%s",
        stream,
        group,
        consumer,
    )

    async def _wait_ready():
        """Wait for handshake gate to be set before processing.

        This gate is used for pause/resume semantics during re-login operations.
        When a re-login handshake is initiated, the gate is cleared to pause
        message processing, then set again once the handshake completes.

        This check must remain in the main loop to support dynamic pausing.
        When the gate is set, wait() returns immediately with no blocking overhead.
        """
        if handshake_gate is None:
            log.debug("[WORKER] No handshake gate, proceeding immediately")
            return
        log.debug("[WORKER] Checking handshake gate...")
        await handshake_gate.wait()
        log.debug("[WORKER] Handshake gate passed, ready to process")

    try:
        r.xgroup_create(stream, group, id="$", mkstream=True)
        log.info("[WORKER] Created consumer group '%s' for stream '%s'", group, stream)
    except Exception as e:
        log.debug("[WORKER] Consumer group already exists (expected): %s", e)

    log.info("[WORKER] Loading rules and interests for %d channels", len(cfg.channels))
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

    log.info(
        "[WORKER] Entering infinite message processing loop (stream=%s, group=%s, consumer=%s)",
        stream,
        group,
        consumer,
    )
    loop_iteration = 0
    while True:
        loop_iteration += 1
        if loop_iteration % 100 == 1:  # Log every 100 iterations to avoid spam
            log.debug(
                "[WORKER] Loop iteration %d - waiting for messages...", loop_iteration
            )

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
            if loop_iteration % 100 == 1:
                log.debug("[WORKER] No new messages in stream, sleeping briefly...")
            await asyncio.sleep(0.1)
            continue

        log.info(
            "[WORKER] Received %d messages from stream, processing...",
            sum(len(msgs) for _, msgs in resp),
        )
        for _, messages in resp:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields["json"])
                    chat_id = payload.get("chat_id", "unknown")
                    msg_num = payload.get("msg_id", "unknown")
                    log.debug(
                        "[WORKER] Processing message: stream_id=%s, chat_id=%s, msg_id=%s",
                        msg_id,
                        chat_id,
                        msg_num,
                    )

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
                    log.debug(
                        "[WORKER] ✓ Message processed: chat_id=%s, msg_id=%s, important=%s",
                        chat_id,
                        msg_num,
                        important,
                    )
                except Exception as e:
                    inc("errors_total")
                    log.exception("worker_error: %s", e)
                    # do not ack; will be retried
