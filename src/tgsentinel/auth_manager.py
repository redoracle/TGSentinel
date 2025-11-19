"""Authentication manager for TG Sentinel.

Handles Telegram authentication flow including:
- Phone code requests and verification
- 2FA password handling
- Rate limiting
- Auth queue processing
- Session management during authentication
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from redis import Redis
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)

# Redis keys for auth queue and responses
AUTH_QUEUE_KEY = "tgsentinel:auth_queue"
AUTH_RESPONSE_HASH = "tgsentinel:auth_responses"


def extract_retry_after_seconds(exc: Exception) -> int | None:
    """Extract retry_after seconds from a Telegram exception.

    Args:
        exc: Telegram API exception

    Returns:
        Number of seconds to wait, or None if not found
    """
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
    m = re.search(r"wait of\s+(\d+)\s+seconds", msg)
    if m:
        try:
            return max(int(m.group(1)), 0)
        except Exception:
            return None
    return None


def normalize_auth_error(exc: Exception) -> Dict[str, Any]:
    """Normalize authentication errors into a consistent error response.

    Args:
        exc: Exception from Telegram authentication

    Returns:
        Dictionary with status, message, reason, and optional retry_after
    """
    msg = str(exc)
    retry_after = extract_retry_after_seconds(exc)
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


class AuthManager:
    """Manages Telegram authentication flow and rate limiting."""

    def __init__(
        self,
        client: TelegramClient,
        redis_client: Redis,
        config,
        authorized_callback=None,
        user_cache_refresh_callback=None,
    ):
        """Initialize authentication manager.

        Args:
            client: Telethon client instance
            redis_client: Redis client for state management
            config: TG Sentinel configuration object
            authorized_callback: Optional callback when auth succeeds
            user_cache_refresh_callback: Optional callback to refresh user identity cache
        """
        self.client = client
        self.redis = redis_client
        self.config = config
        self.authorized = False
        self.auth_event = asyncio.Event()
        self.client_lock = asyncio.Lock()
        self.log = logger

        self._authorized_callback = authorized_callback
        self._user_cache_refresh_callback = user_cache_refresh_callback

    def update_client(self, new_client: TelegramClient) -> None:
        """Update the client reference (used after session import or logout).

        Args:
            new_client: New TelegramClient instance
        """
        self.client = new_client
        self.log.info("AuthManager client reference updated")

    def check_rate_limit(
        self, action: str, phone: str | None = None
    ) -> tuple[bool, int]:
        """Check if action is rate limited.

        DISABLED: Rate limiting disabled for private app environment.
        Always returns (True, 0) to allow all actions.

        Args:
            action: Action type (send_code, resend_code, sign_in)
            phone: Optional phone number

        Returns:
            Tuple of (is_allowed, wait_seconds)
        """
        # Rate limiting disabled for private environment
        return True, 0

    def set_rate_limit(
        self, action: str, wait_seconds: int, phone: str | None = None
    ) -> None:
        """Set rate limit for an action.

        DISABLED: Rate limiting disabled for private app environment.
        This method now does nothing.

        Args:
            action: Action type
            wait_seconds: How long to rate limit
            phone: Optional phone number
        """
        # Rate limiting disabled for private environment - no-op
        return

    def set_auth_response(self, request_id: str, payload: Dict[str, Any]) -> None:
        """Store auth response in Redis for UI to retrieve.

        Args:
            request_id: Request ID from UI
            payload: Response payload dictionary
        """
        try:
            self.redis.hset(AUTH_RESPONSE_HASH, request_id, json.dumps(payload))
            self.redis.expire(AUTH_RESPONSE_HASH, 120)
        except Exception as exc:
            self.log.warning("Failed to store auth response: %s", exc)

    async def ensure_client_connected(self):
        """Ensure Telegram client is connected."""
        if not self.client.is_connected():  # type: ignore[misc]
            await self.client.connect()  # type: ignore[misc]

    async def handle_auth_request(self, data: Dict[str, Any]) -> None:
        """Handle authentication request from UI.

        Args:
            data: Request data containing action, phone, code, etc.
        """
        action = data.get("action")
        request_id = data.get("request_id")
        self.log.info(
            "[AUTH] Processing auth request: action=%s, request_id=%s, authorized=%s",
            action,
            request_id,
            self.authorized,
        )
        if not request_id:
            self.log.warning("[AUTH] Missing request_id in auth request data")
            return
        if self.authorized and action != "status":
            self.log.warning("[AUTH] Rejecting %s request - already authorized", action)
            self.set_auth_response(
                request_id,
                {"status": "error", "message": "Already authorized"},
            )
            return

        try:
            if action == "start":
                await self._handle_start_auth(data, request_id)

            elif action == "resend":
                await self._handle_resend_code(data, request_id)

            elif action == "verify":
                await self._handle_verify_code(data, request_id)

            else:
                self.set_auth_response(
                    request_id,
                    {"status": "error", "message": f"Unknown action: {action}"},
                )
        except Exception as exc:
            self.log.error(
                "[AUTH] Request failed: action=%s, error=%s", action, exc, exc_info=True
            )
            error_response = normalize_auth_error(exc)

            # Store rate limit if present
            retry_after = error_response.get("retry_after")
            if retry_after and retry_after > 0:
                phone = data.get("phone")
                if action == "start":
                    self.set_rate_limit("send_code", retry_after, phone)
                elif action == "resend":
                    self.set_rate_limit("resend_code", retry_after, phone)
                elif action == "verify":
                    self.set_rate_limit("sign_in", retry_after, phone)

                self.log.error(
                    "[AUTH] Rate limit detected: action=%s, wait=%d seconds (~%.1f hours)",
                    action,
                    retry_after,
                    retry_after / 3600.0,
                )

            self.log.debug("[AUTH] Sending error response: %s", error_response)
            self.set_auth_response(request_id, error_response)

    async def _handle_start_auth(self, data: Dict[str, Any], request_id: str) -> None:
        """Handle 'start' action - send auth code to phone."""
        phone = str(data.get("phone", "")).strip()
        if not phone:
            raise ValueError("Phone is required")

        # Clear any stale login progress from previous sessions
        try:
            self.redis.delete("tgsentinel:login_progress")
            self.log.debug("[AUTH] Start: cleared stale login progress")
        except Exception as clear_exc:
            self.log.debug(
                "[AUTH] Start: failed to clear login progress: %s", clear_exc
            )

        # Check rate limit before attempting
        is_allowed, wait_seconds = self.check_rate_limit("send_code", phone)
        if not is_allowed:
            self.log.warning(
                "[AUTH] Start: rate limited, %d seconds remaining", wait_seconds
            )
            self.set_auth_response(
                request_id,
                {
                    "status": "error",
                    "message": f"Rate limited. Please wait {wait_seconds} seconds before trying again.",
                    "reason": "flood_wait",
                    "retry_after": wait_seconds,
                },
            )
            return

        self.log.info("[AUTH] Start: sending code to phone=%s", phone)
        async with self.client_lock:
            await self.ensure_client_connected()
            self.log.debug("[AUTH] Start: client connected, calling send_code_request")
            sent = await self.client.send_code_request(phone)  # type: ignore[misc]
            self.log.info(
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
        self.set_auth_response(request_id, response)
        self.log.debug("[AUTH] Start: response sent to UI via Redis")

    async def _handle_resend_code(self, data: Dict[str, Any], request_id: str) -> None:
        """Handle 'resend' action - resend auth code via SMS."""
        phone = str(data.get("phone", "")).strip()
        if not phone:
            raise ValueError("Phone is required")

        # Check rate limit before resending
        is_allowed, wait_seconds = self.check_rate_limit("resend_code", phone)
        if not is_allowed:
            self.log.warning(
                "[AUTH] Resend: rate limited, %d seconds remaining",
                wait_seconds,
            )
            self.set_auth_response(
                request_id,
                {
                    "status": "error",
                    "message": f"Rate limited. Please wait {wait_seconds} seconds before trying again.",
                    "reason": "flood_wait",
                    "retry_after": wait_seconds,
                },
            )
            return
        async with self.client_lock:
            await self.ensure_client_connected()
            sent = await self.client.send_code_request(  # type: ignore[misc]
                phone, force_sms=True
            )
        response = {
            "status": "ok",
            "message": "Code resent",
            "phone_code_hash": getattr(sent, "phone_code_hash", None),
            "timeout": getattr(sent, "timeout", None),
        }
        self.set_auth_response(request_id, response)

    async def _handle_verify_code(self, data: Dict[str, Any], request_id: str) -> None:
        """Handle 'verify' action - verify code and optionally 2FA password."""
        phone = str(data.get("phone", "")).strip()
        code = str(data.get("code", "")).strip()
        phone_code_hash = data.get("phone_code_hash")
        password = data.get("password")
        self.log.info(
            "[AUTH] Verify: phone=%s, code=%s, has_password=%s",
            phone,
            code[:2] + "***" if code else None,
            bool(password),
        )
        if not phone or not code:
            raise ValueError("Phone and code are required")

        async with self.client_lock:
            # Ensure session file is writable before sign_in (Telethon needs to write during auth)
            session_path = self._ensure_session_writable()

            try:
                await self.ensure_client_connected()
                self.log.debug("[AUTH] Verify: calling client.sign_in with code")
                await self.client.sign_in(  # type: ignore[misc]
                    phone=phone,
                    code=code,
                    phone_code_hash=str(phone_code_hash or ""),
                )
                self.log.info("[AUTH] Verify: sign_in succeeded (no 2FA)")
            except SessionPasswordNeededError:
                self.log.info("[AUTH] Verify: 2FA password required")
                if not password:
                    raise ValueError("Password required for 2FA")

                # Ensure permissions again before 2FA sign_in
                self._ensure_session_writable()

                self.log.debug("[AUTH] Verify: calling client.sign_in with password")
                await self.client.sign_in(password=password)  # type: ignore[misc]
                self.log.info("[AUTH] Verify: 2FA sign_in succeeded")

            # Fix permissions after successful sign_in
            self._ensure_session_writable()

        self.log.debug("[AUTH] Verify: fetching user info with get_me()")
        me = await self.client.get_me()  # type: ignore[misc]
        if me:
            self.log.info(
                "[AUTH] Verify: authentication successful for user_id=%s, username=%s",
                getattr(me, "id", None),
                getattr(me, "username", None),
            )

            # Explicitly save session to ensure persistence
            self._save_session(session_path)

            # Refresh user identity cache to store avatar and full user info
            if self._user_cache_refresh_callback:
                try:
                    await self._user_cache_refresh_callback(me)
                    self.log.debug("[AUTH] Verify: cached user identity with avatar")
                except Exception as cache_exc:
                    self.log.warning(
                        "[AUTH] Verify: cache refresh failed: %s", cache_exc
                    )
                    # Fallback to basic auth marking
                    if self._authorized_callback:
                        result = self._authorized_callback(me)
                        if asyncio.iscoroutine(result):
                            await result
                else:
                    # Cache refresh succeeded
                    if self._authorized_callback:
                        result = self._authorized_callback()
                        if asyncio.iscoroutine(result):
                            await result
            elif self._authorized_callback:
                result = self._authorized_callback(me)
                if asyncio.iscoroutine(result):
                    await result

            # Mark authorized locally
            self.authorized = True
            self.auth_event.set()

            self.set_auth_response(
                request_id,
                {
                    "status": "ok",
                    "message": "Authenticated",
                },
            )
            self.log.debug("[AUTH] Verify: success response sent to UI")
        else:
            self.log.error("[AUTH] Verify: get_me() returned None after sign_in")
            raise ValueError("Verification failed; account not authorized")

    def _ensure_session_writable(self) -> Path:
        """Ensure session file has writable permissions.

        Returns:
            Path to session file
        """
        try:
            session_path = Path(
                self.config.telegram_session or "/app/data/tgsentinel.session"
            )
            if session_path.exists():
                os.chmod(session_path, 0o666)
                self.log.debug("[AUTH] Session file permissions set to 0o666")
            return session_path
        except Exception as perm_exc:
            self.log.warning(
                "[AUTH] Failed to set session permissions: %s",
                perm_exc,
            )
            return Path(self.config.telegram_session or "/app/data/tgsentinel.session")

    def _save_session(self, session_path: Path) -> None:
        """Save Telethon session to disk.

        Args:
            session_path: Path to session file
        """
        try:
            if hasattr(self.client, "session") and hasattr(self.client.session, "save"):  # type: ignore[misc]
                self.client.session.save()  # type: ignore[misc]
                self.log.info("[AUTH] Session saved to disk")

            # Verify session file exists and is readable
            if session_path.exists():
                size = session_path.stat().st_size
                self.log.info(
                    "[AUTH] Session file confirmed, size=%d bytes",
                    size,
                )
            else:
                self.log.warning("[AUTH] Session file not found after save!")
        except Exception as save_exc:
            self.log.error(
                "[AUTH] Failed to save session: %s",
                save_exc,
                exc_info=True,
            )

    async def auth_queue_worker(self, shutdown_event: asyncio.Event) -> None:
        """Background worker that processes authentication requests from Redis queue.

        Args:
            shutdown_event: Event to signal shutdown
        """
        self.log.info(
            "[AUTH-WORKER] Starting auth queue worker (listening on %s)", AUTH_QUEUE_KEY
        )
        loop = asyncio.get_running_loop()
        while not shutdown_event.is_set():
            try:
                result = await loop.run_in_executor(
                    None, lambda: self.redis.blpop([AUTH_QUEUE_KEY], timeout=5)
                )
            except Exception as exc:
                self.log.debug("[AUTH-WORKER] Queue poll failed: %s", exc)
                await asyncio.sleep(1)
                continue

            if not result:
                continue

            _, payload = result  # type: ignore[misc]
            self.log.debug(
                "[AUTH-WORKER] Received auth request from queue: %s bytes",
                len(payload) if payload else 0,
            )
            try:
                data = json.loads(
                    payload.decode() if isinstance(payload, bytes) else payload
                )
                self.log.debug(
                    "[AUTH-WORKER] Parsed request: action=%s, request_id=%s",
                    data.get("action"),
                    data.get("request_id"),
                )
            except Exception as exc:
                self.log.warning("[AUTH-WORKER] Invalid auth queue payload: %s", exc)
                continue

            await self.handle_auth_request(data)
        self.log.info("[AUTH-WORKER] Auth queue worker stopped")
