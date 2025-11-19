# Handler Startup & Login Flow Fix

## Problem

During login/logout/relogin cycles, the handlers weren't coordinating properly with the cache refresher, causing:

1. Login progress bar reaching 100% before channels/users were loaded
2. Missing visibility into handler activity (`[CHATS-HANDLER]`, `[USERS-HANDLER]`, `[DIALOGS-HANDLER]`, `[CACHE-REFRESHER]`)
3. Unclear sequencing between authorization completion and cache readiness

## Solution

### 1. Improved Login Flow Sequencing

**File**: `src/tgsentinel/session_lifecycle.py`

Added a 2-second delay between publishing the `session_authorized` event and marking login as 100% complete:

```python
# Publish session_authorized event to trigger immediate cache refresh
self.redis_mgr.publish_session_event("session_authorized", user_id=user.id)
log.info("[SESSION-MONITOR] ✓ Published session_authorized event to trigger immediate cache refresh")

# Wait briefly for cache refresher to start processing
log.info("[SESSION-MONITOR] Waiting for cache refresher to initialize...")
await asyncio.sleep(2)

# Publish login completion (100%) with TTL
self.redis_client.setex("tgsentinel:login_progress", 300, ...)
log.info("[SESSION-MONITOR] Published login completion (100%) with TTL")
```

**Benefits**:

- Gives cache refresher time to receive the event and start fetching dialogs
- Prevents race condition where UI sees 100% before data is ready
- Maintains smooth progress flow

### 2. Cache Refresher Progress Update

**File**: `src/tgsentinel/cache_manager.py`

Changed cache refresher to publish at 95% (not 100%) to distinguish between "cache ready" and "login complete":

```python
# PUBLISH CACHE READY STATUS (95% - before avatar caching)
# This allows UI to become responsive immediately while avatars load in background
await asyncio.to_thread(
    redis_client.setex,
    "tgsentinel:login_progress",
    300,  # 5 minute TTL
    json.dumps({
        "stage": "cache_ready",
        "percent": 95,
        "message": f"✓ Loaded {len(channels_list)} channels, {len(users_list)} users. Caching avatars...",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }),
)
logger.info("[CACHE-REFRESHER] Published cache ready status (95%) with TTL")
```

**Benefits**:

- Clear distinction between cache ready (95%) and login complete (100%)
- Shows actual channel/user counts in progress message
- Uses TTL to prevent stale progress data

### 3. Enhanced Handler Logging

**File**: `src/tgsentinel/telegram_request_handlers.py`

Added consistent handler tags to all log messages:

```python
async def handle_requests(self, request_pattern: str, process_func: Callable) -> None:
    handler_name = self.__class__.__name__.replace("Handler", "").upper()
    self.log.info("[%s-HANDLER] Starting request handler loop", handler_name)

    while True:
        if not self.is_authorized():
            self.log.debug("[%s-HANDLER] Not authorized, waiting...", handler_name)
            continue

        requests = self.redis_mgr.scan_and_get_requests(request_pattern)
        if requests:
            self.log.info("[%s-HANDLER] Found %d request(s)", handler_name, len(requests))
```

Updated all handler methods:

- `[CHATS-HANDLER]` - for `TelegramChatsHandler`
- `[USERS-HANDLER]` - for `TelegramUsersHandler`
- `[DIALOGS-HANDLER]` - for `TelegramDialogsHandler`
- `[CACHE-REFRESHER]` - already existed in `cache_manager.py`

**Benefits**:

- Easy to grep logs for specific handler activity
- Consistent with `Concurrency.instructions.md` naming conventions
- Helps debug handler lifecycle issues

## Handler Lifecycle

All handlers are started at application startup and run continuously:

1. **At Startup** (before authorization):

   - All handlers start but wait for `handshake_gate` and `authorized_check()`
   - They log `"Not authorized, waiting..."` until login completes

2. **After Authorization**:

   - `[SESSION-MONITOR]` publishes `session_authorized` event
   - `[CACHE-REFRESHER]` receives event and immediately fetches dialogs
   - `[CACHE-REFRESHER]` publishes 95% progress with channel/user counts
   - `[SESSION-MONITOR]` publishes 100% completion after 2-second delay
   - `[CHATS-HANDLER]`, `[DIALOGS-HANDLER]`, `[USERS-HANDLER]` become active

3. **During Relogin**:
   - Handlers pause when `handshake_gate` is cleared
   - Resume automatically when gate is set after new session authorized
   - Cache refresher detects new session and triggers immediate refresh

## Progress Flow

New login progress sequence:

```
 0% - Connecting to Telegram...        [SESSION-MONITOR]
40% - Connecting to Telegram...        [SESSION-MONITOR]
60% - Verifying authorization...       [SESSION-MONITOR]
70% - Downloading user avatar...       [SESSION-MONITOR]
80% - Loading channels and contacts... [SESSION-MONITOR]
95% - ✓ Loaded X channels, Y users... [CACHE-REFRESHER]
100% - Session switch complete!        [SESSION-MONITOR]
```

## Testing

Use the provided test script:

```bash
./test_login_logout_cycle.sh
```

Expected log sequence after session upload:

```
[SESSION-MONITOR] ✓ Published session_authorized event
[SESSION-MONITOR] Waiting for cache refresher to initialize...
[CACHE-REFRESHER] Session authorization detected, triggering immediate refresh
[CACHE-REFRESHER] Fetching dialogs for cache refresh...
[CACHE-REFRESHER] ✓ Updated cache: 365 channels, 127 users
[CACHE-REFRESHER] Published cache ready status (95%) with TTL
[SESSION-MONITOR] Published login completion (100%) with TTL
[CHATS-HANDLER] Starting request handler loop
[DIALOGS-HANDLER] Starting request handler loop
[USERS-HANDLER] Starting request handler loop
```

## Files Modified

1. `src/tgsentinel/session_lifecycle.py`

   - Added 2-second delay before 100% completion
   - Improved event publishing log messages

2. `src/tgsentinel/cache_manager.py`

   - Changed to publish 95% (cache_ready) instead of 100%
   - Added TTL to progress update (300 seconds)
   - Enhanced progress message with actual counts

3. `src/tgsentinel/telegram_request_handlers.py`
   - Added `[HANDLER-NAME]` tags to all log messages
   - Made handler startup visible in logs
   - Improved request processing visibility

## Architecture Alignment

This fix aligns with TG Sentinel architectural patterns:

- ✅ Follows `Concurrency.instructions.md` handler naming
- ✅ Respects `AUTH.instructions.md` authorization flow
- ✅ Uses `Progressbar.instructions.md` progress state management
- ✅ Maintains handler lifecycle from `ENGINEERING_GUIDELINES.md`
