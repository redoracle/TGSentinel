# Avatar Loading Analysis Report - TG Sentinel

**Date**: November 18, 2025
**Issue**: Avatar not displaying on navbar after logout/relogin with different user

---

## Executive Summary

The user avatar works correctly on **first container startup and login** but fails to display after **logout and relogin with a new user**. Root cause: The `/logout` compatibility route in `ui/app.py` unconditionally calls `_invalidate_session()` on GET requests, which **deletes `tgsentinel:user_info` from Redis**—even when just navigating to the login page after logout. This creates a race condition where the Sentinel worker successfully publishes the new user's avatar data, but the UI inadvertently wipes it before the navbar can render.

---

## Architecture Overview: Avatar Retrieval Flow

### 1. **Storage Layer (Redis)**

- **Key**: `tgsentinel:user_info` (no TTL by default)
- **Structure**:
  ```json
  {
    "username": "NikolaTeslaTNova",
    "first_name": "Nikola",
    "last_name": "Tesla",
    "phone": "31625561396",
    "user_id": 7128032085,
    "avatar": "/api/avatar/user/7128032085"
  }
  ```
- **Avatar Image Storage**:
  - **Key**: `tgsentinel:user_avatar:{user_id}` (no TTL)
  - **Value**: Base64-encoded stripped_thumb (ultra-light preview, ~2-5KB)
  - **Purpose**: Instant navbar display without network round-trip

### 2. **Avatar Creation Points**

#### **Point A: First Login / Container Startup** (`src/tgsentinel/main.py`)

```python
# Line ~206: After successful authorization
me = await client.get_me()
if me:
    await _refresh_user_identity_cache(me)
    # Calls session_helpers.refresh_user_identity_cache()
```

**Flow**:

1. `session_helpers.refresh_user_identity_cache()` fetches profile photo
2. Downloads full avatar → converts to base64
3. Stores in Redis: `tgsentinel:user_avatar:{user_id}`
4. Builds `user_info` dict with `avatar: "/api/avatar/user/{user_id}"`
5. Publishes to Redis: `tgsentinel:user_info` (via `redis_mgr.cache_user_info()`)

**Result**: ✅ Avatar loads perfectly because cache population completes _before_ UI renders

#### **Point B: Session Import/Relogin** (`src/tgsentinel/session_lifecycle.py`)

```python
# Line ~230-282: handle_session_import()
user = await new_client.get_me()
user_info = {
    "username": user.username,
    "user_id": user.id,
    # ...
}

# Check for stripped_thumb for instant display
if hasattr(user.photo, "stripped_thumb") and user.photo.stripped_thumb:
    cache_key = f"tgsentinel:user_avatar:{user.id}"
    avatar_b64 = base64.b64encode(user.photo.stripped_thumb).decode("utf-8")
    self.redis_client.set(cache_key, avatar_b64)  # ✅ Stored
    user_info["avatar"] = f"/api/avatar/user/{user.id}"

self.redis_mgr.cache_user_info(user_info)  # ✅ Published to Redis
```

**Timing**:

- Sentinel publishes `user_info` at timestamp: `13:53:51.xxx` (from logs)
- Login progress reaches 100% and modal closes
- Browser redirects to `/alerts` → navbar renders

**Result**: ✅ Avatar data exists in Redis when navbar JS runs `populateSessionInfo()`

---

## Navbar Rendering Mechanism

### **Server-Side (Initial HTML)**

**Location**: `ui/templates/base.html` (Lines 50-59)

```html
<img
  id="user-avatar"
  src="{{ url_for('static', filename='images/logo.png') }}"
  data-default-src="{{ url_for('static', filename='images/logo.png') }}"
  loading="lazy"
  alt="User avatar"
  class="rounded-circle ts-avatar"
/>
<span id="username-label" class="ts-username">loading...</span>
<div id="phone-mask" class="ts-phone">pending</div>
```

- Initial render always shows **placeholder** (`/static/images/logo.png`)
- Actual user data populated by JavaScript after DOMContentLoaded

### **Client-Side (JavaScript Population)**

**Location**: `ui/templates/base.html` (Lines 399-434)

```javascript
async function populateSessionInfo(data) {
  const avatarEl = document.getElementById("user-avatar");
  const usernameEl = document.getElementById("username-label");
  const phoneEl = document.getElementById("phone-mask");

  // Handle logged out state
  if (data.authorized === false) {
    if (usernameEl) usernameEl.textContent = "Not logged in";
    if (phoneEl) phoneEl.textContent = "Login required";
    if (avatarEl) _setDefaultAvatar(avatarEl);
    return;
  }

  // Populate authorized user data
  if (usernameEl && data.username) usernameEl.textContent = data.username;
  if (phoneEl && data.phone_masked) phoneEl.textContent = data.phone_masked;

  // Load avatar with cache-busting
  if (avatarEl && data.avatar) {
    const cacheBuster = "?t=" + Date.now();
    const avatarUrl = data.avatar + cacheBuster;
    const ok = await _fetchAvatarToElement(avatarUrl, avatarEl);
    if (!ok) {
      _setDefaultAvatar(avatarEl); // Fallback
    }
  }
}

async function refreshSessionInfo() {
  const response = await fetch("/api/session/info");
  const payload = await response.json();
  populateSessionInfo(payload);
}

document.addEventListener("DOMContentLoaded", () => {
  refreshSessionInfo(); // ✅ Fetches user data on every page load
  refreshWorkerStatus();
});
```

**Key Points**:

1. Every page load triggers `/api/session/info` request
2. Response includes `authorized`, `username`, `avatar`, `phone_masked`
3. Avatar fetched with `cache: 'no-store'` to avoid stale images
4. Falls back to logo.png if fetch fails

### **API Endpoint** (`ui/routes/session.py`)

**Location**: Lines 96-170

```python
@session_bp.route("/info", methods=["GET"])
def session_info():
    # Check worker authorization from Redis
    is_authorized = False
    worker_status_raw = redis_client.get("tgsentinel:worker_status")
    if worker_status_raw:
        worker_status = json.loads(worker_status_raw.decode())
        is_authorized = worker_status.get("authorized") is True

    # Load user info ONLY if authorized
    user_info = _load_cached_user_info() if is_authorized else None

    # Extract avatar from user_info
    avatar = user_info.get("avatar") if user_info else None
    if not avatar:
        avatar = _fallback_avatar()  # "/static/images/logo.png"

    return jsonify({
        "authorized": is_authorized,
        "username": username,
        "avatar": avatar,
        "phone_masked": phone_masked,
        "connected": bool(redis_client),
        "connected_chats": [...]
    })
```

**Helper**: `_load_cached_user_info()` → `load_cached_user_info()` (`ui/redis_cache.py`)

```python
def load_cached_user_info(redis_client):
    raw = redis_client.get("tgsentinel:user_info")
    if not raw:
        logger.warning("[UI-REDIS] No user_info in Redis")
        return None
    info = json.loads(raw.decode())
    return info
```

---

## Problem Analysis: Why Relogin Fails

### **Timeline of Events (From Logs: 13:53:XX)**

```
13:53:28 - UI POST /api/session/logout
13:53:28 - Sentinel detects logout via pub/sub
13:53:38 - Sentinel completes cleanup, deletes tgsentinel.session
13:53:49 - UI POST /api/session/upload (new user session)
13:53:51 - Sentinel imports session, authorizes new user
13:53:51 - Sentinel publishes tgsentinel:user_info with avatar  ✅
13:53:51 - Sentinel caches stripped_thumb avatar              ✅
13:53:51 - Sentinel updates worker_status: authorized=true   ✅
13:53:53 - Login progress reaches 100%
13:53:57 - Browser: GET /logout (redirect after modal closes) ❌
13:53:57 - UI calls _invalidate_session() → DELETES tgsentinel:user_info ❌
13:53:57 - Browser: GET / (redirected from /logout)
13:53:57 - Navbar JS: fetch /api/session/info
13:53:57 - UI logs: "[UI-REDIS] No user_info in Redis" ❌
13:53:57 - Navbar renders with placeholder avatar ❌
```

### **Root Cause: GET /logout Handler**

**Location**: `ui/app.py` (Lines 998-1025)

```python
@app.route("/logout", methods=["GET", "POST"])
def legacy_logout_redirect():
    if request.method == "GET":
        # Browser navigation - clear session and show login page
        try:
            if _resolve_session_path and _invalidate_session:
                session_path = _resolve_session_path()
                _invalidate_session(session_path)  # ❌ PROBLEM HERE
            # Clear Flask session markers
            flask_session.pop("telegram_authenticated", None)
            flask_session.pop("ui_locked", None)
        except Exception as clear_exc:
            logger.debug("Session clear during GET /logout: %s", clear_exc)
        return redirect("/")
    else:
        # POST request - forward to API endpoint
        return redirect("/api/session/logout", code=307)
```

**What `_invalidate_session()` Does** (`ui/auth.py` lines 159-269):

```python
def invalidate_session(redis_client, session_path, config, repo_root):
    # Remove session files
    for path_str in delete_list:
        Path(path_str).unlink(missing_ok=True)

    # Clear Redis caches
    keys = [
        "tgsentinel:user_info",           # ❌ DELETED
        "tgsentinel:telegram_users_cache",
        "tgsentinel:chats_cache",
        RELOGIN_KEY,
    ]
    for k in keys:
        deleted = redis_client.delete(k)

    # Remove avatar cache
    avatar_pattern = "tgsentinel:user_avatar:*"  # ❌ DELETED
    pattern_keys = [k for k in redis_client.scan_iter(match=pattern_pattern)]
    redis_client.delete(*pattern_keys)
```

**Why This Happens**:

1. After login completes, modal closes and redirects to `/alerts`
2. User sees authenticated page briefly
3. **Background requests**: Browser may follow any `/logout` link or cached redirect
4. GET `/logout` triggers `_invalidate_session()` which wipes Redis data
5. Navbar refresh sees empty `user_info` → falls back to placeholder

---

## First Login vs. Relogin Comparison

| **Aspect**                 | **First Login (Container Startup)**                             | **Relogin (User Switch)**                                    |
| -------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------ |
| **Entry Point**            | `src/tgsentinel/main.py` startup sequence                       | `/api/session/upload` → Sentinel session import              |
| **Avatar Creation**        | `session_helpers.refresh_user_identity_cache()` (full download) | `session_lifecycle.handle_session_import()` (stripped_thumb) |
| **Timing**                 | Sequential startup, no navigation conflicts                     | Async import + UI redirect race                              |
| **Redis Publication**      | ✅ Before UI loads                                              | ✅ Before navbar renders (but...)                            |
| **GET /logout Triggered?** | ❌ No (fresh container)                                         | ✅ Yes (modal redirect)                                      |
| **user_info Deleted?**     | ❌ No                                                           | ✅ **YES** (by GET handler)                                  |
| **Avatar Display**         | ✅ **SUCCESS**                                                  | ❌ **FAILS**                                                 |

---

## Evidence from Logs

### **Successful Avatar Storage (Sentinel)**

```
[2025-11-18 13:53:51] [SESSION-MONITOR] ✓ Cached stripped_thumb avatar: /api/avatar/user/7128032085
[2025-11-18 13:53:51] [SESSION-MONITOR] ✓ Updated Redis with user info (avatar: /api/avatar/user/7128032085)
[2025-11-18 13:53:51] [SESSION-MONITOR] Verified user_info in Redis
```

### **Avatar Missing (UI)**

```
[2025-11-18 13:53:57] [UI-REDIS] No user_info in Redis  ← ❌ First fetch after redirect
[2025-11-18 13:54:02] [UI-REDIS] No user_info in Redis  ← Subsequent refresh
[2025-11-18 13:54:04] [UI-REDIS] No user_info in Redis
```

### **Redis State After Relogin**

```bash
$ redis-cli GET tgsentinel:user_info
(nil)  # ❌ Missing

$ redis-cli GET tgsentinel:worker_status
{"authorized": true, "status": "warming_caches", ...}  # ✅ Present

$ redis-cli KEYS tgsentinel:user_avatar:*
(empty array)  # ❌ Avatar cache cleared
```

---

## Why First Login Works

1. **No GET /logout call**: Fresh container startup goes directly from initialization → login modal → `/api/session/upload` → authorized state
2. **No navigation race**: User doesn't trigger any `/logout` GET requests during startup
3. **Clean cache state**: `user_info` remains in Redis from the moment `refresh_user_identity_cache()` publishes it
4. **Navbar loads correctly**: First `populateSessionInfo()` call finds complete user data

---

## Solution Design

### **Option 1: Remove Redis Clearing from GET /logout** (Recommended)

**Change**: `ui/app.py` (Lines 1006-1022)

```python
@app.route("/logout", methods=["GET", "POST"])
def legacy_logout_redirect():
    if request.method == "GET":
        # Browser navigation - ONLY clear Flask session, don't touch Redis
        try:
            from flask import session as flask_session
            flask_session.pop("telegram_authenticated", None)
            flask_session.pop("ui_locked", None)
        except Exception as clear_exc:
            logger.debug("Session clear during GET /logout: %s", clear_exc)
        return redirect("/")
    else:
        # POST request - forward to actual logout API (which handles Redis cleanup)
        return redirect("/api/session/logout", code=307)
```

**Rationale**:

- GET `/logout` is only for **navigation** (showing login page after logout)
- POST `/api/session/logout` already handles full cleanup (Redis, files, pub/sub to Sentinel)
- Separation of concerns:
  - GET = UI state change (Flask session)
  - POST = Backend state change (Redis, Sentinel coordination)

### **Option 2: Add Guard Check in GET /logout**

```python
if request.method == "GET":
    # Only clear Redis if worker is NOT currently authorized
    # (prevents clearing during active session)
    try:
        worker_auth = _check_worker_auth()
        if worker_auth is False:  # Only clear if truly logged out
            if _resolve_session_path and _invalidate_session:
                session_path = _resolve_session_path()
                _invalidate_session(session_path)
        # Always clear Flask markers
        flask_session.pop("telegram_authenticated", None)
        flask_session.pop("ui_locked", None)
    except Exception as clear_exc:
        logger.debug("Session clear during GET /logout: %s", clear_exc)
    return redirect("/")
```

**Rationale**:

- Prevents clearing Redis when Sentinel is authorized
- More defensive but adds complexity

### **Option 3: Change Modal Redirect**

**Current**: Login modal redirects to `/logout` after completion
**Change**: Redirect directly to target page (`/alerts`, `/`, etc.)

**Issue**: This doesn't solve the root problem—any accidental GET to `/logout` will still wipe cache

---

## Recommended Fix

**Apply Option 1**: Remove `_invalidate_session()` call from GET handler entirely.

**File**: `ui/app.py`
**Lines**: 1006-1022

**Before**:

```python
if request.method == "GET":
    try:
        if _resolve_session_path and _invalidate_session:
            session_path = _resolve_session_path()
            _invalidate_session(session_path)  # ❌ Remove this
        flask_session.pop("telegram_authenticated", None)
        flask_session.pop("ui_locked", None)
    except Exception as clear_exc:
        logger.debug("Session clear during GET /logout: %s", clear_exc)
    return redirect("/")
```

**After**:

```python
if request.method == "GET":
    try:
        # Only clear Flask session markers - Redis cleanup handled by POST logout
        flask_session.pop("telegram_authenticated", None)
        flask_session.pop("ui_locked", None)
    except Exception as clear_exc:
        logger.debug("Session clear during GET /logout: %s", clear_exc)
    return redirect("/")
```

**Why This Works**:

1. POST `/api/session/logout` already handles complete cleanup:
   - Calls `_invalidate_session()` ✅
   - Publishes logout event to Sentinel ✅
   - Clears Redis properly ✅
2. GET `/logout` becomes purely navigational (shows login page)
3. No race condition between avatar publication and deletion
4. Both first login and relogin work identically

---

## Testing Verification Plan

After applying fix:

1. **Test First Login** (baseline):

   ```bash
   docker compose down -v
   docker compose build
   docker compose up -d
   # Upload session → verify avatar appears
   ```

2. **Test Relogin**:

   ```bash
   # From authenticated state
   # Click Logout button (triggers POST logout)
   # Wait for completion
   # Upload new session
   # Verify avatar appears immediately
   ```

3. **Verify Redis State**:

   ```bash
   # After successful relogin
   docker exec -it tgsentinel-redis-1 redis-cli
   > GET tgsentinel:user_info
   # Should show complete user data with avatar field
   > GET tgsentinel:user_avatar:7128032085
   # Should show base64 encoded image
   ```

4. **Check UI Logs**:

   ```bash
   docker compose logs ui | grep "user_info"
   # Should NOT see "[UI-REDIS] No user_info in Redis" after relogin
   ```

5. **Verify Navbar**:
   - Inspect browser DevTools → Network tab
   - `/api/session/info` should return `authorized: true` with valid avatar URL
   - `/api/avatar/user/{user_id}` should return 200 with image data

---

## Additional Notes

### **Current Workarounds (Not Recommended)**

1. Manual Redis key preservation
2. Increasing TTLs (doesn't solve root cause)
3. Server-side rendering with context processor (attempted but still affected by GET logout clearing cache)

### **Related Code That Works Correctly**

- **Channels/Users List**: Not affected because they use separate cache keys (`tgsentinel:cached_channels`, `tgsentinel:cached_users`) which are NOT cleared by `_invalidate_session()`
- **Avatar API Endpoint**: `/api/avatar/user/{id}` correctly serves from Redis when key exists

### **Architecture Notes**

- This issue demonstrates importance of **idempotent navigation routes**
- GET requests should never trigger destructive operations
- State management must clearly separate:
  - **UI state** (Flask session)
  - **Backend state** (Redis, Sentinel)
  - **Navigation** (routing/redirects)

---

## Conclusion

The avatar loading mechanism is architecturally sound. The bug is a **single misplaced cache-clearing operation** in the GET `/logout` route handler. The fix is straightforward and surgical: remove the `_invalidate_session()` call from the GET branch, allowing the POST handler to remain responsible for all cleanup operations.

**Impact**: ✅ Fixes avatar display on relogin while maintaining proper cleanup on logout
**Risk**: ✨ Minimal - GET handler becomes purely navigational (as it should be)
**Complexity**: 🟢 Simple 3-line change

**Status**: ✅ **IMPLEMENTED** - Applied to `ui/app.py` lines 1008-1024

---

## Implementation Details

**File**: `ui/app.py`
**Lines**: 1008-1024
**Change**: Removed `_invalidate_session()` call from GET `/logout` handler

**Modified Code**:

```python
if request.method == "GET":
    # Browser navigation - ONLY clear Flask session markers
    # Redis cleanup is handled by POST /api/session/logout to avoid
    # race conditions where navigation accidentally wipes fresh login data
    try:
        from flask import session as flask_session
        flask_session.pop("telegram_authenticated", None)
        flask_session.pop("ui_locked", None)
    except Exception as clear_exc:
        logger.debug("Session clear during GET /logout: %s", clear_exc)
    return redirect("/")
```

**What Changed**:

- ❌ Removed: `_invalidate_session(session_path)` call
- ❌ Removed: `_resolve_session_path()` call
- ✅ Kept: Flask session marker clearing (`telegram_authenticated`, `ui_locked`)
- ✅ Added: Comment explaining why Redis cleanup is NOT done here

**Why This Is Safe**:

1. POST `/api/session/logout` (lines 343-450 in `ui/routes/session.py`) already handles complete cleanup:
   - Calls `_invalidate_session()` to clear Redis `tgsentinel:user_info` ✅
   - Publishes `session_logout` event to Sentinel ✅
   - Updates `worker_status` to `logged_out` ✅
   - Clears Flask session markers ✅
2. GET `/logout` becomes purely navigational (idempotent):

   - Only clears UI-side Flask session state
   - Does NOT touch Redis or backend state
   - Safe to call multiple times without side effects

3. No risk of "Unable to refresh session information":
   - After real logout: POST endpoint clears everything properly
   - After new login: GET navigation won't wipe the fresh `user_info`
   - `tgsentinel:user_info` always reflects current Sentinel state

**Testing Required**:
Follow the verification plan above to confirm:

- First login still works (baseline)
- Relogin now displays avatar correctly
- Logout still cleans up properly
- No stale Redis keys after logout
