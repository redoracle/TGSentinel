# TG Sentinel Dual-Database Architecture

## Implementation Summary

This document describes the dual-database architecture implementation for TG Sentinel, establishing clear separation of concerns between the UI and Sentinel worker services.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Docker Network                              │
│                         (tgsentinel_net)                             │
│                                                                       │
│  ┌──────────────────────┐              ┌──────────────────────────┐ │
│  │   UI Container       │              │  Sentinel Container      │ │
│  │                      │              │                          │ │
│  │  Flask App           │──HTTP/JSON──▶│  Telethon Worker        │ │
│  │  (ui/app.py)         │              │  HTTP API (port 8080)   │ │
│  │                      │              │                          │ │
│  │  Volume:             │              │  Volume:                 │ │
│  │  tgsentinel_ui_data  │              │  tgsentinel_sentinel_data│ │
│  │  └─ ui.db            │              │  └─ tgsentinel.session  │ │
│  │                      │              │  └─ sentinel.db         │ │
│  └──────────────────────┘              └──────────────────────────┘ │
│            │                                       │                 │
│            └───────────────┬───────────────────────┘                 │
│                            │                                         │
│                  ┌─────────▼─────────┐                              │
│                  │   Redis Container │                              │
│                  │  Volume:          │                              │
│                  │  tgsentinel_redis │                              │
│                  └───────────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Database Ownership

### 1. UI Database (`ui.db`)

- **Owner**: UI service only
- **Location**: `/app/data/ui.db` in `tgsentinel_ui_data` volume
- **Purpose**: UI-specific state management
- **Schema**:
  - `settings` - UI configuration key-value pairs
  - `alerts` - Cached alerts from sentinel
  - `profiles` - User-defined scoring profiles
  - `digest_runs` - Digest execution history
  - `audit_log` - UI action audit trail
  - `ui_sessions` - UI authentication sessions
  - `schema_migrations` - Migration version tracking

### 2. Sentinel Application DB (`sentinel.db`)

- **Owner**: Sentinel service only
- **Location**: `/app/data/sentinel.db` in `tgsentinel_sentinel_data` volume
- **Purpose**: Message ingestion, scoring, and alerts
- **Access**: UI has read-only connection for compatibility (optional)

### 3. Telethon Session File (`tgsentinel.session`)

- **Owner**: Sentinel service only (via Telethon)
- **Location**: `/app/data/tgsentinel.session` in `tgsentinel_sentinel_data` volume
- **Purpose**: Telegram MTProto authentication state
- **Critical**: UI must NEVER directly access this file

---

## HTTP API Contract

### Sentinel Worker API (port 8080)

#### `GET /api/health`

Health check endpoint.

**Response**:

```json
{
  "status": "ok",
  "service": "tgsentinel",
  "timestamp": "2025-11-16T10:00:00Z"
}
```

#### `GET /api/status`

Worker status and authorization state.

**Response**:

```json
{
  "status": "ok",
  "data": {
    "authorized": true,
    "connected": true,
    "user_info": {
      "username": "analyst",
      "first_name": "John",
      "user_id": 123456789
    },
    "last_sync": "2025-11-16T09:55:00Z"
  },
  "error": null
}
```

#### `POST /api/session/import`

Import a Telethon session file from the UI.

**Request** (JSON with base64):

```json
{
  "session_data": "U1FMaXRlIGZvcm1hdCAzAA..."
}
```

**Request** (multipart/form-data):

```
Content-Type: multipart/form-data; boundary=----WebKitFormBoundary
------WebKitFormBoundary
Content-Disposition: form-data; name="session_file"; filename="session.session"

<binary session file content>
------WebKitFormBoundary--
```

**Success Response** (200):

```json
{
  "status": "ok",
  "message": "Session imported successfully",
  "data": {
    "session_path": "/app/data/tgsentinel.session",
    "size": 32768,
    "imported_at": "2025-11-16T10:00:00Z"
  },
  "error": null
}
```

**Error Response** (400):

```json
{
  "status": "error",
  "code": "INVALID_SESSION_FILE",
  "message": "Missing required Telethon tables: sessions",
  "data": null,
  "error": {
    "code": "INVALID_SESSION_FILE",
    "message": "Missing required Telethon tables: sessions"
  }
}
```

#### `GET /api/alerts`

Get recent alerts (placeholder - can be extended).

#### `GET /api/stats`

Get worker statistics (placeholder - can be extended).

---

## Session Upload Flow

### Current Implementation

1. **Browser → UI**: User uploads `.session` file to `/api/session/upload`
2. **UI validates**: Check file size, SQLite format, Telethon tables
3. **UI → Sentinel**: Forward via HTTP POST to `http://sentinel:8080/api/session/import`
4. **Sentinel receives**: Validate, write to `/app/data/tgsentinel.session`
5. **Sentinel reinitializes**: Reload Telethon client with new session
6. **Sentinel → Redis**: Publish `session_updated` event
7. **UI waits**: Poll for worker authorization (60s timeout)
8. **Browser receives**: Success or error response with redirect

### Key Points

- UI **never** writes directly to the session file
- UI **never** mounts the sentinel data volume
- All session management flows through the sentinel HTTP API
- Proper validation at both UI and sentinel layers
- Atomic file writes with proper permissions (0o660)

---

## Environment Variables

### Sentinel Worker

```bash
# Database paths
DB_URI=sqlite:////app/data/sentinel.db
TG_SESSION_PATH=/app/data/tgsentinel.session

# API configuration
SENTINEL_API_PORT=8080

# Telegram credentials
TG_API_ID=<your_api_id>
TG_API_HASH=<your_api_hash>
```

### UI Service

```bash
# UI database
UI_DB_URI=sqlite:////app/data/ui.db

# Sentinel API endpoint (for forwarding requests)
SENTINEL_API_BASE_URL=http://sentinel:8080/api

# UI security
UI_SECRET_KEY=<random_secret_key>
UI_PORT=5000
```

### Shared (Both Services)

```bash
# Redis connection
REDIS_HOST=redis
REDIS_PORT=6379

# Telegram credentials (for validation/fingerprinting)
TG_API_ID=<your_api_id>
TG_API_HASH=<your_api_hash>
```

---

## Docker Compose Configuration

```yaml
volumes:
  tgsentinel_redis_data:
    driver: local
  tgsentinel_sentinel_data:
    driver: local
  tgsentinel_ui_data:
    driver: local

networks:
  tgsentinel_net:
    driver: bridge

services:
  sentinel:
    volumes:
      - ./config:/app/config:ro
      - tgsentinel_sentinel_data:/app/data
    environment:
      - TG_SESSION_PATH=/app/data/tgsentinel.session
      - DB_URI=sqlite:////app/data/sentinel.db
      - SENTINEL_API_PORT=8080
    networks:
      - tgsentinel_net

  ui:
    volumes:
      - ./config:/app/config:ro
      - tgsentinel_ui_data:/app/data
    environment:
      - UI_DB_URI=sqlite:////app/data/ui.db
      - SENTINEL_API_BASE_URL=http://sentinel:8080/api
    networks:
      - tgsentinel_net
```

---

## Security & Robustness

### File Permissions

- **Session file**: `0o660` (rw-rw----)
- **Database files**: `0o664` (rw-rw-r--)
- **Data directories**: `0o777` (rwxrwxrwx) for container compatibility

### Input Validation

- **File size**: Max 10MB for session uploads
- **File format**: SQLite header check (`SQLite format 3\x00`)
- **Telethon structure**: Required tables validation
- **Auth key**: Presence check in sessions table

### Error Handling

- All API endpoints return JSON (never HTML)
- Consistent error envelope structure
- Structured logging with correlation
- Timeout handling (30s for HTTP, 60s for auth)
- Proper HTTP status codes (200, 400, 404, 500, 502, 503, 504)

### Rate Limiting

- Implemented at sentinel level
- Stored in Redis with TTL
- Visible to UI via worker status
- Prevents flood/abuse scenarios

---

## Debugging Checklist

### 1. Verify Endpoints from UI Container

```bash
docker exec -it tgsentinel-ui-1 sh
apk add curl
curl -v http://sentinel:8080/api/health
curl -v http://sentinel:8080/api/status
```

### 2. Confirm Database Files Exist

```bash
# UI container - should have ui.db only
docker exec tgsentinel-ui-1 ls -lah /app/data/

# Sentinel container - should have sentinel.db and tgsentinel.session
docker exec tgsentinel-sentinel-1 ls -lah /app/data/
```

### 3. Check Volume Isolation

```bash
# List volumes
docker volume ls | grep tgsentinel

# Inspect volume mounts
docker inspect tgsentinel-ui-1 | grep -A 10 Mounts
docker inspect tgsentinel-sentinel-1 | grep -A 10 Mounts
```

### 4. Monitor Logs

```bash
# UI logs (session upload flow)
docker logs -f tgsentinel-ui-1 | grep -i "session\|upload\|forward"

# Sentinel logs (API and session import)
docker logs -f tgsentinel-sentinel-1 | grep -i "api\|session\|import"
```

### 5. Test Session Upload Flow

```bash
# From browser or curl
curl -X POST http://localhost:5001/api/session/upload \
  -F "session_file=@my_dutch.session" \
  -H "Cookie: session=<your_session_cookie>"
```

---

## Migration Path

### Before (Problematic Pattern)

```yaml
# Both containers share the same volume
services:
  sentinel:
    volumes:
      - ./data:/app/data
  ui:
    volumes:
      - ./data:/app/data
```

Problems:

- Both containers can access `tgsentinel.session`
- No clear ownership boundaries
- UI writes directly to filesystem
- Race conditions on shared files
- Permission conflicts

### After (Clean Separation)

```yaml
# Separate volumes per service
services:
  sentinel:
    volumes:
      - tgsentinel_sentinel_data:/app/data
  ui:
    volumes:
      - tgsentinel_ui_data:/app/data
```

Benefits:

- Clear ownership: sentinel owns session file
- UI uses HTTP API for communication
- No filesystem sharing between services
- Proper encapsulation and security
- Easier to debug and maintain

---

## Files Modified

1. **docker-compose.yml** - Separate volumes and networks
2. **ui/database.py** - New UI database module
3. **src/tgsentinel/api.py** - New HTTP API server
4. **src/tgsentinel/main.py** - API server integration
5. **ui/app.py** - Session upload forwarding to sentinel
6. **.env** - New environment variables
7. **config/tgsentinel.yml** - Session path configuration

---

## Testing the Implementation

### 1. Clean Start

```bash
# Remove old data
rm -rf data/redis data/sentinel.db data/tgsentinel.session

# Remove old Docker volumes
docker compose down -v

# Rebuild and start
docker compose build
docker compose up -d
```

### 2. Upload Session File

1. Navigate to `http://localhost:5001`
2. Click "Upload Session" or similar UI control
3. Select your `.session` file
4. Wait for upload confirmation

### 3. Verify Logs

```bash
# UI should show forwarding to sentinel
docker logs tgsentinel-ui-1 --tail 20

# Sentinel should show session import and API activity
docker logs tgsentinel-sentinel-1 --tail 50
```

### 4. Check Authorization

```bash
# Query sentinel status
curl http://localhost:8080/api/status

# Should return authorized: true after successful import
```

---

## Common Issues & Fixes

### Issue: "HTTP 404 - Invalid response format"

**Cause**: UI trying to upload to non-existent endpoint

**Fix**: Ensure `SENTINEL_API_BASE_URL=http://sentinel:8080/api` is set

### Issue: "Connection refused to sentinel:8080"

**Cause**: Sentinel API server not started

**Fix**: Check sentinel logs for API startup message, verify `SENTINEL_API_PORT`

### Issue: "Unable to open database file"

**Cause**: Volume mount issues or permission problems

**Fix**: Verify volume exists, check container user permissions

### Issue: "Session imported but worker not authorized"

**Cause**: Session file valid but not authorized on Telegram

**Fix**: Check sentinel logs for Telegram connection errors, may need fresh session

---

## Future Enhancements

1. **UI DB Migrations**: Implement versioned schema migrations
2. **Alert Caching**: Populate UI DB with alerts from sentinel
3. **Sentinel Stats API**: Expose detailed metrics via HTTP
4. **WebSocket for Real-time**: Replace polling with WebSocket updates
5. **API Authentication**: Add token-based auth between UI and sentinel
6. **Health Checks**: Implement Docker HEALTHCHECK directives
7. **Backup/Restore**: UI for backing up session and databases

---

## Conclusion

The dual-database architecture establishes clear boundaries between UI and Sentinel responsibilities:

- **UI** owns presentation logic, user settings, and cached data
- **Sentinel** owns Telegram authentication, message processing, and scoring
- **HTTP API** provides clean, well-defined communication channel
- **Separate volumes** enforce isolation and security

This architecture is production-ready, maintainable, and follows best practices for containerized microservices.
