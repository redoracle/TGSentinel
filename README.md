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

🔧 Configuration

config/tgsentinel.yml

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

⸻

🐍 Quick Start (Docker Compose)

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

⸻

🧠 Importance Model (Summary)

Stage Technique Cost Purpose
A Regex/keyword match, VIP sender, mentions, reaction surge O(1) Immediate signal detection
B Text embeddings (cosine > τ) vs. interest vectors O(n × d) Semantic relevance
C Optional LLM summarization & classification High Rare fallback for long posts

TG Sentinel learns from feedback. React with 👍 on relevant alerts, 👎 on false positives; it re-weights feature coefficients weekly.

⸻

🔒 Privacy & Security Notes
• Uses your own Telegram account session; no bots involved.
• Does not access secret chats (not possible via API).
• All analysis is local. No message content is sent to third-party services.
• Optionally run behind a VPN for IP masking (MTProxy not required).
• Data retention: message content purged after 7–14 days; metadata kept for learning.

⸻

⚙️ Operational Guidelines

Concern Best Practice
Rate limits TG Sentinel is read-only; avoid sending >20 msgs/min to self to prevent spam flag.
Session persistence Keep one .session file per account; reuse on restarts.
Crash safety Redis Streams + checkpointing ensure no message loss.
Scaling Horizontal: multiple semantic workers consuming from stream groups.
Resource use CPU < 5%, RAM ≈ 100–150 MB on typical setups.

⸻

🧪 Example Telethon Skeleton (Simplified)

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

⸻

📈 Roadmap
• Web UI for threshold tuning & feedback visualization
• On-device summarization model integration
• Multi-account support
• Distributed classifier pool
• Fine-tuning via user-tagged data

⸻

🧰 Tech Stack

Layer Technology
Ingestion Python 3 + Telethon / TDLib
Queue Redis 7 Streams
Data store SQLite
Classifier Local embeddings + optional LLM
Metrics Prometheus + Grafana
Deployment Docker / Docker Compose

⸻

📜 License

MIT License © 2025 Michael — TG Sentinel
Use at your own risk; this software interacts with the Telegram API via your own user session.

⸻

🧭 Philosophy

“Signal over noise.”
TG Sentinel is designed for professionals, developers, and analysts who need to stay informed without drowning in chatter.
It’s not another notification flood — it’s a sentinel standing guard over your attention.

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
