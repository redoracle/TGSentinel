"""Delivery orchestration for alerts and interests.

This module decouples notification delivery from scoring/evaluation, implementing the
"Delivery" axis of the three-axis taxonomy (Target Entity × Semantic Type × Delivery).

Handles four delivery modes:
- NONE: Save to Telegram Saved Messages only
- DM: Immediate Telegram DM/mention
- DIGEST: Batched delivery via DigestWorker
- BOTH: Immediate DM + later digest inclusion

Related architectural constraints:
- Constraint 1 (Dual-Service Separation): UI never calls notifier directly; all via Sentinel
- Constraint 2 (Concurrency): Async functions for all I/O (Telegram API, webhook HTTP)
- Constraint 4 (Structured Logging): Logs include semantic_type, delivery_mode, target_entity
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class DeliveryPayload:
    """Payload for delivery orchestration.

    Attributes:
        semantic_type: 'alert_keyword' or 'interest_semantic'
        delivery_mode: 'none', 'dm', 'digest', or 'both'
        delivery_target: Telegram handle/channel for DM/digest
        message_text: Content to deliver
        chat_title: Source chat/channel
        sender_name: Original sender
        chat_id: Telegram chat ID
        msg_id: Telegram message ID
        score: Keyword score (for alerts) or max semantic score (for interests)
        matched_profiles: List of profile IDs that triggered
        trigger_annotations: Additional metadata as JSON dict
        request_id: Optional correlation ID for logging
    """

    semantic_type: str
    delivery_mode: str
    delivery_target: Optional[str]
    message_text: str
    chat_title: str
    sender_name: str
    chat_id: int
    msg_id: int
    score: float
    matched_profiles: List[str]
    trigger_annotations: Dict[str, Any]
    request_id: Optional[str] = None


async def orchestrate_delivery(
    payload: DeliveryPayload,
    client: Any,  # TelegramClient
    notifier: Any,  # Notifier instance with notify_dm, save_to_telegram, etc.
    webhooks_cfg: Optional[Any] = None,
) -> Dict[str, Any]:
    """Orchestrate message delivery based on semantic type and delivery mode.

    This is the single entry point for all delivery decisions, ensuring orthogonality
    between scoring (alerts_evaluator/interests_evaluator) and delivery.

    Args:
        payload: Delivery payload with semantic type, mode, target, and message details
        client: Telethon TelegramClient for API calls
        notifier: Notifier instance with delivery methods
        webhooks_cfg: Optional webhook configuration

    Returns:
        Dict with delivery status:
        {
            "delivery_mode_used": str,
            "delivery_target_used": Optional[str],
            "dm_sent": bool,
            "saved_to_messages": bool,
            "webhook_sent": bool,
            "digest_queued": bool,
            "errors": List[str]
        }

    Logs:
        - [DELIVERY-ORCHESTRATOR] INFO: Delivery outcomes per mode
        - [DELIVERY-ORCHESTRATOR] ERROR: Delivery failures
    """
    extra = {
        "request_id": payload.request_id,
        "semantic_type": payload.semantic_type,
        "delivery_mode": payload.delivery_mode,
        "target_entity": "feed",
    }

    result = {
        "delivery_mode_used": payload.delivery_mode,
        "delivery_target_used": payload.delivery_target,
        "dm_sent": False,
        "saved_to_messages": False,
        "webhook_sent": False,
        "digest_queued": False,
        "errors": [],
    }

    log.info(
        "[DELIVERY-ORCHESTRATOR] Starting delivery: mode=%s, semantic_type=%s, chat_id=%s, msg_id=%s",
        payload.delivery_mode,
        payload.semantic_type,
        payload.chat_id,
        payload.msg_id,
        extra=extra,
    )

    # Normalize delivery mode
    mode = payload.delivery_mode.lower()

    # Execute delivery based on mode
    if mode == "none":
        # NONE: Save to Telegram Saved Messages only
        await _deliver_saved_messages(payload, client, notifier, result)

    elif mode == "dm":
        # DM: Immediate Telegram DM/mention
        await _deliver_dm(payload, client, notifier, result)
        await _deliver_webhooks(payload, notifier, webhooks_cfg, result)

    elif mode == "digest":
        # DIGEST: Queue for batched delivery (handled by DigestWorker)
        result["digest_queued"] = True
        log.info(
            "[DELIVERY-ORCHESTRATOR] Queued for digest: semantic_type=%s, profiles=%s",
            payload.semantic_type,
            payload.matched_profiles,
            extra=extra,
        )

    elif mode == "both":
        # BOTH: DM now + digest later
        await _deliver_dm(payload, client, notifier, result)
        await _deliver_webhooks(payload, notifier, webhooks_cfg, result)
        result["digest_queued"] = True
        log.info(
            "[DELIVERY-ORCHESTRATOR] Delivered DM + queued for digest",
            extra=extra,
        )

    else:
        error_msg = f"Unknown delivery mode: {payload.delivery_mode}"
        result["errors"].append(error_msg)
        log.error(
            "[DELIVERY-ORCHESTRATOR] %s",
            error_msg,
            extra=extra,
        )

    log.info(
        "[DELIVERY-ORCHESTRATOR] Delivery complete: mode=%s, dm_sent=%s, saved=%s, webhook=%s, digest=%s, errors=%d",
        payload.delivery_mode,
        result["dm_sent"],
        result["saved_to_messages"],
        result["webhook_sent"],
        result["digest_queued"],
        len(result["errors"]),
        extra=extra,
    )

    return result


async def _deliver_saved_messages(
    payload: DeliveryPayload,
    client: Any,
    notifier: Any,
    result: Dict[str, Any],
) -> None:
    """Deliver to Telegram Saved Messages."""
    try:
        if hasattr(notifier, "save_to_telegram"):
            await notifier.save_to_telegram(
                client=client,
                message_text=payload.message_text,
                chat_title=payload.chat_title,
                sender_name=payload.sender_name,
                score=payload.score,
            )
            result["saved_to_messages"] = True
            log.info(
                "[DELIVERY-ORCHESTRATOR] Saved to Telegram Saved Messages",
                extra={"semantic_type": payload.semantic_type},
            )
    except Exception as exc:
        error_msg = f"Failed to save to Saved Messages: {exc}"
        result["errors"].append(error_msg)
        log.error(
            "[DELIVERY-ORCHESTRATOR] %s",
            error_msg,
            exc_info=True,
            extra={"semantic_type": payload.semantic_type},
        )


async def _deliver_dm(
    payload: DeliveryPayload,
    client: Any,
    notifier: Any,
    result: Dict[str, Any],
) -> None:
    """Deliver via Telegram DM/mention."""
    try:
        if hasattr(notifier, "notify_dm"):
            target = payload.delivery_target or "me"  # Default to self
            await notifier.notify_dm(
                client=client,
                title=payload.chat_title,  # title parameter
                text=payload.message_text,  # text parameter
                target=target,
                sender_name=payload.sender_name,
                score=payload.score,
            )
            result["dm_sent"] = True
            log.info(
                "[DELIVERY-ORCHESTRATOR] Sent DM to %s",
                target,
                extra={"semantic_type": payload.semantic_type},
            )
    except Exception as exc:
        error_msg = f"Failed to send DM: {exc}"
        result["errors"].append(error_msg)
        log.error(
            "[DELIVERY-ORCHESTRATOR] %s",
            error_msg,
            exc_info=True,
            extra={"semantic_type": payload.semantic_type},
        )


async def _deliver_webhooks(
    payload: DeliveryPayload,
    notifier: Any,
    webhooks_cfg: Optional[Any],
    result: Dict[str, Any],
) -> None:
    """Deliver via webhooks (if configured)."""
    if not webhooks_cfg or not hasattr(notifier, "notify_webhook"):
        return

    try:
        # Build webhook payload
        webhook_payload = {
            "semantic_type": payload.semantic_type,
            "delivery_mode": payload.delivery_mode,
            "chat_id": payload.chat_id,
            "msg_id": payload.msg_id,
            "message_text": payload.message_text,
            "chat_title": payload.chat_title,
            "sender_name": payload.sender_name,
            "score": payload.score,
            "matched_profiles": payload.matched_profiles,
            "trigger_annotations": payload.trigger_annotations,
        }

        await notifier.notify_webhook(
            webhooks_cfg=webhooks_cfg,
            payload=webhook_payload,
            profile_ids=payload.matched_profiles,
        )
        result["webhook_sent"] = True
        log.info(
            "[DELIVERY-ORCHESTRATOR] Sent webhook notifications",
            extra={"semantic_type": payload.semantic_type},
        )
    except Exception as exc:
        error_msg = f"Failed to send webhook: {exc}"
        result["errors"].append(error_msg)
        log.error(
            "[DELIVERY-ORCHESTRATOR] %s",
            error_msg,
            exc_info=True,
            extra={"semantic_type": payload.semantic_type},
        )
