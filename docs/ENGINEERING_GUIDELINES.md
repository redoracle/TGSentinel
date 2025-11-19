# TG Sentinel — Engineering Guidelines

Audience: engineers and contributors maintaining or extending TG Sentinel.

This document describes the architecture, runtime components, configuration model, persistence, UI/API surface, and key development workflows.

---

## Table of Contents

- [System Overview](#system-overview)
- [Data Flow](#data-flow)
- [Configuration Model](#configuration-model)
- [Persistence](#persistence)
- [Heuristics and Semantic Scoring](#heuristics-and-semantic-scoring)
- [Notifications and Digests](#notifications-and-digests)
- [UI Architecture and API Surface](#ui-architecture-and-api-surface)
- [Redis and Caching](#redis-and-caching)
- [Running and Developing](#running-and-developing)
- [Performance and Tuning](#performance-and-tuning)
- [Extension Points](#extension-points)
- [Unified Profiles System Architecture](#unified-profiles-system-architecture)
- [Configuration Reload Mechanism](#configuration-reload-mechanism)
- [Troubleshooting](#troubleshooting)
- [Telegram Entity IDs](#telegram-entity-ids)
- [Security Notes](#security-notes)
- [Glossary](#glossary)

---

## System Overview

TG Sentinel monitors Telegram dialogs via a user session, filters messages with heuristics and optional semantic scoring, persists results, and delivers alerts/digests. A web UI surfaces health, alerts, and configuration.

Core components:

- **Ingestion**: Telethon client streams `NewMessage` events from the authenticated user session.
- **Queueing**: Redis Streams (`tgsentinel:messages`) decouple ingestion and processing.
- **Scoring**: Rule‑based heuristics plus optional sentence‑transformer embeddings for semantic similarity against "interests".
- **Persistence**: SQLite stores message metadata and feedback.
- **Delivery**: Telegram DMs or channel posts for alerts; scheduled digests.
- **UI**: Flask + Socket.IO dashboard for monitoring, configuration, and tools.

### Session Management (Single-Owner Pattern)

TG Sentinel implements a **Single-Owner Process** pattern to ensure SQLite session integrity:

- **Sentinel container**: SOLE owner of `tgsentinel.session` SQLite database

  - Exclusive TelegramClient instance
  - Performs all sign_in() operations
  - Saves session every 60 seconds + before disconnect
  - Processes auth requests from Redis queue

- **UI container**: ZERO direct session access

  - TelegramClient set to None (prevented from import)
  - All Telegram operations delegated via Redis IPC
  - Submits credentials to `tgsentinel:auth_queue`
  - Polls `tgsentinel:worker_status` for authorization

- **Redis delegation patterns**:
  - Authentication: UI → Redis → Sentinel → Telegram
  - Get dialogs: UI → Redis → Sentinel → client.get_dialogs()
  - Get users: UI → Redis → Sentinel → filtered dialogs
  - Participant info: UI → Redis → Sentinel → cached results

**Benefits**:

- No "database is locked" errors (single writer)
- No re-authentication loops (session persists)
- No session corruption (exclusive access)
- Scalable architecture (multiple UI workers, one sentinel)

**Session Persistence**:

- Periodic: Every 60 seconds during operation
- Pre-disconnect: Before all client.disconnect() calls
- Graceful shutdown: Signal handlers save session
- File permissions: umask(0o000) set at module top

**API Delegation Patterns**:

```markdown
Authentication Flow:
UI: POST /api/session/login/start
→ Redis: tgsentinel:auth_queue (action: "start")
→ Sentinel: client.send_code_request()
→ Redis: tgsentinel:auth_responses:{request_id}
→ UI: Returns phone_code_hash

UI: POST /api/session/login/verify
→ Redis: tgsentinel:auth_queue (action: "verify")
→ Sentinel: client.sign_in(code)
→ Sentinel: client.get_me() validation
→ Redis: tgsentinel:worker_status (authorized: true)
→ UI: Confirms login

Telegram Data Access:
UI: GET /api/telegram/chats
→ Redis: tgsentinel:request:get_dialogs:{request_id}
→ Sentinel: telegram_dialogs_handler()
→ Sentinel: client.get_dialogs()
→ Redis: tgsentinel:response:get_dialogs:{request_id}
→ UI: Returns chat list

UI: GET /api/telegram/users
→ Redis: tgsentinel:telegram_users_request:{request_id}
→ Sentinel: telegram_users_handler()
→ Sentinel: client.get_dialogs() + User filter
→ Redis: tgsentinel:telegram_users_response:{request_id}
→ UI: Returns user list
```

**Implementation Details**:

- **UI Container** (`ui/app.py`):

  - `TelegramClient = None` prevents accidental instantiation
  - All endpoints use Redis delegation (auth_queue, request/response pattern)
  - Response timeout: 30-60 seconds with polling
  - No direct session file access

- **Sentinel Container** (`src/tgsentinel/main.py`):

  - Single `TelegramClient` via `make_client(cfg)`
  - Auth queue handler: `_handle_auth_request()`
  - Dialog handler: `telegram_dialogs_handler()`
  - Session file: `/app/data/tgsentinel.session` (0o666 permissions)
  - Session saved: every 60s + before disconnect + on signals

- **Client Factory** (`src/tgsentinel/client.py`):
  - Single creation point ensures one instance
  - Sets file permissions: `os.chmod(session_path, 0o666)`
  - Returns configured TelegramClient

**Validation Checklist**:

```bash
# Verify UI has no TelegramClient usage
grep -r "TelegramClient(" ui/  # Should return nothing

# Verify sentinel owns session
docker compose logs sentinel | grep "TelegramClient"
# Should show single client creation

# Monitor Redis delegation
docker compose exec redis redis-cli KEYS "tgsentinel:*"
# Should show auth_queue, request/response keys

# Check session file ownership
docker compose exec sentinel ls -la /app/data/tgsentinel.session
# Should show: -rw-rw-rw- (0o666 permissions)
```

**Troubleshooting Session Issues**:

- **"database is locked"**:

  - Cause: Multiple processes writing to session DB
  - Fix: Verify UI has `TelegramClient = None`; check for rogue instances
  - Validation: `grep -r "from telethon import TelegramClient" ui/` should return nothing

- **"attempt to write a readonly database"**:

  - Cause: umask too restrictive or multiple writers
  - Fix: Ensure `umask(0o000)` at module top before imports
  - Validation: Check file permissions are 0o666

- **Session not persisting**:

  - Cause: Session not saved before disconnect
  - Fix: Verify periodic handler running; check all disconnect paths call `client.session.save()`
  - Validation: `docker compose logs sentinel | grep "session.save"` should show periodic saves

- **Authentication loop** (keeps asking for code):

  - Cause: Session not written to disk properly
  - Fix: Check periodic persistence handler; verify fsync after writes; restart sentinel
  - Validation: Session file should exist and have non-zero size after successful auth

- **Re-authentication after restart**:
  - Cause: Session file not persisted or corrupted
  - Fix: Check volume mounts in docker-compose.yml; verify `./data` is mounted rw
  - Validation: Session file should survive container restart

Repository layout:

- `src/tgsentinel/`: core runtime
  - `main.py`: orchestrator (client, digests, workers, metrics, participant info service).
  - `client.py`: Telethon client, avatar/chat‑type caching, Redis producer.
  - `worker.py`: Redis consumer, heuristics + semantic scorer, notifier, DB upsert.
  - `heuristics.py`: fast signal extraction (mentions, VIPs, keywords, reactions, replies).
  - `semantic.py`: optional embeddings model and interest vector scoring.
  - `store.py`: DB schema init, upsert/update helpers (SQLAlchemy).
  - `digest.py`: top‑N digest generator with message enrichment and links.
  - `config.py`: typed config loading with env‑var overrides.
  - `metrics.py`: log‑based counters.
  - `logging_setup.py`: consistent logging format/levels.
- `ui/`: Flask + Socket.IO app, templates, static assets.
- `config/`: YAML config, webhooks, developer settings.
- `data/`: session/db/avatars/profiles and runtime artifacts (shared by services).

---

## Data Flow

### 1. Telethon → client.py

- **Session Ownership**: Sentinel container exclusively owns TelegramClient instance and session database.
- Registers an `events.NewMessage` handler.
- Normalizes payload: `chat_id`, `chat_title`, `msg_id`, `sender_id`, `sender_name`, `mentioned`, `text`, `replies`, `reactions`, `timestamp`.
- Caches:
  - Chat type: `tgsentinel:chat_type:{chat_id}` (24h TTL).
  - Avatars: `/app/data/avatars/{user|chat}_{id}.jpg` and Redis key `tgsentinel:{user|chat}_avatar:{id}` (1h TTL).
- Filters:
  - Skips own messages (compares `sender_id` against `tgsentinel:user_info`).
  - Private chat messages only if sender in `cfg.monitored_users` (when configured).
- Emits to Redis Stream with `xadd(maxlen=100000, approximate=True)`.

### 2. worker.py

- Consumer group processing with `XREADGROUP`.
- On each message:
  - Heuristics (`heuristics.run_heuristics`): mentions/VIP/keywords/reaction+reply thresholds produce reasons and a pre‑score.
  - Semantic score (`semantic.score_text`) if embeddings are loaded; adds to pre‑score.
  - Upsert row in `messages` with rich context (chat/sender/text/triggers/sender_id).
  - If important (heuristics or semantic ≥ threshold) send alert(s) via `notifier` and mark alerted; increment metrics.
- Periodically checks `/app/data/.reload_config` to hot‑reload YAML + interest profiles.

### 3. digest.py

- Queries top alerted messages since a time window; fetches Telegram details for nicer entries (sender/time/text preview); posts DM or to target channel.

### 4. ui/app.py

- Flask server with Socket.IO. Provides dashboard, alerts view, configuration, analytics, profiles, developer tools, console, and docs.
- Caches summary/health for short TTLs to avoid over‑querying.
- Uses the same SQLite DB as the worker and reads Redis where available.

---

## Configuration Model

Two layers:

- **Environment variables** (deployment‑specific; secrets). Examples:
  - Telegram: `TG_API_ID`, `TG_API_HASH`, optional `TG_PHONE`.
  - Alerts/Digests: `ALERT_MODE` (`dm|channel|both`), `ALERT_CHANNEL`, `HOURLY_DIGEST`, `DAILY_DIGEST`, `DIGEST_TOP_N`.
  - Semantic: `EMBEDDINGS_MODEL`, `SIMILARITY_THRESHOLD`.
  - Anomaly Detection: `ANOMALY_USE_STDDEV`, `ANOMALY_STDDEV_MULTIPLIER`, `ANOMALY_VOLUME_THRESHOLD`, `ANOMALY_IMPORTANCE_THRESHOLD`, `ANOMALY_ALERT_RATE`.
  - Infra: `REDIS_HOST`, `REDIS_PORT`, `REDIS_STREAM`, `REDIS_GROUP`, `REDIS_CONSUMER`, `DB_URI`.
  - UI: `UI_SECRET_KEY` (required), `UI_PORT` (default 5000), `API_BASE_URL` (docs page only).
- **YAML** (`config/tgsentinel.yml`):
  - `telegram.session`: path to Telethon session file.
  - `alerts`: delivery mode/channel and digest defaults.
  - `channels[]`: per‑chat rules (id, name, vip_senders, keywords, reaction/reply thresholds, rate limit per hour).
  - `monitored_users[]`: restrict private dialogs by user id.
  - `interests[]`: topics used to build the semantic interest vector.

**Priority**: environment variables override YAML for overlapping fields (e.g., alert mode/digest).

**Hot‑reload**: UI writes YAML atomically and creates `/app/data/.reload_config`. Worker polls every 5s and reloads rules + interests, then removes marker.

---

## Persistence

### Database

SQLite (URI `sqlite:////app/data/sentinel.db` by default). Schema in `store.py`:

- `messages(chat_id, msg_id, content_hash, score, alerted, chat_title, sender_name, message_text, triggers, sender_id, created_at)`; PK `(chat_id, msg_id)`; `upsert_message` maintains latest score/context.
- `feedback(chat_id, msg_id, label, created_at)`; thumbs up/down from UI.

### Runtime Files

Under `/app/data` (mounted to `./data` via Docker):

- Telegram session: `tgsentinel.session` (path from YAML).
- Avatars cache: `data/avatars/*` (optional).
- Alert profiles store: `data/alert_profiles.json` (per-channel heuristic profiles).
- Interest profiles store: `data/profiles.yml` (semantic AI profiles, YAML format).
- Reload marker: `.reload_config`.

---

## Heuristics and Semantic Scoring

### Heuristics (`heuristics.py`)

- **Signals**: mention, VIP sender id, keyword regex hit (case‑insensitive, union of words), reaction and reply thresholds per channel.
- **Output**: `HeuristicResult(important, reasons, content_hash, pre_score)`.

**10 Detection Categories:**

1. **Messages Requiring Direct Action** (+0.5 to +1.2)
2. **Decisions, Voting, Direction Changes** (+1.0 to +1.1)
3. **Direct Mentions and Replies** (+1.5 to +2.0)
4. **Key Importance Indicators** (+0.9 to +1.5)
5. **Updates Related to Interests/Projects** (+0.8 to +1.2)
6. **Structured/Sensitive Data** (+0.7 to +1.3)
7. **Personal Context Changes** (+0.5 boost for private chats)
8. **Risk/Incident Messages** (+1.0)
9. **Opportunity Messages** (+0.6)
10. **Meta-Important Messages** (+0.9 to +1.2)

### Semantic (`semantic.py`)

- Optional; loaded when `EMBEDDINGS_MODEL` is set and sentence‑transformers is available.
- **Interest vector**: encode all `interests[]`, L2‑normalize, then mean.
- **Score**: cosine similarity(message, interest_vector) ∈ [−1..1], typically [0..1] with normalized vectors; compared to `SIMILARITY_THRESHOLD`.

**Alert decision**: important if heuristics triggered OR semantic score ≥ threshold. Score persisted and used for digest ranking.

---

## Notifications and Digests

- `notifier.py`: posts to `me` (Saved Messages) or a configured channel/bot username.
- `digest.py`: hourly/daily digests (enabled by config or `TEST_DIGEST=1` on startup). Builds Telegram deep links; enriches with sender/time/text.

---

## UI Architecture and API Surface

**Frameworks**: Flask 3, Flask‑SocketIO, Flask‑CORS. Static served under `ui/static`, templates under `ui/templates`.

### Pages

- `/` (Dashboard): summary, live feed, health.
- `/alerts`: alerted messages + daily digest summary.
- `/config`: edit runtime config (alerts/digest), channels management (add/delete), monitored users management (add/delete), semantic interests overview, and quick actions (save, clean DB, restart sentinel).
- `/analytics`: quick metrics (rate/latency/resources) and keyword frequency, anomaly detection with configurable thresholds.
- `/profiles`: interest profiles CRUD persisted to `data/profiles.yml` (YAML format). Toggle/rename/import/export/test sample; legacy `interests[]` auto‑migrates on demand. Also manages alert profiles with backtesting.
- `/console`: log stream placeholder and diagnostics export.
- `/docs`: UI page that links to API base/API docs (static).

### Selected API Endpoints

- **Dashboard/health**: `/api/dashboard/summary`, `/api/dashboard/activity`, `/api/system/health`.
- **Alerts**: `/api/alerts/recent`, `/api/alerts/digests`, `/api/alerts/feedback` (records label in `feedback`), `/api/export_alerts?format=human|machine`.
- **Config**: `/api/config/current`, `/api/config/save`, `/api/config/clean-db`, `/api/config/channels`, `/api/config/channels/add`, `DELETE /api/config/channels/<chat_id>`, `/api/config/users/add`, `DELETE /api/config/users/<user_id>`, `/api/config/interests`.
- **Telegram helpers**: `/api/telegram/chats`, `/api/telegram/users`, `/api/session/info`, `/api/participant/info` (worker‑assisted with Redis cache).
- **Alert Profiles**: `/api/profiles/alert/list`, `/api/profiles/alert/get`, `/api/profiles/alert/upsert`, `/api/profiles/alert/delete`, `/api/profiles/alert/toggle`, `/api/profiles/alert/backtest`.
- **Interest Profiles**: `/api/profiles/get|save|delete|toggle|export|import|test|train`, `/api/profiles/interest/backtest`.
- **Webhooks config**: `GET/POST/DELETE /api/webhooks` writing `config/webhooks.yml` (secrets masked on read).
- **Diagnostics**: `/api/console/diagnostics` (downloads anonymized JSON); `/api/export_alerts` (CSV export).
- **Developer**: `/api/developer/settings` saves `config/developer.yml` (API key stored as SHA‑256 hash; prometheus port; flags).

### Security Model

Intended for private deployments. UI requires `UI_SECRET_KEY` and sets a permissive CSP/CORS to simplify local ops. For Internet exposure, place behind a reverse proxy and add authentication (e.g., HTTP basic or SSO) and TLS termination.

---

## Redis and Caching

- **Stream**: `tgsentinel:messages` with consumer group `workers` by default.
- **Participant lookup flow**: UI writes `tgsentinel:participant_request:{chat_id}:{user_id|chat}`; worker processes and caches `tgsentinel:participant:{chat_id}:{user_id|chat}` for 30 minutes.
- **Avatars**: cached to filesystem and Redis for 1 hour; Redis restored from filesystem if key missing.

---

## Running and Developing

### Docker Compose Services

(`docker-compose.yml`):

- **redis**: Redis 7 with AOF; exposes 6379.
- **sentinel**: core app; mounts `./config` (ro) and `./data` (rw); requires `.env` for `TG_API_ID`, `TG_API_HASH`, etc.
- **ui**: same image; runs `python /app/ui/app.py`; exposes `5001:5000`; needs `UI_SECRET_KEY`.

### Local Development

- **First authentication** (inside the sentinel container):

  ```bash
  docker compose run --rm -it sentinel python -m tgsentinel.main
  ```

- **Start stack**:

  ```bash
  docker compose up -d
  docker compose logs -f sentinel
  ```

- **UI locally**:

  ```bash
  UI_SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') \
  REDIS_HOST=localhost REDIS_PORT=6379 DB_URI=sqlite:////$(pwd)/data/sentinel.db \
  python ui/app.py
  ```

### Testing

Pytest suite under `tests/` covers config precedence, client ingestion, worker loop, digest, UI endpoints, and participant info flow. Run with:

```bash
python -m pytest -q
```

---

## Performance and Tuning

- **High volume**: raise reaction/reply thresholds; increase per‑channel rate limits; consider disabling embeddings (`EMBEDDINGS_MODEL=`).
- **Low volume or niche content**: enable embeddings; adjust `SIMILARITY_THRESHOLD` (e.g., 0.35–0.5) and craft focused `interests[]` phrases.
- **DB size**: periodically export/archive if needed; `POST /api/config/clean-db` clears tables and Redis stream/cache in one step.

### Performance Characteristics

**Alert Profile Matching:**

- Speed: <1ms per message
- Memory: O(keywords) - very efficient
- Scalability: Can handle 1000s of keywords

**Interest Profile Matching:**

- Speed: ~10-50ms per message (ML inference)
- Memory: Model embeddings cached
- Scalability: Vectorized operations

**Backtesting:**

- Query time: ~100-500ms for 100 messages
- Re-scoring: <100ms for alert profiles
- Re-scoring: 1-5s for interest profiles (semantic)
- Max messages: 1000 per backtest (configurable)

---

## Extension Points

- **Heuristics**: add new signals or weights in `heuristics.py` and persist reason tags to `messages.triggers`.
- **Semantic**: swap embedding model or supply a custom encoder with the same interface.
- **Delivery**: add additional sinks (email/webhook) by extending `notifier.py` and UI webhook settings.
- **Observability**: replace `metrics.py` with a Prometheus client or OTEL exporter.
- **UI/API**: add routes in `ui/app.py` and templates under `ui/templates/` (Bootstrap/Chart.js are available via CDN).

---

## Unified Profiles System Architecture

### Two-Profile Strategy

**Alert Profiles** (Heuristic/Keyword-based):

- Per-channel keyword configuration
- Fast pattern matching (codes, polls, etc.)
- Metadata-based rules (pins, admins, reactions)
- Instant detection without ML
- Storage: `data/alert_profiles.json`

**Interest Profiles** (Semantic/AI-based):

- Global semantic understanding
- ML model training with examples
- Context and meaning detection
- Works across all channels
- Storage: `data/profiles.yml`

### Alert Profile Schema

```json
{
  "channel_123": {
    "id": "channel_123",
    "name": "Algorand Dev",
    "type": "channel",
    "channel_id": 123,
    "enabled": true,

    "action_keywords": ["can you", "need help"],
    "decision_keywords": ["vote", "proposal"],
    "urgency_keywords": ["urgent", "critical"],
    "importance_keywords": ["important", "announcement"],
    "release_keywords": ["v1.", "release"],
    "security_keywords": ["CVE", "vulnerability"],
    "risk_keywords": ["breach", "attack"],
    "opportunity_keywords": ["airdrop", "presale"],
    "keywords": ["legacy", "keywords"],

    "vip_senders": [11111, 22222],
    "reaction_threshold": 8,
    "reply_threshold": 10,

    "detect_codes": true,
    "detect_documents": true,
    "prioritize_pinned": true,
    "prioritize_admin": true,
    "detect_polls": true,

    "rate_limit_per_hour": 5,

    "created_at": "2025-11-13T...",
    "updated_at": "2025-11-13T...",
    "trigger_count": 42,
    "last_triggered": "2025-11-13T..."
  }
}
```

### Interest Profile Schema

```json
{
  "name": "algorand core development",
  "description": "Technical discussions about Algorand protocol",
  "enabled": true,

  "positive_samples": ["Example message 1", "Example message 2"],
  "negative_samples": ["Noise example 1"],

  "threshold": 0.42,
  "weight": 1.0,
  "priority": "high",

  "keywords": ["algorand", "consensus"],
  "channels": [123, 456],

  "created_at": "2025-11-13T...",
  "updated_at": "2025-11-13T...",
  "last_trained": "2025-11-13T...",
  "model_version": "all-MiniLM-L6-v2"
}
```

### Backtesting

Test profiles against historical messages:

**Backtest Result:**

```json
{
  "status": "ok",
  "profile_id": "channel_123",
  "profile_name": "Algorand Dev",
  "test_date": "2025-11-13T...",

  "matches": [...],

  "stats": {
    "total_messages": 87,
    "matched_messages": 15,
    "match_rate": 17.2,
    "avg_score": 2.1,
    "true_positives": 13,
    "false_positives": 2,
    "false_negatives": 1,
    "precision": 86.7
  },

  "recommendations": [
    "🎯 Good precision - profile is well-tuned",
    "Consider adding 'hotfix' to urgency keywords"
  ]
}
```

### Configuration Sync Flow

```text
User edits alert profile in UI
         ↓
POST /api/profiles/alert/upsert
         ↓
Save to data/alert_profiles.json
         ↓
sync_alert_profiles_to_config()
         ↓
Update config/tgsentinel.yml channels
         ↓
Touch data/.reload_config marker
         ↓
Sentinel process detects marker
         ↓
Reloads config (worker.py process_loop)
         ↓
New rules active!
```

---

## Configuration Reload Mechanism

### Overview

Automatic configuration reloading system that allows channels to be added via the UI without requiring container restarts.

### Architecture

**UI Container** (`tgsentinel-ui-1`):

When configuration changes are made through API endpoints:

1. **Config File Update**: YAML file written atomically using `tempfile` + `shutil.move`
2. **In-Memory Reload**: `reload_config()` called to refresh Flask app's global config object
3. **Signal Creation**: Creates `/app/data/.reload_config` marker file to notify worker

**Sentinel Container** (`tgsentinel-sentinel-1`):

Worker process monitors for reload signals:

1. **Periodic Check**: Every 5 seconds, checks for `/app/data/.reload_config` marker file
2. **Reload Sequence** (if marker exists):
   - Loads fresh config from YAML file
   - Rebuilds channel rules dictionary
   - Reloads semantic interests
   - Deletes marker file
   - Logs reload event with new channel count
3. **Error Handling**: Removes marker even on failure to prevent infinite retry loop

### Implementation Details

**UI Side** (`ui/app.py`):

```python
def reload_config():
    """Reload configuration from disk without reinitializing DB/Redis"""
    global config
    from tgsentinel.config import load_config
    config = load_config()
```

Integrated into:

- `api_config_channels_add()`: After adding channels via "+ ADD" button
- `api_config_save()`: After any config form submission

**Worker Side** (`src/tgsentinel/worker.py`):

```python
reload_marker = Path("/app/data/.reload_config")
last_cfg_check = 0
cfg_check_interval = 5  # seconds

# In main loop:
if reload_marker.exists():
    new_cfg = load_config()
    cfg = new_cfg
    rules = load_rules(cfg)
    load_interests(cfg.interests)
    reload_marker.unlink()
    log.info("Configuration reloaded successfully with %d channels", len(cfg.channels))
```

### Shared Volume

Docker Compose mounts `/app/data` as shared volume:

```yaml
volumes:
  - ./data:/app/data
```

Both containers can:

- Read/write YAML config files
- Create/detect marker files
- Access shared Telegram session files

### Benefits

1. **Zero Downtime**: No need to restart containers when adding channels
2. **Immediate Effect**: New channels monitored within 5 seconds
3. **Shared State**: Both UI and worker stay synchronized
4. **Error Resilient**: Failed reloads don't break the system
5. **Developer Friendly**: Easy to debug via marker file presence

### Limitations

1. **Polling Interval**: 5-second delay before worker picks up changes
2. **File-Based Signaling**: Requires shared volume between containers
3. **Single Instance**: Not designed for multi-worker deployments (yet)

---

## Troubleshooting

- **No alerts**: ensure `ALERT_MODE` and (if needed) `ALERT_CHANNEL`; verify channel IDs in YAML; inspect worker logs.
- **Embeddings disabled warning**: ensure sentence‑transformers installed and `EMBEDDINGS_MODEL` set.
- **Redis offline**: stack still works in degraded mode for historical data; live feed and participant lookups may be limited.
- **Session errors**: re‑authenticate and ensure the `telegram.session` path in YAML is writable and shared.

---

## Telegram Entity IDs

- **Positive IDs**: users (private chats).
- **Negative IDs**: groups/channels.
- **Supergroups/channels** commonly appear as `-100xxxxxxxxxx`.

UI color‑codes ids (info vs primary) and offers copy/delete actions in channel management.

This is Telegram's native format, not a TGSentinel convention.

---

## Security Notes

- Do not expose the UI on the public Internet without authentication and TLS.
- Keep `.env`, `config/tgsentinel.yml`, and `data/tgsentinel.session` out of version control and back them up securely.
- `UI_SECRET_KEY` is mandatory; generate with `python -c "import secrets; print(secrets.token_hex(32))"`.

---

## Glossary

- **Important message**: heuristics triggered or semantic score ≥ threshold.
- **Digest**: periodic summary of top N alerted messages in a timeframe.
- **Interest**: short topic phrase used to build a semantic intent vector.
- **Alert Profile**: per-channel heuristic/keyword configuration for fast pattern matching.
- **Interest Profile**: global semantic/AI configuration for context detection.
- **Backtest**: testing profile effectiveness against historical messages.
- **Hot-reload**: automatic configuration refresh without container restart.
