# Configuration & Monitoring Guide

This guide helps you customize TG Sentinel's behavior and monitor its activity.

---

## 🎯 Quick Reference: Configuration Priority

```
Environment Variables (.env) > YAML File (config/tgsentinel.yml)
```

**IMPORTANT:** Environment variables **always override** YAML settings. This ensures:

- ✅ Secrets stay secure (never committed to git)
- ✅ Deployment-specific settings are easy to change
- ✅ Base configuration can be version-controlled
- ✅ Docker containers can be configured at runtime

**Golden Rule:**

- Use `.env` for: credentials, deployment-specific values, feature flags
- Use YAML for: channel rules, interest profiles, structural configuration

---

## 📋 Table of Contents

- [Configuration Priority](#quick-reference-configuration-priority)
- [Configuration Overview](#configuration-overview)
- [Environment Variables](#environment-variables)
- [YAML Configuration](#yaml-configuration)
- [Monitoring & Logs](#monitoring--logs)
- [Database Queries](#database-queries)
- [Troubleshooting](#troubleshooting)

---

## Configuration Overview

TG Sentinel uses a **two-layer configuration system**:

1. **Environment Variables** (`.env` file) - Runtime settings, credentials, and feature toggles
2. **YAML Configuration** (`config/tgsentinel.yml`) - Channel rules, keywords, and interests

**Priority:** Environment variables **override** YAML settings for alerts and digest configuration.

### How Priority Works

When the application loads configuration:

```python
# Example: Alert mode configuration
1. Check environment variable ALERT_MODE
2. If not set, fall back to config/tgsentinel.yml alerts.mode
3. If neither exists, use hardcoded default ("dm")
```

**Practical Example:**

If you have:

- `.env`: `ALERT_MODE=both`
- `tgsentinel.yml`: `alerts.mode: "dm"`

Result: The app uses `mode="both"` from `.env` (environment wins)

---

## Environment Variables

### Required Settings

Edit your `.env` file:

```bash
# Telegram API Credentials (from https://my.telegram.org/auth)
TG_API_ID=12345678                    # Your API ID (7-8 digits)
TG_API_HASH=abc123def456...           # Your API hash (32 hex chars)
```

### Alert Configuration

```bash
# Where to send alerts
ALERT_MODE=both                       # Options: dm | channel | both
ALERT_CHANNEL=@your_bot_name          # Target channel/bot username

# Digest settings
HOURLY_DIGEST=true                    # Send hourly digest (true/false)
DAILY_DIGEST=true                     # Send daily digest (true/false)
DIGEST_TOP_N=10                       # Number of messages per digest
```

**Alert Modes:**

- `dm` - Send only to Telegram "Saved Messages"
- `channel` - Send only to the specified channel/bot
- `both` - Send to both destinations

### Semantic Analysis

```bash
# Embedding model for semantic scoring
EMBEDDINGS_MODEL=all-MiniLM-L6-v2     # Model name (or empty to disable)
SIMILARITY_THRESHOLD=0.42             # Score threshold (0.0-1.0)
```

**Available models:**

- `all-MiniLM-L6-v2` (default) - Fast, 80MB
- `all-mpnet-base-v2` - Better quality, 420MB
- Empty string - Disable semantic scoring (heuristics only)

### Infrastructure

```bash
# Redis connection
REDIS_HOST=redis                      # Redis hostname
REDIS_PORT=6379                       # Redis port
REDIS_STREAM=tgsentinel:messages      # Stream name
REDIS_GROUP=workers                   # Consumer group
REDIS_CONSUMER=worker-1               # Consumer ID

# Database
DB_URI=sqlite:////app/data/sentinel.db

# Logging
LOG_LEVEL=INFO                        # DEBUG | INFO | WARNING | ERROR
```

### Testing

```bash
# Trigger digest immediately on startup
TEST_DIGEST=true
```

---

## YAML Configuration

Edit `config/tgsentinel.yml`:

### Basic Structure

```yaml
telegram:
  session: "data/tgsentinel.session"

alerts:
  mode: "dm" # Overridden by ALERT_MODE env var
  target_channel: "" # Overridden by ALERT_CHANNEL env var
  digest:
    hourly: true # Overridden by HOURLY_DIGEST env var
    daily: true # Overridden by DAILY_DIGEST env var
    top_n: 10 # Overridden by DIGEST_TOP_N env var

channels: [] # List of channel rules (see below)
interests: [] # List of interest topics (see below)
```

### Channel Rules

Each channel can have custom filtering rules:

```yaml
channels:
  - id: -100123456789 # Channel ID (get from @userinfobot)
    name: "Dev Updates" # Friendly name (for logs)
    vip_senders: [111111, 222222] # User IDs to always alert
    keywords: # Keywords to match (case-insensitive)
      - "release"
      - "security"
      - "CVE"
      - "breaking change"
    reaction_threshold: 5 # Alert if reactions >= this
    reply_threshold: 3 # Alert if replies >= this
    rate_limit_per_hour: 10 # Max alerts per hour for this channel
```

**How to get channel IDs:**

1. Forward a message from the channel to [@userinfobot](https://t.me/userinfobot)
2. Or use: `docker compose exec sentinel python -c "from telethon.sync import TelegramClient; c = TelegramClient('data/tgsentinel.session', None, None); c.connect(); print([(d.id, d.name) for d in c.get_dialogs()])"`

### Interest Topics

Define topics for semantic scoring:

```yaml
interests:
  - "blockchain technology and cryptocurrencies"
  - "software security vulnerabilities"
  - "AI and machine learning developments"
  - "open source project releases"
```

**Tips:**

- Use descriptive phrases (not just keywords)
- 3-10 interests work best
- More specific = better matching

### Example Configuration

```yaml
channels:
  # High-priority security channel
  - id: -1001234567890
    name: "Security Advisories"
    vip_senders: []
    keywords: ["CVE", "vulnerability", "patch", "exploit", "0day"]
    reaction_threshold: 3
    reply_threshold: 2
    rate_limit_per_hour: 20

  # Developer updates (with VIP filtering)
  - id: -1009876543210
    name: "Python Updates"
    vip_senders: [123456, 789012] # Core maintainers
    keywords: ["release", "deprecated", "breaking"]
    reaction_threshold: 10
    reply_threshold: 5
    rate_limit_per_hour: 5

interests:
  - "security vulnerabilities and exploits"
  - "Python programming language updates"
  - "DevOps and infrastructure automation"
```

---

## Monitoring & Logs

### View Live Logs

```bash
# Follow all logs
docker compose logs -f sentinel

# Last 50 lines
docker compose logs --tail=50 sentinel

# Specific time range
docker compose logs --since 1h sentinel
```

### Log Levels

- `INFO` - Normal operations (message processed, alert sent)
- `WARNING` - Embeddings disabled, configuration issues
- `ERROR` - Redis connection failed, Telegram API errors

### Check Service Status

```bash
# Container status
docker compose ps

# Resource usage
docker stats tgsentinel-sentinel-1
```

---

## Database Queries

### View Recent Alerts

```bash
# Connect to database
docker compose exec sentinel sqlite3 /app/data/sentinel.db
```

**SQL queries:**

```sql
-- Last 10 alerted messages
SELECT chat_id, msg_id, score, created_at
FROM messages
WHERE alerted = 1
ORDER BY created_at DESC
LIMIT 10;

-- Messages by chat
SELECT chat_id, COUNT(*) as count, AVG(score) as avg_score
FROM messages
WHERE alerted = 1
GROUP BY chat_id
ORDER BY count DESC;

-- Recent high-scoring messages
SELECT chat_id, msg_id, score, created_at
FROM messages
WHERE score > 2.0
ORDER BY score DESC
LIMIT 20;

-- Messages from last 24 hours
SELECT chat_id, msg_id, score, created_at
FROM messages
WHERE alerted = 1
  AND created_at >= datetime('now', '-1 day')
ORDER BY created_at DESC;
```

### Export Message Log

```bash
# Export to CSV
docker compose exec sentinel sqlite3 /app/data/sentinel.db \
  -header -csv \
  "SELECT * FROM messages WHERE alerted=1 ORDER BY created_at DESC LIMIT 100" \
  > alerts.csv
```

---

## Troubleshooting

### No Alerts Received

1. **Check alert mode:**

   ```bash
   docker compose exec sentinel env | grep ALERT
   ```

   Ensure `ALERT_MODE=both` or `channel` and `ALERT_CHANNEL` is set.

2. **Verify channel access:**

   ```bash
   docker compose logs sentinel | grep -i error
   ```

3. **Test with manual digest:**

   ```bash
   # Insert test messages
   docker compose exec sentinel python /app/data/test_digest.py

   # Trigger digest
   docker compose run --rm -e TEST_DIGEST=true sentinel python -m tgsentinel.main
   ```

### Messages Not Matching

1. **Check keyword case sensitivity:**

   - Keywords are case-insensitive by default

2. **Verify channel ID:**

   ```bash
   # List all your channels
   docker compose exec sentinel python -c "
   from telethon.sync import TelegramClient
   import sys
   sys.path.insert(0, '/app/src')
   from tgsentinel.config import load_config
   cfg = load_config()
   c = TelegramClient(cfg.telegram_session, cfg.api_id, cfg.api_hash)
   c.connect()
   for d in c.get_dialogs():
       if d.is_channel or d.is_group:
           print(f'{d.id}: {d.name}')
   "
   ```

3. **Check semantic scoring:**

   ```bash
   # Verify embeddings loaded
   docker compose logs sentinel | grep -i "sentence"
   ```

### High CPU/Memory Usage

1. **Disable embeddings if not needed:**

   ```bash
   # In .env
   EMBEDDINGS_MODEL=
   ```

2. **Reduce interests count:**

   - Keep 3-5 focused topics

3. **Lower digest frequency:**

   ```bash
   HOURLY_DIGEST=false
   DAILY_DIGEST=true
   ```

### Session Errors

If you see "Session is not authorized":

```bash
# Remove old session
docker compose down
rm data/tgsentinel.session*

# Reauthorize
docker compose run --rm -it sentinel python -m tgsentinel.main
```

---

## Quick Reference

### Restart After Configuration Change

```bash
docker compose restart sentinel
```

### View Current Configuration

```bash
# Environment variables
docker compose exec sentinel env | grep -E 'ALERT|DIGEST|EMBEDDINGS'

# YAML config
cat config/tgsentinel.yml
```

### Test Alert Delivery

```bash
# Method 1: Manual digest with test data
docker compose exec sentinel python /app/data/test_digest.py
docker compose run --rm -e TEST_DIGEST=true sentinel python -m tgsentinel.main

# Method 2: Send yourself a message in a monitored channel
# Use keywords from your config
```

### Backup Important Data

```bash
# Session file (keep secure!)
cp data/tgsentinel.session data/tgsentinel.session.backup

# Database
cp data/sentinel.db data/sentinel.db.backup

# Configuration
cp config/tgsentinel.yml config/tgsentinel.yml.backup
cp .env .env.backup
```

---

## Performance Tuning

### For High-Volume Channels (1000+ msgs/day)

```yaml
# Increase thresholds
channels:
  - id: -100123456789
    reaction_threshold: 20 # Higher bar
    reply_threshold: 15
    rate_limit_per_hour: 3 # Fewer alerts
```

```bash
# Disable semantic scoring
EMBEDDINGS_MODEL=
```

### For Low-Volume Channels (< 100 msgs/day)

```yaml
# Lower thresholds
channels:
  - id: -100123456789
    reaction_threshold: 2
    reply_threshold: 1
    rate_limit_per_hour: 10
```

```bash
# Enable semantic scoring
EMBEDDINGS_MODEL=all-MiniLM-L6-v2
SIMILARITY_THRESHOLD=0.35       # Lower threshold
```

---

## Support

- **Documentation**: [README.md](../README.md)
- **Issues**: <https://github.com/redoracle/TGSentinel/issues>
- **Logs**: `docker compose logs -f sentinel`

---

**Need more help?** Check the logs first, then open an issue with:

1. Your configuration (without API credentials)
2. Relevant log excerpts
3. Expected vs actual behavior
