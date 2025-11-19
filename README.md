# 🛰️ TG Sentinel

> **Intelligent Telegram Activity Sentinel**
>
> TG Sentinel is a self-hosted, privacy-preserving Telegram companion that listens to all messages across your channels, groups, and private chats (using your own user session, not a bot), and alerts you only when something truly important happens.
>
> Its goal is simple: **reduce noise, preserve signal.**

---

## 🚀 Overview

Modern Telegram power-users often belong to dozens of channels and groups, most of which are muted due to noise.  
TG Sentinel automatically monitors them for you, applies intelligent filtering and semantic scoring, and delivers concise alerts or daily digests containing only high-value messages.

TG Sentinel runs locally or on your own server — your data never leaves your environment.

---

## 🧩 Core Features

| Category                        | Description                                                                                                                                            |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **User-client ingestion**       | Connects directly to Telegram via **MTProto (user session)** using [Telethon](https://docs.telethon.dev) or [TDLib]. No bots or group invites needed.  |
| **Global listener**             | Subscribes to all dialogs, channels, and groups you belong to (except secret chats).                                                                   |
| **Two-stage importance engine** | Stage A: fast heuristics (mentions, VIPs, keywords, reactions, replies, pins). Stage B: semantic scoring using embeddings or local LLM classification. |
| **Multi-channel rules**         | YAML configuration per chat: keywords, VIP senders, reaction thresholds, rate limits.                                                                  |
| **Digest & alerts**             | Sends important posts directly to your “Saved Messages” or to a private “Important 🔔” channel. Hourly/daily digests group the highlights.             |
| **Feedback loop**               | React 👍/👎 on alerts; TG Sentinel learns from your votes and adjusts thresholds automatically.                                                        |
| **Privacy-first**               | Runs entirely under your control. No external APIs required. Message contents are analyzed locally.                                                    |
| **Resilient & observable**      | Durable ingestion via Redis Streams; Prometheus metrics; graceful reconnection; gap recovery.                                                          |
| **Minimal footprint**           | ~100 MB RAM; runs on any always-on VPS, mini-PC, or Docker host.                                                                                       |

---

## 🏗️ Architecture

```mermaid
flowchart TD
    A[Telegram Cloud] -->|MTProto updates| B[TG Sentinel Client]
    B -->|Normalized messages| C[Redis Streams]
    C --> D[Heuristic Filter]
    D -->|passes threshold| E[Semantic Scorer]
    E -->|importance score| F[Notifier / Digest Generator]
    F -->|alerts| G[User (Saved Messages / Private Channel)]
    F --> H[(SQLite ]
    F --> I[Prometheus Metrics]
```

Components

1️⃣ Telegram Client
• Based on Telethon (Python) or TDLib bindings.
• Maintains a persistent session (.session file).
• Streams NewMessage events from all accessible chats.
• Performs normalization: chat ID, sender, timestamp, text, entities, reply/reaction counts.
• **Single-Owner Pattern**: Only the sentinel container owns and writes to the session SQLite database.

2️⃣ Redis Stream (Message Bus)
• Lightweight, append-only queue between ingestion and analysis stages.
• Ensures at-least-once delivery, buffering, and natural backpressure.

3️⃣ Heuristic Filter
• Fast rule-based stage written in Python.
• Checks:
• Mentions of you
• VIP sender IDs
• Reaction / reply surge thresholds
• Admin/pinned posts
• Keyword or regex hits
• Emits candidate messages with metadata and reason tags.

4️⃣ Semantic Scorer
• Embedding-based or local LLM classifier.
• Computes cosine similarity between message vector and user “interest profiles” (topics defined in config/interests.yml).
• Optional local embedding model (e.g., all-MiniLM-L6-v2, bge-small-en) or quantized ggml model.
• Fallback to API call only if local model abstains.

5️⃣ Notifier & Digest Generator
• Sends:
• Instant alerts (per-message DM to yourself)
• Hourly or daily digest (Top-N by score)
• Implements deduplication (chat_id + msg_id hash) and per-channel rate limits.
• Optional push to Pushover, ntfy, or email.

6️⃣ Metadata & Feedback Store
• SQLite:
• Message IDs, scores, alert history
• Feedback reactions (👍/👎)
• Per-channel dynamic thresholds
• Simple table schema; nightly cleanup of old content.

7️⃣ Observability
• Prometheus metrics:
• sentinel_messages_total{stage=ingest|heuristic|semantic}
• sentinel_alerts_total
• sentinel_errors_total
• Optional Grafana dashboard to tune thresholds.

⸻

## 🔒 Session Architecture

TG Sentinel follows a **Single-Owner Process** pattern for Telegram session management to ensure data integrity and prevent SQLite concurrency issues:

### Key Principles

- **Sentinel Container**: Exclusive owner of the Telegram session SQLite database
- **UI Container**: Never directly accesses the session file; all Telegram operations delegated via Redis
- **No Re-authentication**: Session persists across container restarts once authenticated
- **Zero Concurrency Conflicts**: Single writer pattern eliminates "database is locked" errors

### Architecture Flow

```bash
User (Web UI)
    ↓
    ↓ Credentials via Redis
    ↓
UI Container (Flask)
    ↓ Redis IPC (auth_queue)
    ↓
Sentinel Container (Telethon)
    ↓ Exclusive session access
    ↓
Telegram Session (SQLite)
    ↓
Telegram API
```

### Authentication Process

1. User enters phone/code in web UI
2. UI submits credentials to Redis (`tgsentinel:auth_queue`)
3. Sentinel reads queue and performs sign-in operation
4. Sentinel validates with `client.get_me()`
5. Session persisted to disk automatically
6. UI polls status and confirms login

**Result**: Session file owned exclusively by sentinel; no dual-writer conflicts; no re-authentication loops.

### Common Session Issues

If you experience session problems:

- **"database is locked"** - Should never happen with single-owner pattern; verify UI container not accessing session
- **Re-authentication required** - Session not persisting (check container logs for save errors)
- **Connection timeout** - Network issues or Telegram API limits

Solution: Restart containers and verify session ownership:

```bash
docker compose restart
docker compose logs sentinel | grep "Session loaded"
# Should show: "Session loaded via get_me(): User(...)"
```

For technical details, see [Engineering Guidelines: Session Management](docs/ENGINEERING_GUIDELINES.md#session-management-single-owner-pattern).

⸻

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- Telegram account
- API credentials from <https://my.telegram.org/auth>

### Setup

1. **Get Telegram API Credentials**

   - Visit <https://my.telegram.org/auth>
   - Log in with your phone number
   - Go to "API development tools"
   - Create a new application
   - Copy your `api_id` (7-8 digits) and `api_hash` (32-character hex)

2. **Configure Environment**

   ```bash
   # Clone the repository
   git clone https://github.com/redoracle/TGSentinel.git
   cd TGSentinel

   # Create .env file
   cp .env.sample .env

   # Edit .env with your credentials
   nano .env
   ```

   Required `.env` variables:

   ```bash
   TG_API_ID=12345678
   TG_API_HASH=0123456789abcdef0123456789abcdef

   # Webhook encryption key (required if using webhooks)
   # Generate with: python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   WEBHOOK_SECRET_KEY=your_generated_fernet_key_here

   # Alert settings
   ALERT_MODE=both              # dm | channel | both
   ALERT_CHANNEL=@your_bot      # Your notification channel/bot
   HOURLY_DIGEST=true
   DAILY_DIGEST=true

   # Optional: customize other settings
   EMBEDDINGS_MODEL=all-MiniLM-L6-v2
   SIMILARITY_THRESHOLD=0.42
   ```

3. **Configure Channels** (optional)

   Edit `config/tgsentinel.yml` to add monitored channels:

   ```yaml
   channels:
     - id: -100123456789
       name: "My Channel"
       vip_senders: [111111, 222222]
       keywords: ["important", "urgent", "security"]
       reaction_threshold: 5
       reply_threshold: 3

   interests:
     - "topic I care about"
     - "another important subject"
   ```

4. **First Run (Interactive Login)**

   ```bash
   docker compose build
   docker compose run --rm -it sentinel python -m tgsentinel.main
   ```

   You'll be prompted for:

   - Phone number (with country code, e.g., +1234567890)
   - Login code (sent via SMS or Telegram)
   - 2FA password (if enabled)

   This creates `data/tgsentinel.session` for future runs.

5. **Start Services**

```bash
docker compose up -d
docker compose logs -f sentinel
```

### Testing

Run the test suite:

```bash
# Using make
make test

# Or directly
python tools/run_tests.py

# With coverage
make test-cov
```

### Development

Format code (like Prettier for Python):

```bash
make format
```

Available commands:

```bash
make help              # Show all commands
make format            # Format all Python files
make format-check      # Check formatting (CI mode)
make test              # Run tests
make lint              # Run type checking
make clean             # Clean generated files
make docker-build      # Build Docker image
make docker-up         # Start services
make docker-down       # Stop services
make docker-logs       # Follow logs
```

---

## 🔧 Configuration

### Environment Variables

All settings can be overridden via environment variables:

| Variable               | Default                           | Description                                               |
| ---------------------- | --------------------------------- | --------------------------------------------------------- |
| `TG_API_ID`            | _(required)_                      | Telegram API ID from my.telegram.org                      |
| `TG_API_HASH`          | _(required)_                      | Telegram API hash                                         |
| `WEBHOOK_SECRET_KEY`   | _(required for webhooks)_         | Fernet encryption key for webhook secrets (fail-fast)     |
| `ALERT_MODE`           | `dm`                              | Alert destination: `dm`, `channel`, or `both`             |
| `ALERT_CHANNEL`        | `""`                              | Target channel/bot username (e.g., `@kit_red_bot`)        |
| `HOURLY_DIGEST`        | `true`                            | Enable hourly digest                                      |
| `DAILY_DIGEST`         | `true`                            | Enable daily digest                                       |
| `DIGEST_TOP_N`         | `10`                              | Number of top messages in digest                          |
| `EMBEDDINGS_MODEL`     | `all-MiniLM-L6-v2`                | Sentence transformer model (empty to disable)             |
| `SIMILARITY_THRESHOLD` | `0.42`                            | Semantic similarity threshold (0-1)                       |
| `REDIS_HOST`           | `redis`                           | Redis hostname                                            |
| `REDIS_PORT`           | `6379`                            | Redis port                                                |
| `DB_URI`               | `sqlite:////app/data/sentinel.db` | Database connection string                                |
| `UI_SKIP_AUTH`         | `""`                              | DEV ONLY: when set to `true`, UI gating is disabled       |
| `UI_LOCK_PASSWORD`     | `""`                              | Optional UI lock password (empty disables password check) |
| `UI_LOCK_TIMEOUT`      | `900`                             | Idle timeout in seconds before UI auto-locks              |

### YAML Configuration

`config/tgsentinel.yml`:

```yaml
telegram:
  api_id: 123456
  api_hash: "your_api_hash_here"
  session: "tgsentinel.session"

redis:
  host: localhost
  port: 6379
  stream: "tgsentinel:messages"

database:
  uri: "sqlite:///data/sentinel.db"

alerts:
  mode: "dm" # dm | channel | both
  digest_hourly: true
  digest_daily: true

channels:
  - id: -100123456789
    name: "Algorand Dev"
    vip_senders: [12345, 67890]
    keywords: ["release", "security", "CVE", "go-algorand"]
    reaction_threshold: 8
    reply_threshold: 10
    rate_limit_per_hour: 5

interests:
  - "algorand core development"
  - "blockchain security advisories"
  - "governance proposals"
```

---

---

## 🐍 Quick Start (Docker Compose)

```yaml
version: "3.9"
services:
  redis:
    image: redis:7
    restart: always
  sentinel:
    image: ghcr.io/youruser/tgsentinel:latest
    environment:
      - TG_API_ID=${TG_API_ID}
      - TG_API_HASH=${TG_API_HASH}
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    depends_on:
      - redis
    restart: always
```

1. Create a new Telegram API app at my.telegram.org.
2. Fill in your api_id and api_hash in config/tgsentinel.yml.
3. Run docker compose up -d.
4. On first run, TG Sentinel will open an interactive login to obtain your session.
5. Start receiving intelligent alerts within minutes.

---

## 🧠 Importance Model (Summary)

| Stage | Technique                                            | Cost     | Purpose                      |
| ----- | ---------------------------------------------------- | -------- | ---------------------------- |
| A     | Regex/keyword match, VIP sender, mentions, reactions | O(1)     | Immediate signal detection   |
| B     | Text embeddings (cosine > τ) vs. interest vectors    | O(n × d) | Semantic relevance           |
| C     | Optional LLM summarization & classification          | High     | Rare fallback for long posts |

TG Sentinel learns from feedback. React with 👍 on relevant alerts, 👎 on false positives; it re-weights feature coefficients weekly.

---

## 🔒 Privacy & Security Notes

• Uses your own Telegram account session; no bots involved.
• Does not access secret chats (not possible via API).
• All analysis is local. No message content is sent to third-party services.
• Optionally run behind a VPN for IP masking (MTProxy not required).
• Data retention: message content purged after 7–14 days; metadata kept for learning.

---

## ⚙️ Operational Guidelines

| Concern             | Best Practice                                                                     |
| ------------------- | --------------------------------------------------------------------------------- |
| Rate limits         | TG Sentinel is read-only; avoid sending >20 msgs/min to self to prevent spam flag |
| Session persistence | Keep one .session file per account; reuse on restarts                             |
| Crash safety        | Redis Streams + checkpointing ensure no message loss                              |
| Scaling             | Horizontal: multiple semantic workers consuming from stream groups                |
| Resource use        | CPU < 5%, RAM ≈ 100–150 MB on typical setups                                      |

---

## 🧪 Example Telethon Skeleton (Simplified)

```python
from telethon import TelegramClient, events
import re

API_ID = ...
API_HASH = ...
SESSION = "tgsentinel.session"
client = TelegramClient(SESSION, API_ID, API_HASH)

IMPORTANT = re.compile(r"(algorand|security|release|CVE)", re.I)
VIP = {12345, 67890}

def important(m):
    return (
        m.mentioned or
        m.sender_id in VIP or
        (m.message and IMPORTANT.search(m.message))
    )

@client.on(events.NewMessage())
async def handler(event):
    if important(event.message):
        text = (event.message.message or "").strip()
        await client.send_message("me", f"🔔 {event.chat.title}: {text[:200]}")

client.start()
client.run_until_disconnected()
```

---

## 📈 Roadmap

• Web UI for threshold tuning & feedback visualization
• On-device summarization model integration
• Multi-account support
• Distributed classifier pool
• Fine-tuning via user-tagged data

---

## 🧰 Tech Stack

| Layer      | Technology                      |
| ---------- | ------------------------------- |
| Ingestion  | Python 3 + Telethon / TDLib     |
| Queue      | Redis 7 Streams                 |
| Data store | SQLite                          |
| Classifier | Local embeddings + optional LLM |
| Metrics    | Prometheus + Grafana            |
| Deployment | Docker / Docker Compose         |

---

## 📜 License

MIT License © 2025 — TG Sentinel

Use at your own risk; this software interacts with the Telegram API via your own user session.

---

## 🧭 Philosophy

> "Signal over noise."

TG Sentinel is designed for professionals, developers, and analysts who need to stay informed without drowning in chatter.
It's not another notification flood — it's a sentinel standing guard over your attention.

## How to run

```bash

source .venv/bin/activate  # optional, if using virtualenv
cp .env.sample .env
# edit .env with your API credentials
# optionally adjust config/tgsentinel.yml

docker compose build
docker compose up -d
# first start will prompt Telegram login in the container logs:
docker compose logs -f sentinel
```

---

## 📚 Documentation

Comprehensive guides are available in the `docs/` directory:

- **[USER_GUIDE.md](docs/USER_GUIDE.md)** - Day-to-day usage, web UI walkthrough, alert & interest profiles, troubleshooting
- **[USAGE.md](docs/USAGE.md)** - Deployment with Docker, web UI setup, configuration reload, tools & maintenance
- **[ENGINEERING_GUIDELINES.md](docs/ENGINEERING_GUIDELINES.md)** - Architecture, data flow, unified profiles system, extension points
- **[CONFIGURATION.md](docs/CONFIGURATION.md)** - Environment variables, YAML config reference, database queries, performance tuning

### 🔧 Utility Tools

See **[tools/README.md](tools/README.md)** for development and session management utilities:

- **`generate_session.py`** - Generate portable Telegram session files for UI upload (avoid SMS codes)
- **`check_rate_limit.py`** - Check Telegram rate limit status
- **`run_tests.py`** - Run the full test suite
- And more...

## 📄 License

TG Sentinel is distributed under the Apache License 2.0, a permissive and industry-standard open-source license chosen to support transparency, broad adoption, and long-term commercial viability.

### Why Apache-2.0

- **Trust and Auditability**

TG Sentinel monitors all Telegram channels, groups, and chats through a user-owned session.
This requires maximum clarity, observability, and community oversight.
A permissive license ensures that independent developers and security professionals can inspect the entire codebase without restrictions.

- **Strong Legal Protection**

Apache-2.0 includes explicit and robust clauses for:
• Patent use
• Contributor rights
• Warranty disclaimers
• Limitation of liability

For a privacy-sensitive tool that handles encrypted communication and AI-driven analysis, these protections are essential for both maintainers and users.

- **Maximum Adoption and Ecosystem Growth**

Permissive licensing allows:
• Universities and researchers to integrate it into experimental pipelines
• Developers to embed it into private infrastructures
• Companies to build solutions on top of it
• Teams to deploy and customize it freely

This accelerates innovation and builds a larger ecosystem around TG Sentinel.

- **Foundation for Future Commercial Extensions**

Apache-2.0 supports a clean path toward:
• Pro and enterprise editions
• Hosted SaaS offerings
• Proprietary add-on modules
• Partnerships and integrations

The community version remains fully open, while advanced features can be offered under commercial licenses without conflict.
