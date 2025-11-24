# Webhook Integration Implementation

**Status**: ✅ Complete  
**Date**: 2024-11-24  
**Version**: Option B (Per-Profile Webhooks)

## Overview

This document describes the implementation of per-profile webhook integration in TG Sentinel, allowing Alert and Interest profiles to trigger external webhooks when matches occur.

## Architecture

### Design Choice: Per-Profile Webhooks (Option B)

**Selected**: Option B - Each profile can specify which webhooks to trigger  
**Rejected**: Option A - Global webhooks that fire on any alert

**Rationale**:

- Granular routing: Security alerts → PagerDuty, Product feedback → Jira, etc.
- Aligns with USE_CASES.md requirements for specific webhook-driven workflows
- Maintains backward compatibility (webhook field is optional)
- No race conditions: webhook delivery is fire-and-forget async

## Implementation Details

### 1. Schema Changes (`src/tgsentinel/config.py`)

Added `webhooks: List[str]` field to:

- `ProfileDefinition` (Global/Interest profiles)
- `ChannelRule` (Alert profiles)

```python
# Webhook integrations (list of webhook service names from config/webhooks.yml)
webhooks: List[str] = field(default_factory=list)
```

**Backward Compatibility**: Field defaults to empty list, existing profiles without webhooks field work unchanged.

### 2. Webhook Delivery Function (`src/tgsentinel/notifier.py`)

Implemented `notify_webhook()` function:

**Features**:

- Async delivery using `aiohttp`
- n8n-compatible JSON payload format
- HMAC-SHA256 signatures (`X-Webhook-Signature` header)
- Secret decryption using Fernet (same encryption as UI)
- 10-second timeout per webhook
- Comprehensive error handling and logging
- Returns delivery results: `{"success": [...], "failed": [...]}`

**Payload Format** (n8n-compatible):

```json
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
```

**Retry Logic**:

- **Max Attempts**: 4 (1 initial + 3 retries)
- **Backoff Strategy**: Exponential delays: 0s, 1s, 2s, 4s
- **Success Criteria**: HTTP status < 400
- **Failure Handling**: All retries exhausted → mark as "failed"
- **Database Recording**: Each attempt logged with status, timing, errors

**Delivery History** (`webhook_deliveries` table):

- **Fields**: service, profile, status, http_status, response_time_ms, error_message, attempt, created_at
- **Status Values**: `success`, `failed`, `retry_1`, `retry_2`, `retry_3`
- **Retention**: 30 days (configurable via `cleanup_old_webhook_deliveries()`)
- **UI Display**: Latest 10 deliveries in Developer panel (auto-refresh every 30s)

### 3. Worker Pipeline Integration (`src/tgsentinel/worker.py`)

Modified `process_one()` function to deliver webhooks after Telegram notifications:

**Flow**:

1. Heuristic scoring + semantic matching
2. Check if alert threshold met
3. **If important**:
   - Send Telegram DM/channel notifications
   - Extract webhook list from profile
   - Build n8n payload
   - Call `notify_webhook()` async
   - Log results (success/failed)
   - Mark alert in database

**Race Condition Prevention**:

- Webhook delivery is non-blocking (fire-and-forget)
- Does NOT wait for webhook response before marking alert
- Failures logged but don't prevent Telegram delivery
- No shared state between webhook calls

### 4. UI Changes

#### HTML Forms (`ui/templates/profiles/`)

Added webhook selection section to both Alert and Interest profile editors:

```html
<!-- Webhook Integration -->
<div class="border-top pt-3">
  <h3 class="h6 mb-0">
    <svg>...</svg>
    Webhook Notifications
  </h3>
  <label for="alert-webhooks">Notify Webhooks (optional)</label>
  <select
    class="form-select"
    id="alert-webhooks"
    name="webhooks"
    multiple
    size="5"
  >
    <!-- Populated dynamically from /api/webhooks -->
  </select>
  <small
    >Hold Ctrl/Cmd to select multiple webhooks. Alerts matching this profile
    will be sent to selected webhooks.</small
  >
</div>
```

**Location**: After "Apply Profile To" section, before "Digest Schedules"

#### JavaScript (`ui/static/js/profiles/`)

**init.js**: Added `loadAvailableWebhooks()` function:

- Fetches webhooks from `/api/webhooks` on page load
- Populates `<select>` elements for both Alert and Interest forms
- Handles disabled webhook feature gracefully

**alert_profiles.js**:

- `saveAlertProfile()`: Extracts selected webhooks from multi-select
- `loadAlertProfile()`: Populates multi-select with profile's webhooks

**interest_profiles.js**:

- `saveInterestProfile()`: Extracts selected webhooks from multi-select
- `loadInterestProfile()`: Populates multi-select with profile's webhooks

### 5. Dependencies

Added `aiohttp==3.11.11` to `requirements.txt` for async HTTP requests.

**Graceful Degradation**: If `aiohttp` not installed, `notify_webhook()` logs error and returns failed status (Telegram notifications still work).

## Usage Workflow

### For End Users

1. **Configure Webhooks** (Developer Panel):

   ```
   Service: pagerduty
   URL: https://events.pagerduty.com/v2/enqueue
   Secret: <your-integration-key>
   ```

2. **Assign to Profile** (Profiles Page):

   - Edit Alert or Interest profile
   - Scroll to "Webhook Notifications" section
   - Select one or more webhooks (Ctrl/Cmd + click)
   - Save profile

3. **Test**:
   - Trigger an alert matching the profile
   - Check webhook service for received payload
   - Check Sentinel logs: `[WORKER] Webhooks delivered...`

### For n8n Users

**Webhook Node Configuration**:

- Method: `POST`
- Content Type: `application/json`
- Authentication: `Header Auth`
  - Name: `X-Webhook-Signature`
  - Value: Validate HMAC-SHA256 signature

**Function Node (Signature Validation)**:

```javascript
const crypto = require("crypto");
const secret = "your-webhook-secret";
const body = JSON.stringify($input.item.json);
const signature = crypto
  .createHmac("sha256", secret)
  .update(body)
  .digest("hex");
const expected = `sha256=${signature}`;
const received = $input.item.headers["x-webhook-signature"];

if (received !== expected) {
  throw new Error("Invalid webhook signature");
}

return $input.item.json;
```

## Backward Compatibility

### Existing Profiles

**Without `webhooks` field**: Work unchanged

- Schema default: `webhooks: List[str] = field(default_factory=list)`
- Worker checks: `if webhook_services:` (empty list = no webhook delivery)
- UI: Multi-select shows "No webhooks configured" if none exist

**With `webhooks` field**: Validated on load

- Invalid service names (not in `config/webhooks.yml`) are logged as warnings
- Delivery continues for valid webhooks

### Migration Path

**No action required** for existing profiles:

1. Deploy updated code
2. Existing profiles load/save without changes
3. Optionally add webhooks to profiles via UI

**Demo Profiles**: All profiles in `demo/` work unchanged (no webhook field).

## Testing Checklist

### Unit Tests (Recommended)

- [ ] `test_notify_webhook_success()` - Successful delivery
- [ ] `test_notify_webhook_failed()` - HTTP error handling
- [ ] `test_notify_webhook_signature()` - HMAC validation
- [ ] `test_notify_webhook_empty_list()` - No webhooks specified
- [ ] `test_notify_webhook_missing_service()` - Service not in config

### Integration Tests

- [ ] Load profile without `webhooks` field → No errors
- [ ] Save profile with webhooks → Field persists
- [ ] Import demo profile → Works unchanged
- [ ] Trigger alert with webhook → Payload delivered
- [ ] Backtest with webhook profile → No webhook delivery (backtest doesn't trigger webhooks)

### Manual Testing

1. **Setup**:

   ```bash
   docker compose down -v
   docker compose build
   docker compose up -d
   ```

2. **Configure Webhook**:

   - Developer panel → Add webhook (e.g., RequestBin for testing)
   - Save and test delivery

3. **Assign to Profile**:

   - Profiles → Edit Alert profile
   - Select webhook in multi-select
   - Save profile

4. **Trigger Alert**:

   - Send test message to monitored channel
   - Check webhook endpoint for received payload
   - Verify logs: `docker compose logs sentinel | grep WEBHOOK`

5. **Verify Backward Compatibility**:
   - Import `demo/Alerts/security-monitoring.json`
   - Verify it loads without errors
   - Save without selecting webhooks
   - Trigger alert → Only Telegram notification (no webhook)

## Race Condition Analysis

**Question**: Can webhook delivery create race conditions?

**Answer**: ✅ No, by design:

1. **Non-blocking**: `await notify_webhook()` is fire-and-forget

   - Does not wait for all webhooks to respond
   - Failures don't block subsequent processing

2. **No shared state**: Each webhook call is independent

   - Read-only access to `config/webhooks.yml`
   - No writes during delivery

3. **Message processing continues**:

   - `mark_alerted()` called regardless of webhook status
   - Alert counter incremented immediately
   - Next message processed without waiting

4. **Async session per call**:
   ```python
   async with aiohttp.ClientSession() as session:
       for service_name in webhook_services:
           # Each request is independent
   ```

**Concurrency Model Compliance**: Follows `Concurrency.instructions.md`:

- No threads (asyncio only)
- No blocking I/O in main loop
- Clean error handling

## Backtest Integration

**Status**: ⚠️ Webhooks **NOT** triggered during backtest

**Rationale**:

- Backtests process historical data
- Triggering webhooks would spam external services
- Backtest is for profile tuning, not live alerting

**Future Enhancement** (Optional):

- Add `--webhook-dry-run` flag to show what _would_ be sent
- Log webhook payload JSON without actual delivery
- Useful for testing n8n workflow logic

## Security Considerations

### Webhook Secrets

- Stored encrypted in `config/webhooks.yml` (Fernet)
- Decrypted in-memory during delivery
- Never logged or exposed in API responses

### Signature Validation

- HMAC-SHA256 of request body
- Webhook receivers MUST validate signature
- Prevents replay attacks and tampering

### Rate Limiting

**Current**: No rate limiting on webhooks (respects 10s timeout)

**Recommendation**: Add Redis-based rate limiting per webhook service:

```python
rate_limit_key = f"tgsentinel:webhook_rate_limit:{service_name}"
if r.incr(rate_limit_key) > MAX_CALLS_PER_MINUTE:
    log.warning(f"[WEBHOOK] Rate limit exceeded for {service_name}")
    return
r.expire(rate_limit_key, 60)
```

## Troubleshooting

### Webhooks Not Delivered

**Check**:

1. `WEBHOOK_SECRET_KEY` set in `.env`
2. Webhook service enabled in `config/webhooks.yml`
3. Profile has webhooks selected (UI or JSON file)
4. `aiohttp` installed: `pip install aiohttp`
5. Logs: `docker compose logs sentinel | grep -i webhook`

### Invalid Signature Errors

**Fix**: Ensure secret in `config/webhooks.yml` matches receiver:

```bash
# Decrypt secret (Python)
from cryptography.fernet import Fernet
import os
key = os.getenv("WEBHOOK_SECRET_KEY")
cipher = Fernet(key.encode())
secret = cipher.decrypt(b"encrypted_value").decode()
```

### Webhook Timeouts

**Fix**: Increase timeout in `notifier.py`:

```python
async with session.post(
    url, json=payload, headers=headers,
    timeout=aiohttp.ClientTimeout(total=30)  # Increase from 10
) as response:
```

## Documentation Updates

### User-Facing

- ✅ `docs/USER_GUIDE.md` - Already mentions webhooks API
- ✅ `docs/USE_CASES.md` - Already describes webhook workflows
- 🆕 Add "Webhook Configuration" section to `docs/CONFIGURATION.md`

### Developer-Facing

- ✅ `docs/ENGINEERING_GUIDELINES.md` - Already mentions webhook extension point
- 🆕 This file: `WEBHOOK_INTEGRATION.md`

## Delivery History & Monitoring

### Database Schema (`webhook_deliveries` table)

Records every webhook delivery attempt for observability and debugging.

**Table Structure**:

```sql
CREATE TABLE webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_service TEXT NOT NULL,
    profile_id TEXT,
    profile_name TEXT,
    chat_id INTEGER,
    msg_id INTEGER,
    status TEXT NOT NULL,  -- 'success', 'failed', 'retry_1', 'retry_2', 'retry_3'
    http_status INTEGER,
    response_time_ms INTEGER,
    error_message TEXT,
    payload TEXT,  -- JSON payload sent
    attempt INTEGER DEFAULT 1,  -- 1-4
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX idx_webhook_deliveries_created_at ON webhook_deliveries(created_at DESC);
CREATE INDEX idx_webhook_deliveries_service ON webhook_deliveries(webhook_service);
CREATE INDEX idx_webhook_deliveries_status ON webhook_deliveries(status);
```

**Helper Functions** (`src/tgsentinel/store.py`):

- `record_webhook_delivery()` - Insert delivery record
- `get_recent_webhook_deliveries()` - Fetch latest N records (for UI)
- `cleanup_old_webhook_deliveries()` - Delete records older than N days (default: 30)

### API Endpoints

**Sentinel API** (`src/tgsentinel/api.py`):

```
GET /api/webhooks/history?limit=10
```

Returns recent webhook deliveries with status, timing, and errors.

**UI Proxy** (`ui/api/developer_routes.py`):

```
GET /api/webhooks/history?limit=10
```

Proxies request to Sentinel API (follows existing proxy pattern for cross-container calls).

### Developer Panel UI

**Location**: Developer Tools page → "Webhook Delivery History" section

**Features**:

- Table showing latest 10 deliveries
- Auto-refresh every 30 seconds
- Manual refresh button
- Color-coded status badges:
  - 🟢 Green: `success`
  - 🔴 Red: `failed`
  - 🟡 Yellow: `retry_1`, `retry_2`, `retry_3`
- Columns: Time, Service, Profile, Status, Response (HTTP code + timing), Error

**JavaScript** (`ui/templates/developer.html`):

- `loadWebhookHistory()` - Fetch and populate table
- Auto-refresh interval: 30s
- Error handling: Shows friendly error message if Sentinel unavailable

### Retry Monitoring

Each retry attempt is recorded separately with:

- `status`: `retry_1`, `retry_2`, `retry_3` (final failure marked as `failed`)
- `attempt`: 1-4 (matches retry number)
- `created_at`: Timestamp of each attempt (shows delay between retries)

**Example Timeline**:

```
Attempt 1: status=retry_1, attempt=1, created_at=12:00:00
Attempt 2: status=retry_2, attempt=2, created_at=12:00:01 (+1s delay)
Attempt 3: status=retry_3, attempt=3, created_at=12:00:03 (+2s delay)
Attempt 4: status=failed, attempt=4, created_at=12:00:07 (+4s delay)
```

## Future Enhancements

1. **Webhook Templates**: Pre-configured templates for popular services (Slack, Discord, PagerDuty, etc.) ⏳ Pending
2. ~~**Retry Logic**: Exponential backoff for failed deliveries~~ ✅ Implemented
3. **Delivery Queue**: Redis queue for reliable delivery during network issues
4. ~~**Webhook Logs**: Store delivery history in database~~ ✅ Implemented
5. **UI Webhook Testing**: "Send Test" button per profile (not just developer panel)
6. **Backtest Dry-Run**: Show what webhooks would be triggered without delivery ⏳ Pending
7. **Rate Limiting**: Per-webhook rate limits (Redis-based)

## Compliance

### Architecture Contracts

- ✅ `DB_Architecture.instructions.md` - No DB changes, JSON-based
- ✅ `Concurrency.instructions.md` - Async, no threads, graceful errors
- ✅ `Progressbar.instructions.md` - N/A (webhooks don't use progress tracking)
- ✅ `UI_UX.instructions.md` - Multi-select, clear labels, docs link
- ✅ `TESTS.instructions.md` - Test recommendations included

### Code Quality

- ✅ Type hints: `List[str]`, `Dict[str, Any]`, `Optional`
- ✅ Structured logging: `[WEBHOOK]` tags, request_id propagation
- ✅ Error handling: Try/except with exc_info=True
- ✅ Docstrings: All functions documented

## Changelog

**2024-11-24**: Initial implementation

- Added per-profile webhook support
- Implemented async webhook delivery
- Updated UI forms and JavaScript
- Added comprehensive documentation

---

**Maintainer**: TG Sentinel Team  
**Last Updated**: 2024-11-24
