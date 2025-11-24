import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml
from sqlalchemy import Engine
from telethon import TelegramClient

# Import aiohttp only for type checking to avoid dependency issues
if TYPE_CHECKING:
    import aiohttp

log = logging.getLogger(__name__)


async def notify_dm(client: TelegramClient, title: str, text: str):
    await client.send_message("me", f"🔔 {title}\n{text}")


async def notify_channel(client: TelegramClient, channel: str, title: str, text: str):
    await client.send_message(channel, f"🔔 {title}\n{text}")


async def notify_webhook(
    webhook_services: List[str],
    payload: Dict[str, Any],
    webhook_config_path: str = "config/webhooks.yml",
    db_engine: Optional[Engine] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Send notifications to configured webhooks with retry logic.

    Args:
        webhook_services: List of webhook service names to notify (e.g., ["slack", "pagerduty"])
        payload: Message data to send (JSON-serializable dict)
        webhook_config_path: Path to webhooks.yml configuration file
        db_engine: SQLAlchemy engine for recording delivery history (optional)
        dry_run: If True, log payload without sending (for backtest mode)

    Returns:
        Dictionary with delivery results: {"success": [...], "failed": [...]}

    Payload format (n8n-compatible JSON):
        {
            "event": "alert_triggered",
            "timestamp": "2024-11-24T12:00:00Z",
            "profile_id": 1001,
            "profile_name": "Security Alerts",
            "chat_id": 123456,
            "chat_name": "Security Channel",
            "message_id": 789,
            "sender_id": 111111,
            "sender_name": "Alice",
            "message_text": "Urgent security update...",
            "score": 8.5,
            "triggers": "security_keywords",
            "matched_profiles": ["security", "urgent"]
        }
    """
    if not webhook_services:
        log.debug("[WEBHOOK] No webhooks specified, skipping notification")
        return {"success": [], "failed": []}

    # Dry-run mode: log payload without delivery
    if dry_run:
        log.info(
            f"[WEBHOOK] DRY-RUN: Would send to {webhook_services}",
            extra={"payload": payload, "services": webhook_services},
        )
        log.info(f"[WEBHOOK] DRY-RUN: Payload: {json.dumps(payload, indent=2)}")
        return {"success": webhook_services, "failed": []}

    # Load webhook configuration
    try:
        webhook_path = Path(webhook_config_path)
        if not webhook_path.exists():
            log.warning(
                f"[WEBHOOK] Config file not found: {webhook_config_path}, skipping webhook delivery"
            )
            return {"success": [], "failed": webhook_services}

        with open(webhook_path, "r") as f:
            webhook_data = yaml.safe_load(f) or {}

        webhooks = webhook_data.get("webhooks", [])
        webhook_map = {
            wh.get("service"): wh for wh in webhooks if wh.get("enabled", True)
        }

    except Exception as exc:
        log.error(f"[WEBHOOK] Failed to load webhook config: {exc}", exc_info=True)
        return {"success": [], "failed": webhook_services}

    # Deliver to each webhook
    results = {"success": [], "failed": []}

    # Import aiohttp here to avoid dependency issues if not installed
    try:
        import aiohttp
    except ImportError:
        log.error(
            "[WEBHOOK] aiohttp not installed, cannot deliver webhooks. Install with: pip install aiohttp"
        )
        return {"success": [], "failed": webhook_services}

    async with aiohttp.ClientSession() as session:
        for service_name in webhook_services:
            webhook = webhook_map.get(service_name)
            if not webhook:
                log.warning(
                    f"[WEBHOOK] Service '{service_name}' not found in {webhook_config_path}"
                )
                results["failed"].append(service_name)
                continue

            url = webhook.get("url")
            secret = webhook.get("secret", "")

            if not url:
                log.warning(f"[WEBHOOK] Service '{service_name}' has no URL configured")
                results["failed"].append(service_name)
                continue

            # Retry logic with exponential backoff (max 3 retries: 1s, 2s, 4s)
            max_attempts = 4  # 1 initial + 3 retries
            retry_delays = [0, 1, 2, 4]  # seconds

            success = False
            last_error = None
            last_http_status = None
            last_response_time_ms = None

            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    delay = retry_delays[attempt - 1]
                    log.info(
                        f"[WEBHOOK] Retrying {service_name} (attempt {attempt}/{max_attempts}) after {delay}s delay"
                    )
                    await asyncio.sleep(delay)

                try:
                    # Serialize payload once to ensure HMAC and HTTP body match
                    body = json.dumps(payload, ensure_ascii=False)

                    # Prepare headers
                    headers = {"Content-Type": "application/json"}

                    # Add HMAC signature if secret is configured
                    if secret:
                        # Decrypt secret (assume Fernet encryption, same as UI)
                        try:
                            import os

                            from cryptography.fernet import Fernet

                            webhook_key = os.getenv("WEBHOOK_SECRET_KEY")
                            if webhook_key:
                                cipher = Fernet(
                                    webhook_key.encode()
                                    if isinstance(webhook_key, str)
                                    else webhook_key
                                )
                                decrypted_secret = cipher.decrypt(
                                    secret.encode()
                                ).decode()
                            else:
                                log.warning(
                                    f"[WEBHOOK] WEBHOOK_SECRET_KEY not set, cannot decrypt secret for {service_name}"
                                )
                                decrypted_secret = (
                                    secret  # Use as-is (backward compatibility)
                                )
                        except Exception as decrypt_exc:
                            log.warning(
                                f"[WEBHOOK] Failed to decrypt secret for {service_name}: {decrypt_exc}, using raw secret"
                            )
                            decrypted_secret = secret

                        # Compute HMAC-SHA256 signature over the serialized body
                        import hmac

                        signature = hmac.new(
                            decrypted_secret.encode(), body.encode(), hashlib.sha256
                        ).hexdigest()
                        headers["X-Webhook-Signature"] = f"sha256={signature}"

                    # Send POST request with timing (use data= instead of json= to send exact body)
                    start_time = time.time()
                    async with session.post(
                        url,
                        data=body,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        last_response_time_ms = elapsed_ms
                        last_http_status = response.status

                        if response.status < 400:
                            log.info(
                                f"[WEBHOOK] Successfully delivered to {service_name} (HTTP {response.status}, {elapsed_ms}ms, attempt {attempt}/{max_attempts})"
                            )
                            success = True

                            # Record success in database
                            if db_engine:
                                from .store import record_webhook_delivery

                                try:
                                    record_webhook_delivery(
                                        engine=db_engine,
                                        webhook_service=service_name,
                                        profile_id=str(payload.get("profile_id", "")),
                                        profile_name=payload.get("profile_name", ""),
                                        chat_id=payload.get("chat_id", 0),
                                        msg_id=payload.get("message_id", 0),
                                        status="success",
                                        http_status=response.status,
                                        response_time_ms=elapsed_ms,
                                        payload=json.dumps(payload),
                                        attempt=attempt,
                                    )
                                except Exception as db_exc:
                                    log.error(
                                        f"[WEBHOOK] Failed to record delivery in DB: {db_exc}"
                                    )

                            results["success"].append(service_name)
                            break  # Exit retry loop on success

                        else:
                            response_text = await response.text()
                            last_error = f"HTTP {response.status}: {response_text}"
                            log.warning(
                                f"[WEBHOOK] Failed to deliver to {service_name}: HTTP {response.status} ({elapsed_ms}ms, attempt {attempt}/{max_attempts})"
                            )

                            # Record retry attempt in database
                            if db_engine and attempt < max_attempts:
                                from .store import record_webhook_delivery

                                try:
                                    record_webhook_delivery(
                                        engine=db_engine,
                                        webhook_service=service_name,
                                        profile_id=str(payload.get("profile_id", "")),
                                        profile_name=payload.get("profile_name", ""),
                                        chat_id=payload.get("chat_id", 0),
                                        msg_id=payload.get("message_id", 0),
                                        status=f"retry_{attempt}",
                                        http_status=response.status,
                                        response_time_ms=elapsed_ms,
                                        error_message=last_error,
                                        payload=json.dumps(payload),
                                        attempt=attempt,
                                    )
                                except Exception as db_exc:
                                    log.error(
                                        f"[WEBHOOK] Failed to record retry in DB: {db_exc}"
                                    )

                except aiohttp.ClientError as http_exc:
                    elapsed_ms = (
                        int((time.time() - start_time) * 1000)
                        if "start_time" in locals()
                        else 0
                    )
                    last_error = f"HTTP error: {http_exc}"
                    last_response_time_ms = elapsed_ms
                    log.warning(
                        f"[WEBHOOK] HTTP error delivering to {service_name} ({elapsed_ms}ms, attempt {attempt}/{max_attempts}): {http_exc}"
                    )

                    # Record retry attempt in database
                    if db_engine and attempt < max_attempts:
                        from .store import record_webhook_delivery

                        try:
                            record_webhook_delivery(
                                engine=db_engine,
                                webhook_service=service_name,
                                profile_id=str(payload.get("profile_id", "")),
                                profile_name=payload.get("profile_name", ""),
                                chat_id=payload.get("chat_id", 0),
                                msg_id=payload.get("message_id", 0),
                                status=f"retry_{attempt}",
                                http_status=last_http_status or 0,
                                response_time_ms=elapsed_ms,
                                error_message=last_error,
                                payload=json.dumps(payload),
                                attempt=attempt,
                            )
                        except Exception as db_exc:
                            log.error(
                                f"[WEBHOOK] Failed to record retry in DB: {db_exc}"
                            )

                except Exception as exc:
                    elapsed_ms = (
                        int((time.time() - start_time) * 1000)
                        if "start_time" in locals()
                        else 0
                    )
                    last_error = f"Unexpected error: {exc}"
                    last_response_time_ms = elapsed_ms
                    log.warning(
                        f"[WEBHOOK] Unexpected error delivering to {service_name} ({elapsed_ms}ms, attempt {attempt}/{max_attempts}): {exc}"
                    )

                    # Record retry attempt in database
                    if db_engine and attempt < max_attempts:
                        from .store import record_webhook_delivery

                        try:
                            record_webhook_delivery(
                                engine=db_engine,
                                webhook_service=service_name,
                                profile_id=str(payload.get("profile_id", "")),
                                profile_name=payload.get("profile_name", ""),
                                chat_id=payload.get("chat_id", 0),
                                msg_id=payload.get("message_id", 0),
                                status=f"retry_{attempt}",
                                http_status=last_http_status or 0,
                                response_time_ms=elapsed_ms,
                                error_message=last_error,
                                payload=json.dumps(payload),
                                attempt=attempt,
                            )
                        except Exception as db_exc:
                            log.error(
                                f"[WEBHOOK] Failed to record retry in DB: {db_exc}"
                            )

            # After all retries, mark as failed if not successful
            if not success:
                log.error(
                    f"[WEBHOOK] Failed to deliver to {service_name} after {max_attempts} attempts. Last error: {last_error}"
                )
                results["failed"].append(service_name)

                # Record final failure in database
                if db_engine:
                    from .store import record_webhook_delivery

                    try:
                        record_webhook_delivery(
                            engine=db_engine,
                            webhook_service=service_name,
                            profile_id=str(payload.get("profile_id", "")),
                            profile_name=payload.get("profile_name", ""),
                            chat_id=payload.get("chat_id", 0),
                            msg_id=payload.get("message_id", 0),
                            status="failed",
                            http_status=last_http_status,
                            response_time_ms=last_response_time_ms,
                            error_message=last_error,
                            payload=json.dumps(payload),
                            attempt=max_attempts,
                        )
                    except Exception as db_exc:
                        log.error(f"[WEBHOOK] Failed to record failure in DB: {db_exc}")

    return results
