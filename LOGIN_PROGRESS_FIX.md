# Login Progress Bar Fix

## Problem

The login progress bar was getting stuck at 100% when users refreshed the page during or after login. This was caused by stale `tgsentinel:login_progress` Redis data persisting between login sessions.

### Root Causes

1. Login progress updates were stored in Redis **without TTL**, causing them to persist indefinitely
2. When starting a new login, the old "completed" (100%) progress data was not cleared
3. The UI JavaScript polled `/api/worker/login-progress` and immediately saw the stale 100% completion

## Solution

### 1. Clear Stale Progress at Login Start

**File**: `src/tgsentinel/auth_manager.py`

Added code to delete any existing `tgsentinel:login_progress` key when processing a new "start" auth action:

```python
# Clear any stale login progress from previous sessions
try:
    self.redis.delete("tgsentinel:login_progress")
    self.log.debug("[AUTH] Start: cleared stale login progress")
except Exception as clear_exc:
    self.log.debug("[AUTH] Start: failed to clear login progress: %s", clear_exc)
```

### 2. Set TTL on All Login Progress Updates

**File**: `src/tgsentinel/redis_operations.py`

Changed `publish_login_progress()` to **always** use a TTL (default 300 seconds):

```python
def publish_login_progress(
    self, stage: str, percent: int, message: str, ttl: Optional[int] = 300
) -> None:
    # Always use TTL to prevent stale progress data between sessions
    self.redis.setex(LOGIN_PROGRESS_KEY, ttl, json.dumps(payload))
    self.log.debug("Published login progress: %s (%d%%), TTL=%ds", stage, percent, ttl)
```

### 3. Add TTL to Session Lifecycle Completion

**File**: `src/tgsentinel/session_lifecycle.py`

Updated the final 100% completion update to use `setex()` instead of `set()`:

```python
self.redis_client.setex(
    "tgsentinel:login_progress",
    300,  # 5 minute TTL to prevent stale data
    json.dumps({...})
)
```

## Testing

### Manual Verification

```bash
# 1. Clean containers and volumes
docker compose down -v

# 2. Rebuild and start
docker compose build && docker compose up -d

# 3. Create stale progress
docker exec tgsentinel-redis-1 redis-cli SETEX tgsentinel:login_progress 300 \
  '{"stage":"completed","percent":100,"message":"Old login","timestamp":"2025-11-17T19:00:00Z"}'

# 4. Trigger new auth start
docker exec tgsentinel-redis-1 redis-cli RPUSH tgsentinel:auth_queue \
  '{"action":"start","request_id":"test123","phone":"+31625561396"}'

# 5. Wait and verify progress was cleared
sleep 3
docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:login_progress
# Should return empty (no output)
```

### Expected Behavior

- **Before fix**: Stale "completed" progress persisted, causing UI to show 100% and reload immediately
- **After fix**:
  - Stale progress is cleared when new login starts
  - All progress updates expire after 5 minutes (300s TTL)
  - UI shows proper incremental progress (0% → 50% → 80% → 100%)
  - Progress bar completes smoothly and app loads correctly

## No Timeouts or Arbitrary Limits

As requested, the fix does not introduce any timeouts or arbitrary time limits to the login flow itself. The TTL is only for **cleanup** of stale data - the actual login process waits indefinitely for completion (via the JavaScript polling loop that continues until `stage === 'completed'` or `percent >= 100`).

## Files Modified

1. `src/tgsentinel/auth_manager.py` - Clear stale progress at auth start
2. `src/tgsentinel/redis_operations.py` - Always use TTL for progress updates
3. `src/tgsentinel/session_lifecycle.py` - Use setex() for final completion update
