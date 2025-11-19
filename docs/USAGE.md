# TG Sentinel — Deployment & Usage Guide

This guide covers running TG Sentinel end-to-end with Docker, including first-time setup, configuration, testing, UI deployment, and ongoing operations.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Clone Repository](#clone-the-repository)
- [Configuration](#configuration)
- [Launch with Docker Compose](#launch-with-docker-compose)
- [Web UI Setup](#web-ui-setup)
- [Operating the Stack](#operating-the-stack)
- [Running Tests](#run-tests-inside-docker)
- [Monitoring & Metrics](#monitoring--metrics)
- [Maintenance Tips](#maintenance-tips)
- [Tools](#tools)

---

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- Telegram API credentials with user-session access (not a bot)
- Basic familiarity with shell commands

---

## Clone the Repository

```bash
git clone https://github.com/your-org/TGSentinel.git
cd TGSentinel
```

---

## Configuration

### Telegram API Credentials

You must register as a Telegram developer to obtain `api_id` and `api_hash`.

Visit: <https://my.telegram.org/auth>

### Environment Variables

Copy `.env.sample` to `.env` and adjust the values:

```bash
cp .env.sample .env
```

Key variables:

- `TG_API_ID` / `TG_API_HASH`: Telegram API credentials
- `UI_SECRET_KEY`: Required for web UI (generate with `python -c "import secrets; print(secrets.token_hex(32))"`)
- `REDIS_HOST` / `REDIS_PORT`: Redis connection (default: redis/6379)
- `DB_URI`: Storage backend (defaults to SQLite under `./data`)
- `EMBEDDINGS_MODEL`: Optional Sentence-Transformers model to enable semantic scoring (e.g., `all-MiniLM-L6-v2`)
- `SIMILARITY_THRESHOLD`: Minimum semantic similarity needed to auto-alert (default: 0.42)
- `ALERT_MODE`: Where to send alerts (`dm`, `channel`, or `both`)
- `ALERT_CHANNEL`: Target channel/bot username (if using `channel` or `both`)
- `HOURLY_DIGEST` / `DAILY_DIGEST`: Enable digest schedules (true/false)
- `DIGEST_TOP_N`: Number of messages per digest (default: 10)

**Anomaly Detection (optional):**

```bash
# Standard deviation mode (recommended)
ANOMALY_USE_STDDEV=true
ANOMALY_STDDEV_MULTIPLIER=2.0

# Or use fixed thresholds
ANOMALY_VOLUME_THRESHOLD=50
ANOMALY_IMPORTANCE_THRESHOLD=3.0
ANOMALY_ALERT_RATE=0.3
```

### YAML App Configuration

`config/tgsentinel.yml` defines channel-specific rules:

- `telegram.session`: Path to the Telethon session file (auto-created after first login)
- `alerts`: Delivery mode (DM/channel/both) and digest schedule
- `channels`: Per-chat rules for VIP senders, keyword triggers, rate limits, and thresholds
- `monitored_users`: Restrict private chat monitoring to specific user IDs
- `interests`: Topics used for semantic similarity when embeddings are enabled

Adjust this file to match the channels you want to monitor.

---

## Launch with Docker Compose

### Services Overview

The `docker-compose.yml` file defines three services:

1. **redis**: Redis 7 with AOF persistence; exposes port 6379
2. **sentinel**: Core TGSentinel app; mounts `./config` (ro) and `./data` (rw)
3. **ui**: Flask web dashboard; exposes port 5001→5000; requires `UI_SECRET_KEY`

### Initial Startup

1. Build the image and start Redis plus TG Sentinel in the background:

   ```bash
   docker compose up --build -d
   ```

   The Compose file mounts `./config` and `./data` so configuration and state persist on the host.

2. Complete the first-time Telegram login inside the running container:

   ```bash
   docker compose exec sentinel python -m tgsentinel.main
   ```

   Follow the prompts to enter your phone number and verification codes. Telethon writes the session file to the `telegram.session` path in `config/tgsentinel.yml` (default `data/tgsentinel.session`). When you see "Signed in successfully," press `Ctrl+C` to exit.

3. Restart the application container so it runs headless using the saved session:

   ```bash
   docker compose restart sentinel
   ```

   Future restarts only require `docker compose up -d`—no additional login unless you revoke the session.

---

## Web UI Setup

### Architecture

The web UI is a Flask + Socket.IO application that provides:

- Real-time dashboard with stats and live activity feed
- Alerts viewer with feedback buttons
- Configuration editor for channels, interests, alerts, and digests
- Analytics page with keyword frequency and anomaly detection
- Profiles management (Alert Profiles and Interest Profiles)
- Console with diagnostics export and log streaming
- API documentation page

### Accessing the Dashboard

Once services are running:

- **Dashboard**: <http://localhost:5001/>
- **Alerts**: <http://localhost:5001/alerts>
- **Configuration**: <http://localhost:5001/config>
- **Analytics**: <http://localhost:5001/analytics>
- **Profiles**: <http://localhost:5001/profiles>
- **Console**: <http://localhost:5001/console>
- **Docs**: <http://localhost:5001/docs>

### UI Features

#### Dashboard (`/`)

- **Stat Cards**: Messages ingested (24h), Alerts sent (24h), Avg importance, System health
- **Live Activity Feed**: Real-time table of recent alerts with scores and timestamps
- **System Health Panel**: Redis stream depth, Database size, Last update timestamp
- **Auto-refresh**: Stats update every 5 seconds via Socket.IO

#### Alerts (`/alerts`)

- **Alerts Table**: Chat ID, Message ID, Score (color-coded), Hash, Timestamp
- **Daily Digests**: Timeline view with counts and average scores
- **Export**: CSV download with `format` parameter (`human` or `machine` headers)
- **Feedback Buttons**: 👍/👎 for each alert (stored in `feedback` table)

#### Configuration (`/config`)

- **Alerts Tab**: Alert mode (dm/channel/both), Target channel, Hourly/daily digest toggles
- **Channels Tab**: List of monitored channels with add/delete functionality
- **Private Users Tab**: Manage monitored private chat users
- **Interests Tab**: Semantic interest topics for scoring
- **System Tab**: Redis host, Database URI (read-only)
- **Save Configuration**: Writes YAML atomically and hot-reloads worker (within 5 seconds)

#### Analytics (`/analytics`)

- **Live Metrics**: Messages/min, semantic latency, CPU/memory, Redis depth
- **Keyword Heatmap**: Bar chart of most frequent keywords
- **Channel Activity**: Doughnut chart of alert distribution by channel
- **Anomalies (24h)**: High volume/importance/alert-rate detection with configurable thresholds

#### Profiles (`/profiles`)

- **Alert Profiles Tab**: Per-channel keyword configuration with 10 detection categories
- **Interest Profiles Tab**: Semantic/AI-based global topic detection
- **Backtesting**: Test profiles against historical messages
- **Actions**: Toggle, rename, export, import, duplicate, test sample text

#### Console (`/console`)

- **Diagnostics Export**: Download anonymized JSON snapshot
- **Live Logs**: Terminal-style log output with auto-scroll (Socket.IO placeholder)

### Configuration Reload Mechanism

The UI and worker communicate via a shared marker file:

1. **UI Container** (`tgsentinel-ui-1`):

   - Writes configuration atomically using `tempfile` + `shutil.move`
   - Calls `reload_config()` to refresh Flask app's global config
   - Creates `/app/data/.reload_config` marker file

2. **Sentinel Container** (`tgsentinel-sentinel-1`):
   - Checks for marker file every 5 seconds
   - If present: loads fresh config, rebuilds channel rules, reloads interests
   - Deletes marker file and logs reload event

**Benefits:**

- Zero downtime when adding channels
- New channels monitored within 5 seconds
- No container restarts required
- Error resilient (failed reloads don't break system)

---

## Operating the Stack

### Stream Logs

```bash
# Follow all logs
docker compose logs -f sentinel
docker compose logs -f ui

# Last 50 lines
docker compose logs --tail=50 sentinel

# Specific time range
docker compose logs --since 1h sentinel
```

### Stop Services

```bash
docker compose down
```

### Apply Updates

```bash
docker compose pull
docker compose build --no-cache
docker compose up -d
```

### Restart Individual Services

```bash
# Restart sentinel (picks up config changes)
docker compose restart sentinel

# Restart UI only
docker compose restart ui

# Restart Redis
docker compose restart redis
```

---

## Run Tests inside Docker

Execute the pytest suite using the application image:

```bash
docker compose run --rm sentinel python -m pytest -q
```

The repository is bind-mounted into the container, so tests operate on your working copy.

### Test Coverage

195 tests across:

- Configuration loading and priority
- Client ingestion and avatar caching
- Worker loop and heuristics
- Digest generation
- UI endpoints and live data
- Participant info flow
- Config reload mechanism
- Alert profiles and backtesting

---

## Monitoring & Metrics

### Log-Based Metrics

TG Sentinel emits log-based metrics such as:

```text
metric alerts_total{chat=-100123456789} 5 ts=...
metric messages_processed{chat=-100123456789} 87 ts=...
```

Forward container logs to your aggregation system, or extend `metrics.py` to export to Prometheus or another backend.

### Health Endpoints

- `GET /api/system/health`: Redis and database health with stream depth and DB size
- `GET /api/dashboard/summary`: 24-hour stats (messages ingested, alerts sent, avg importance)
- `GET /api/dashboard/activity`: Recent messages from Redis stream

### Resource Monitoring

```bash
# Container status
docker compose ps

# Resource usage (live)
docker stats tgsentinel-sentinel-1 tgsentinel-ui-1

# Disk usage
du -sh data/
```

---

## Maintenance Tips

### Backups

Keep these under backup:

- `.env`: secrets and deployment config
- `config/tgsentinel.yml`: channel rules and interests
- `config/webhooks.yml`: webhook configurations (if used)
- `data/tgsentinel.session`: Telegram session file
- `data/sentinel.db`: alerts and feedback history
- `data/profiles.yml`: interest profiles (YAML format)
- `data/alert_profiles.json`: alert profiles

### Housekeeping

- **Clean Database**: Use UI Config page or `POST /api/config/clean-db` to clear messages/feedback and Redis stream
- **Embeddings**: Disable if resource-constrained by clearing `EMBEDDINGS_MODEL` env var
- **Rate Limits**: Adjust per-channel `rate_limit_per_hour` to reduce alert volume
- **Old Logs**: Rotate Docker logs with `docker-compose.yml` logging configuration

### Configuration Updates

- **Channel Rules**: Edit in UI Config page; changes sync to YAML and hot-reload
- **Alert Profiles**: Edit in UI Profiles page; stored in `data/alert_profiles.json` and synced to YAML
- **Environment Variables**: Modify `.env` and restart services (`docker compose up -d`)
- **YAML Direct Edit**: Edit `config/tgsentinel.yml` and restart sentinel (`docker compose restart sentinel`)

### Session Management

- **Session Expiry**: Re-authenticate if Telegram revokes session (run `python -m tgsentinel.main` in container)
- **Multiple Sessions**: Use different `telegram.session` paths for different accounts
- **Session Backup**: Keep `.backup` copy of session file for disaster recovery

---

## Tools

TGSentinel includes several utility tools under `/tools`:

### Populate History

Fetch historical messages from monitored channels and add them to Redis stream for backtesting:

```bash
# Fetch latest 100 messages from all channels
docker compose run --rm sentinel python tools/populate_history.py

# Fetch 50 messages from specific channel
docker compose run --rm sentinel python tools/populate_history.py --limit 50 --channel-id -1001234567890

# Dry run (preview without adding to Redis)
docker compose run --rm sentinel python tools/populate_history.py --dry-run

# Clear existing stream and add fresh history
docker compose run --rm sentinel python tools/populate_history.py --clear --limit 100
```

### Simulate Digest from History

Test digest generation with historical messages:

```bash
docker compose run --rm sentinel python tools/simulate_digest_from_history.py
```

### Verify Config UI

Check UI configuration endpoints:

```bash
docker compose run --rm sentinel python tools/verify_config_ui.py
```

### Run Tests

Dedicated test runner with coverage options:

```bash
# Run all tests
docker compose run --rm sentinel python tools/run_tests.py

# Run specific test file
docker compose run --rm sentinel python tools/run_tests.py tests/test_config.py

# Run with coverage
docker compose run --rm sentinel python tools/run_tests.py --coverage
```

### Format Code

Format Python code with black:

```bash
docker compose run --rm sentinel bash tools/format.sh
```

---

## Troubleshooting

### Services Won't Start

- Check `.env` has required variables: `TG_API_ID`, `TG_API_HASH`, `UI_SECRET_KEY`
- Verify Docker and Docker Compose versions: `docker --version`, `docker compose version`
- Check port conflicts: `lsof -i :5001` (macOS) or `netstat -tuln | grep 5001` (Linux)

### Authentication Issues

- **"Session not authorized"**: Re-run `docker compose exec sentinel python -m tgsentinel.main`
- **"Phone code invalid"**: Request new code; ensure correct API credentials
- **2FA prompt**: Enter 2FA password when prompted

### No Alerts Arriving

- Verify channels exist in `config/tgsentinel.yml` with correct IDs
- Check `ALERT_MODE` env var: `dm`, `channel`, or `both`
- If using `channel` mode, ensure `ALERT_CHANNEL` is set to valid channel/bot username
- Review worker logs: `docker compose logs -f sentinel`

### High CPU/Memory Usage

- Disable embeddings: clear `EMBEDDINGS_MODEL` env var
- Reduce number of interests in config
- Increase per-channel rate limits
- Tighten keyword rules to reduce false positives

### Redis Connection Issues

- Verify Redis container is running: `docker compose ps redis`
- Check Redis logs: `docker compose logs redis`
- Test connection: `docker compose exec redis redis-cli ping` (should return `PONG`)
- Restart Redis if needed: `docker compose restart redis`

### UI Not Accessible

- Check UI container is running: `docker compose ps ui`
- Verify `UI_SECRET_KEY` is set in `.env`
- Review UI logs: `docker compose logs -f ui`
- Try accessing directly: `curl http://localhost:5001/api/system/health`

---

With this setup, TG Sentinel runs fully in Docker, simplifying upgrades, restarts, and deployment across hosts. The web UI provides comprehensive monitoring and configuration capabilities without requiring container restarts for most changes.
