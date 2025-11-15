# Single-Owner Session Architecture

## Overview

TGSentinel follows the **Single Owner Process** pattern for Telegram session management, ensuring SQLite session database integrity and preventing concurrent write conflicts.

## Architecture Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                    User Authentication Flow                  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    UI Container (Flask)                      │
│  • Collects credentials (phone, code, password)             │
│  • NO TelegramClient instances                              │
│  • NO session DB access                                     │
│  • Submits credentials via Redis                            │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ Redis IPC
                           │ Key: "tgsentinel:auth_queue"
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              Sentinel Container (Telethon)                   │
│  • SOLE owner of session SQLite DB                          │
│  • EXCLUSIVE TelegramClient instance                        │
│  • Processes auth requests from Redis                       │
│  • Performs all sign_in() operations                        │
│  • Saves session every 60 seconds                           │
│  • Saves session before disconnect                          │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              Session Database (SQLite)                       │
│  Path: /app/data/tgsentinel.session                         │
│  Owner: sentinel container ONLY                             │
│  Mode: WAL (Write-Ahead Logging)                            │
│  Permissions: 0o666 (read: anyone, write: sentinel)         │
└─────────────────────────────────────────────────────────────┘
```

## Key Principles

### 1. Single Writer Pattern

- **Sentinel** is the ONLY process that creates TelegramClient instances
- **Sentinel** is the ONLY process that writes to the session SQLite database
- All other processes communicate via Redis IPC

### 2. Credential Delegation

- **UI** collects user credentials (phone, code, 2FA password)
- **UI** writes credentials to Redis key: `tgsentinel:auth_queue`
- **Sentinel** reads credentials from Redis
- **Sentinel** performs `client.sign_in()` operations
- **Sentinel** validates with `client.get_me()`
- **Sentinel** marks authorization status in Redis: `tgsentinel:worker_status`

### 3. Session Persistence

- Session saved every 60 seconds during operation (periodic handler)
- Session saved before all `client.disconnect()` calls
- Session saved during graceful shutdown
- File permissions: `umask(0o000)` set at module TOP before imports

## API Delegation Patterns

### Authentication Flow

```
UI: POST /api/session/login/start
  → Redis: tgsentinel:auth_queue (action: "start")
  → Sentinel: Reads queue, calls client.send_code_request()
  → Redis: tgsentinel:auth_responses:{request_id}
  → UI: Returns phone_code_hash to user

UI: POST /api/session/login/verify
  → Redis: tgsentinel:auth_queue (action: "verify")
  → Sentinel: Reads queue, calls client.sign_in(code)
  → Sentinel: Validates with client.get_me()
  → Redis: tgsentinel:worker_status (authorized: true)
  → UI: Waits for authorization confirmation
```

### Telegram Data Access

```
UI: GET /api/telegram/chats
  → Redis: tgsentinel:request:get_dialogs:{request_id}
  → Sentinel: telegram_dialogs_handler() reads request
  → Sentinel: Calls client.get_dialogs()
  → Redis: tgsentinel:response:get_dialogs:{request_id}
  → UI: Returns chat list to user

UI: GET /api/telegram/users
  → Redis: tgsentinel:telegram_users_request:{request_id}
  → Sentinel: telegram_users_handler() reads request
  → Sentinel: Calls client.get_dialogs() + filters for Users
  → Redis: tgsentinel:telegram_users_response:{request_id}
  → UI: Returns user list
```

## Files Modified for Single-Owner Pattern

### UI Container (`ui/app.py`)

**Changes:**

- ✅ Removed `from telethon import TelegramClient` import
- ✅ Set `TelegramClient = None` to prevent accidental usage
- ✅ `/api/session/login/start`: Submit to Redis, not sign_in()
- ✅ `/api/session/login/verify`: Submit to Redis, wait for sentinel
- ✅ `/api/telegram/chats`: Delegate to sentinel via Redis (was direct TelegramClient)
- ✅ `/api/telegram/users`: Already uses Redis delegation pattern

**No TelegramClient Instances:**

```python
# OLD - WRONG (dual writer)
client = TelegramClient(session_path, api_id, api_hash)
await client.connect()
await client.sign_in(phone, code)

# NEW - CORRECT (single owner)
auth_data = {"phone": phone, "code": code, "phone_code_hash": hash}
redis_client.setex("tgsentinel:auth_queue", 300, json.dumps(auth_data))
# Wait for sentinel to process and mark authorized
```

### Sentinel Container (`src/tgsentinel/main.py`)

**Session Ownership:**

- ✅ Creates single TelegramClient via `make_client(cfg)`
- ✅ Processes auth queue: `_handle_auth_request()`
- ✅ Performs all `sign_in()` operations
- ✅ Periodic session persistence: `session_persistence_handler()`
- ✅ Session saved before disconnect in all code paths
- ✅ Handles Redis-delegated requests: dialogs, users, participants

**Session Persistence Points:**

```python
# 1. Periodic (every 60 seconds)
async def session_persistence_handler():
    while True:
        await asyncio.sleep(60)
        client.session.save()

# 2. Before disconnect
try:
    if hasattr(client, "session"):
        client.session.save()
    await client.disconnect()
except Exception:
    pass

# 3. Graceful shutdown
signal.signal(signal.SIGTERM, lambda: client.session.save())
```

### Client Factory (`src/tgsentinel/client.py`)

**Single Creation Point:**

```python
def make_client(cfg: AppCfg) -> TelegramClient:
    session_path = _resolve_session_path(cfg)
    client = TelegramClient(session_path, cfg.api_id, cfg.api_hash)

    # Ensure proper permissions (shared volume)
    if Path(session_path).exists():
        os.chmod(session_path, 0o666)

    return client
```

## Test Compliance

### Updated Tests

- ✅ `tests/test_ui_channels.py`: Uses Redis mocks, not TelegramClient
- ✅ `tests/test_ui_login_endpoints.py`: Mocks `_submit_auth_request()`
- ✅ `tests/test_telegram_users_api.py`: Uses Redis delegation pattern

### Test Pattern

```python
# OLD - WRONG (creates TelegramClient in test)
with patch("telethon.TelegramClient") as mock_tg:
    mock_client = MagicMock()
    mock_tg.return_value = mock_client
    response = client.get("/api/telegram/chats")

# NEW - CORRECT (mocks Redis delegation)
with patch("app.redis_client") as mock_redis:
    mock_redis.get.return_value = json.dumps({
        "status": "ok",
        "chats": [{"id": 123, "name": "Test"}]
    })
    response = client.get("/api/telegram/chats")
```

## Validation Checklist

### ✅ Single Owner Verified

- [x] Sentinel is ONLY process creating TelegramClient
- [x] UI has NO TelegramClient imports or instances
- [x] All authentication goes through sentinel
- [x] All Telegram API calls delegated via Redis

### ✅ Session Integrity

- [x] Session DB written by sentinel only
- [x] Session saved every 60 seconds
- [x] Session saved before all disconnects
- [x] umask(0o000) set at module TOP before imports
- [x] File permissions 0o666 for shared volume

### ✅ Redis IPC Patterns

- [x] Auth requests: UI → Redis → Sentinel
- [x] Dialogs requests: UI → Redis → Sentinel
- [x] Users requests: UI → Redis → Sentinel
- [x] Response timeout handling (30-60 seconds)
- [x] Request cleanup after processing

### ✅ Test Compliance

- [x] Tests use Redis mocks, not TelegramClient mocks
- [x] Tests verify delegation pattern, not direct access
- [x] No tests create TelegramClient instances in UI code

## Benefits of Single-Owner Pattern

### 1. SQLite Integrity

- No "database is locked" errors
- No concurrent write conflicts
- No session state corruption
- Proper WAL mode operation

### 2. Session Persistence

- No re-authentication after container restart
- Durable session across crashes
- Graceful handling of network interruptions
- User account not rate-limited by Telegram

### 3. Clean Architecture

- Clear separation of concerns
- UI = presentation layer only
- Sentinel = business logic + Telegram API
- Redis = IPC/message bus
- Easy to scale horizontally (multiple UI workers, one sentinel)

### 4. Security

- Single credential storage point (sentinel)
- No credential leaks in UI logs
- Session file access controlled
- Proper umask/permissions for shared volumes

## Troubleshooting

### "attempt to write a readonly database"

**Cause:** Multiple processes writing to session DB, or umask too restrictive
**Fix:** Ensure sentinel is sole owner; umask(0o000) at module top

### "database is locked"

**Cause:** Concurrent writes from UI and sentinel
**Fix:** Verify UI has NO TelegramClient instances

### Session not persisting

**Cause:** Session not saved before disconnect
**Fix:** All disconnect paths must call `client.session.save()` first

### Authentication loop (keeps asking for code)

**Cause:** Session not written to disk properly
**Fix:** Check periodic persistence handler is running; fsync after writes

## References

- [Telethon Session Management](https://docs.telethon.dev/en/stable/concepts/sessions.html)
- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [Best Practices: Single Writer Pattern](https://www.sqlite.org/whentouse.html)
- TGSentinel Engineering Guidelines: `docs/ENGINEERING_GUIDELINES.md`
