# Set umask to ensure files created are world-readable/writable (for container multi-access)
# MUST be at the very top before any imports or file operations
import os as _os_early

_os_early.umask(0o022)

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from redis import Redis
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import SQLiteSession

from .api import (
    set_config,
    set_engine,
    set_redis_client,
    set_sentinel_state,
    start_api_server,
)
from .auth_manager import AUTH_QUEUE_KEY, AUTH_RESPONSE_HASH, AuthManager
from .cache_manager import channels_users_cache_refresher
from .client import make_client, start_ingestion
from .config import load_config
from .digest import send_digest
from .logging_setup import setup_logging
from .metrics import dump, initialize_build_info
from .participant_info import fetch_participant_info
from .redis_operations import RedisManager
from .session_helpers import SessionHelpers
from .session_lifecycle import SessionLifecycleManager
from .session_manager import relogin_coordinator, session_persistence_handler
from .shutdown_coordinator import ShutdownCoordinator
from .store import init_db
from .telegram_request_handlers import (
    ParticipantInfoHandler,
    TelegramChatsHandler,
    TelegramDialogsHandler,
    TelegramUsersHandler,
)
from .worker import process_loop
from .worker_orchestrator import WorkerOrchestrator


async def _run():
    setup_logging()
    log = logging.getLogger("tgsentinel")

    # Initialize build info metric at startup
    initialize_build_info()

    cfg = load_config()
    engine = init_db(cfg.db_uri)

    session_file_path = Path(cfg.telegram_session or "/app/data/tgsentinel.session")

    # Only create client if session file exists and is not empty
    # This prevents Telethon from creating an empty session file at startup
    client: TelegramClient | None = None
    if session_file_path.exists() and session_file_path.stat().st_size > 0:
        log.info(
            "[STARTUP] Found existing session file (%d bytes), creating client",
            session_file_path.stat().st_size,
        )
        client = make_client(cfg)
    else:
        log.info(
            "[STARTUP] No valid session file found, deferring client creation until session upload"
        )
        # Create a minimal dummy client that will be replaced on session upload
        # Use a temporary in-memory session to avoid creating files
        from telethon.sessions import MemorySession

        client = TelegramClient(MemorySession(), cfg.api_id, cfg.api_hash)

    # Initialize Redis early so helpers can use it
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)
    redis_mgr = RedisManager(r)

    # Create client lock to prevent concurrent session file access
    client_lock = asyncio.Lock()

    # Initialize session helpers with client_lock to prevent SQLite locking
    session_helpers = SessionHelpers(
        client=client,
        session_file_path=session_file_path,
        redis_manager=redis_mgr,
        client_lock=client_lock,
    )

    def _close_session_binding() -> None:
        session_helpers.close_session_binding()

    def _rebind_session_binding() -> None:
        session_helpers.rebind_session_binding()

    async def _refresh_user_identity_cache(user_obj=None) -> None:
        await session_helpers.refresh_user_identity_cache(user_obj)

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
    dialogs_cache: tuple[datetime, list] | None = None
    dialogs_cache_lock = asyncio.Lock()
    dialogs_cache_ttl = timedelta(seconds=45)

    # Client reference dictionary for dynamic lookup after session imports
    client_ref_dict = {"value": client}

    # Core shared state for generation-based architecture
    authorized = False
    authorized_user_id: int | None = None
    session_generation = 0  # Monotonically increasing on each login
    auth_event = asyncio.Event()
    cache_ready_event = asyncio.Event()
    logout_event = asyncio.Event()

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

        # Publish sentinel credentials
        redis_mgr.publish_credentials(fingerprint, source="sentinel", ttl=3600)

        # Verify parity with UI credentials
        try:
            ui_payload = redis_mgr.get_credentials(source="ui")
            if not ui_payload:
                log.warning(
                    "UI credential fingerprint not found in Redis; ensure UI container is running"
                )
                return
            ui_fp = ui_payload.get("fingerprint") or {}
            if ui_fp != fingerprint:
                log.error(
                    "TG credential mismatch detected. UI=%s sentinel=%s",
                    ui_fp,
                    fingerprint,
                )
                redis_mgr.publish_worker_status(
                    authorized=False, status="credential_mismatch", ttl=60
                )
                raise SystemExit("Credential mismatch between UI and sentinel")
        except SystemExit:
            raise
        except Exception as exc:
            log.warning("Could not verify credential parity: %s", exc)

    _publish_and_check_credentials()

    # Defer ingestion until after authorization

    async def _mark_authorized(user=None):
        nonlocal authorized, authorized_user_id, session_generation

        # Increment session generation for new login
        session_generation += 1
        authorized = True
        authorized_user_id = getattr(user, "id", None) if user else None
        auth_manager.authorized = True

        # Set auth event (unblocks handlers waiting for auth)
        auth_event.set()

        log.info(
            "[AUTH] Marking as authorized, user=%s, generation=%d",
            authorized_user_id,
            session_generation,
        )

        # Update API state
        from .api import set_sentinel_state

        set_sentinel_state("authorized", True)
        set_sentinel_state("connected", True)
        set_sentinel_state("session_generation", session_generation)

        # IMMEDIATELY refresh user identity and avatar cache BEFORE publishing status
        # This ensures the UI has the correct avatar when it fetches session info
        if user:
            try:
                await _refresh_user_identity_cache(user)
                log.debug("[AUTH] User identity and avatar cached")
            except Exception as refresh_exc:
                log.warning("[AUTH] Failed to refresh user identity: %s", refresh_exc)

        # Publish worker status to Redis with generation and warming_caches status
        redis_mgr.publish_worker_status(
            authorized=True,
            status="warming_caches",
            ttl=3600,
            extra_fields={
                "user_id": authorized_user_id,
                "session_generation": session_generation,
            },
        )

    async def _ensure_client_connected():
        await session_helpers.ensure_client_connected()

    def _set_auth_response(request_id: str, payload: Dict[str, Any]) -> None:
        redis_mgr.set_auth_response(AUTH_RESPONSE_HASH, request_id, payload, ttl=120)

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

                # Skip dialog fetch if we're not fully authorized yet to avoid DB locks
                if not authorized:
                    log.debug("Skipping dialog fetch - not yet authorized")
                    return []

                # Use client_ref_dict to get current client (handles session imports)
                current_client = client_ref_dict["value"]
                dialogs = await current_client.get_dialogs()  # type: ignore[misc]
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

    # Cache refresher functions extracted to cache_manager.py module

    # Initialize AuthManager for handling authentication flow
    auth_manager = AuthManager(
        client=client,
        redis_client=r,
        config=cfg,
        authorized_callback=lambda user=None: _mark_authorized(user),
        user_cache_refresh_callback=_refresh_user_identity_cache,
    )
    # Reference to authorized state from auth_manager
    authorized = auth_manager.authorized
    auth_event = auth_manager.auth_event

    # Relogin coordinator and session persistence extracted to session_manager.py module

    # Setup graceful shutdown using ShutdownCoordinator
    loop = asyncio.get_running_loop()
    shutdown_coordinator = ShutdownCoordinator(loop)
    shutdown_coordinator.register_signal_handlers()
    shutdown_event = shutdown_coordinator.shutdown_event

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
        redis_mgr.clear_relogin_state()
    except Exception as exc:
        log.warning("[AUTH] Failed to clear stale auth queue: %s", exc)

    # Start auth_worker early so UI can trigger auth requests
    auth_worker_task = asyncio.create_task(
        auth_manager.auth_queue_worker(shutdown_event)
    )
    log.info("[STARTUP] Auth queue worker started")

    # Start HTTP API server for UI communication
    api_port = int(os.getenv("SENTINEL_API_PORT", "8080"))
    set_config(cfg)
    set_redis_client(r)
    set_engine(engine)
    set_sentinel_state("session_path", str(session_file_path))
    start_api_server(host="0.0.0.0", port=api_port)
    log.info("[STARTUP] HTTP API server started on port %d", api_port)

    # Non-interactive startup: do not prompt for phone in headless envs
    log.info("[STARTUP] Connecting to Telegram...")
    await client_ref_dict["value"].connect()  # type: ignore[misc]
    log.info("[STARTUP] Connected to Telegram")

    # Helper to update client reference (used by relogin_coordinator)
    def set_client(new_client: TelegramClient) -> None:
        nonlocal client
        client = new_client
        client_ref_dict["value"] = new_client

    # Now start relogin_coordinator after initial connection is established
    # This prevents race conditions during the initial connect phase
    relogin_coordinator_task = asyncio.create_task(
        relogin_coordinator(
            client_ref=lambda: client,
            client_setter=set_client,
            redis_client=r,
            handshake_gate=handshake_gate,
            authorized_setter=lambda val: setattr(auth_manager, "authorized", val),
            make_client_func=make_client,
            cfg=cfg,
            close_session_func=_close_session_binding,
            refresh_user_identity_func=_refresh_user_identity_cache,
            mark_authorized_func=_mark_authorized,
        )
    )
    log.info("[STARTUP] Relogin coordinator started")

    # Initialize session lifecycle manager
    session_lifecycle_mgr = SessionLifecycleManager(
        cfg=cfg,
        redis_client=r,
        redis_manager=redis_mgr,
        session_helpers=session_helpers,
        session_file_path=session_file_path,
        make_client_func=make_client,
        start_ingestion_func=start_ingestion,
        mark_authorized_func=_mark_authorized,
    )
    # Store references for client updates during session import
    session_lifecycle_mgr.auth_manager = auth_manager
    # participant_handler will be assigned later after initialization

    # Start session update monitor immediately to catch uploaded sessions
    async def session_monitor():
        """Monitor for uploaded session files and logout requests."""
        nonlocal authorized, dialogs_cache

        # Use shared client_ref_dict and local refs for other state
        authorized_ref = {"value": authorized}
        dialogs_cache_ref = {"value": dialogs_cache}

        await session_lifecycle_mgr.monitor_session_events(
            client_ref=client_ref_dict,  # Use shared dict
            auth_event=auth_event,
            handshake_gate=handshake_gate,
            authorized_ref=authorized_ref,
            dialogs_cache_ref=dialogs_cache_ref,
        )

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
                client_ref_dict["value"].get_me(), timeout=15  # type: ignore[misc]
            )
            user_id = getattr(me, "id", None) if me else None
            username = getattr(me, "username", None) if me else None

            if me:
                log.info(
                    "[STARTUP] ✓ Session restored successfully: user_id=%s, username=%s",
                    user_id,
                    username,
                )
                await _mark_authorized(me)
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
                auth_status = await asyncio.wait_for(
                    client_ref_dict["value"].is_user_authorized(), timeout=10  # type: ignore[misc]
                )
                if authorized:
                    log.info("[STARTUP] ✓ Client is authorized (direct check)")
                    await _mark_authorized()
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
                    await _mark_authorized()
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
        redis_mgr.publish_worker_status(authorized=False, status="waiting", ttl=30)

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
            redis_mgr.publish_worker_status(
                authorized=False, status="unauthorized", ttl=60
            )
            return

    # Get and store current user info in Redis for UI access
    try:
        me = await client_ref_dict["value"].get_me()  # type: ignore[misc]
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
            min_score=0.0,
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
            min_score=0.0,
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
            min_score=0.0,
        )

    # Initialize request handlers
    participant_handler = ParticipantInfoHandler(
        client=client,
        redis_client=r,
        redis_manager=redis_mgr,
        handshake_gate=handshake_gate,
        authorized_check=lambda: authorized,
        auth_event=auth_event,
        cache_ready_event=cache_ready_event,
        logout_event=logout_event,
        get_session_generation=lambda: session_generation,
        get_authorized_user_id=lambda: authorized_user_id,
    )
    # Assign to session lifecycle manager for client updates
    session_lifecycle_mgr.participant_handler = participant_handler

    chats_handler = TelegramChatsHandler(
        redis_client=r,
        redis_manager=redis_mgr,
        handshake_gate=handshake_gate,
        authorized_check=lambda: authorized,
        auth_event=auth_event,
        cache_ready_event=cache_ready_event,
        logout_event=logout_event,
        get_session_generation=lambda: session_generation,
        get_authorized_user_id=lambda: authorized_user_id,
    )

    dialogs_handler = TelegramDialogsHandler(
        redis_client=r,
        redis_manager=redis_mgr,
        handshake_gate=handshake_gate,
        authorized_check=lambda: authorized,
        auth_event=auth_event,
        cache_ready_event=cache_ready_event,
        logout_event=logout_event,
        get_session_generation=lambda: session_generation,
        get_authorized_user_id=lambda: authorized_user_id,
    )

    users_handler = TelegramUsersHandler(
        redis_client=r,
        redis_manager=redis_mgr,
        handshake_gate=handshake_gate,
        authorized_check=lambda: authorized,
        auth_event=auth_event,
        cache_ready_event=cache_ready_event,
        logout_event=logout_event,
        get_session_generation=lambda: session_generation,
        get_authorized_user_id=lambda: authorized_user_id,
    )

    # Initialize worker orchestrator
    worker_orchestrator = WorkerOrchestrator(
        cfg=cfg,
        client_ref=lambda: client_ref_dict["value"],  # Dynamic client lookup
        engine=engine,
        redis_manager=redis_mgr,
        handshake_gate=handshake_gate,
        authorized_check=lambda: authorized,
        participant_handler=participant_handler,
        chats_handler=chats_handler,
        dialogs_handler=dialogs_handler,
        users_handler=users_handler,
    )

    async def run_workers():
        """Run all background workers using the orchestrator."""
        await worker_orchestrator.run_all_workers(
            session_persistence_handler_func=lambda: session_persistence_handler(
                client_ref=lambda: client_ref_dict["value"],  # Dynamic client lookup
                authorized_check_func=lambda: authorized,
                redis_client=r,
            ),
            cache_refresher_func=lambda: channels_users_cache_refresher(
                get_client_func=lambda: client_ref_dict[
                    "value"
                ],  # Dynamic client lookup
                redis_client=r,
                get_cached_dialogs_func=_get_cached_dialogs,
                handshake_gate=handshake_gate,
                authorized_check_func=lambda: authorized,
                auth_event=auth_event,
                cache_ready_event=cache_ready_event,
                logout_event=logout_event,
                get_session_generation_func=lambda: session_generation,
                get_authorized_user_id_func=lambda: authorized_user_id,
            ),
        )

    workers_task = asyncio.create_task(run_workers())

    # Wait for either the workers to complete or shutdown signal
    # ShutdownCoordinator handles graceful cancellation of all tasks
    await shutdown_coordinator.wait_for_shutdown_or_completion(
        workers_task, background_tasks=[auth_worker_task, relogin_coordinator_task]
    )

    # Perform graceful shutdown of Telegram client
    await shutdown_coordinator.graceful_shutdown(client)


if __name__ == "__main__":
    asyncio.run(_run())
