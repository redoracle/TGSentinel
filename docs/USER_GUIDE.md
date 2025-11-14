# TG Sentinel — User Guide

This guide explains what TG Sentinel does and how to use it day‑to‑day. It covers first‑time setup, configuration, and all capabilities exposed in the web UI.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Configuration Basics](#configuration-basics)
- [Using the Web UI](#using-the-web-ui)
- [Login & Access Control](#login--access-control)
- [Using the Web UI](#using-the-web-ui)
- [Alert Profiles System](#alert-profiles-system)
- [Interest Profiles System](#interest-profiles-system)
- [Advanced Capabilities](#advanced-capabilities)
- [Backups and Maintenance](#backups-and-maintenance)
- [Troubleshooting](#troubleshooting)
- [Quick Reference](#quick-reference)

---

## What It Does

TG Sentinel watches your Telegram channels, groups, and private chats (with your own user session). It filters messages with smart rules and optional semantic matching, then:

- Sends you immediate alerts for important messages.
- Builds hourly/daily digests of highlights.
- Lets you browse alerts and health in a web dashboard.

Your data stays local on your machine or server.

---

## Prerequisites

- Docker with Compose v2 (recommended), or a local Python 3.11 environment.
- Telegram API credentials (a user application, not a bot): obtain `api_id` and `api_hash` at <https://my.telegram.org/auth>.
- A random secret for the UI: `python -c "import secrets; print(secrets.token_hex(32))"`.

---

## Quick Start (Docker)

1. Configure environment variables

- Copy `.env.sample` to `.env` and set:
  - `TG_API_ID`, `TG_API_HASH`
  - `UI_SECRET_KEY`
  - Optional: `ALERT_MODE` (`dm|channel|both`), `ALERT_CHANNEL`, `HOURLY_DIGEST`, `DAILY_DIGEST`, `DIGEST_TOP_N`
  - Optional: `EMBEDDINGS_MODEL` (e.g., `all-MiniLM-L6-v2`) and `SIMILARITY_THRESHOLD`

1. Start services

```bash
docker compose up --build -d
```

1. First login (one‑time)

You have two options:

- UI Login (recommended): Open the UI at <http://localhost:5001/>. If no session exists, the page locks and a login modal appears. Enter phone → Send code → Verify (add 2FA if required).
- CLI Login (fallback):

  ```bash
  docker compose exec sentinel python -m tgsentinel.main
  ```

  Follow prompts to authenticate (phone/code/2FA). A session file will be saved; press Ctrl+C when you see "Signed in".

1. Open the UI (if using CLI login)

- Go to <http://localhost:5001/> (UI service maps 5001→5000 by default).

---

## Configuration Basics

Two places control behavior:

- `.env` (environment variables): easiest way to set alert mode, digests, Redis/DB connections, and semantic options. These override YAML when overlapping.
- `config/tgsentinel.yml` (YAML): lists channels to monitor and their rules, optional monitored users for private chats, and "interests" for semantic matching.

Tips:

- To start, keep `ALERT_MODE=dm` and leave `ALERT_CHANNEL` empty.
- Add your channels by ID (negative IDs are groups/supergroups; `-100…` is common).
- If you only care about a few private chats, add them under "Monitored Users".

---

## Login & Access Control

- If a session is missing or has been cleared via re‑login, the **entire UI is locked** until you authenticate. A login modal opens automatically and the background becomes inert and dimmed.
- After successful login:
  - The worker reloads configuration and reconnects to MTProto automatically (no container restart required).
  - The header (Analyst) updates with your username, phone (masked), and avatar.
- If a code expires or the verification context is missing, the modal shows a **Resend code** CTA with a short cooldown to prevent spam.
- You can always trigger re‑login with the header button: **Re‑login / Switch Account**.

Note: The "Refresh Session Info" button has been removed. Session details auto‑refresh after login/re‑login.

### UI Lock (Local Screen Lock)

You can temporarily lock the UI without logging out of Telegram. This is useful for preventing unintended access while stepping away.

- Configure env vars:
  - `UI_LOCK_PASSWORD` (optional; empty disables password check)
  - `UI_LOCK_TIMEOUT` (seconds; default 900) for idle auto‑lock
- Click the lock icon next to “Re‑login / Switch Account” to lock immediately.
- When the lock engages, the UI shows an unlock prompt/modal. Enter the password to continue.
- Lock/unlock does not change your Telegram connectivity or session file.

## Using the Web UI

Navigation lives at the top. Pages are described below.

### Dashboard

- Summary: messages ingested and alerts sent over the last 24 hours; average importance.
- Live Activity: recent messages from the queue (when Redis is connected).
- Health: Redis stream depth, DB size, and process metrics.

### Alerts

- Recent Alerts: table of alerted items with channel, sender, excerpt, score, trigger, and time.
- Daily Digests: mini‑timeline with counts and average score per day.
- Export: click "Export Alerts" (CSV) via API `/api/export_alerts`. When data is present, headers are machine‑friendly (for CSV parsers); when empty, headers are human‑friendly.
- Feedback: 👍/👎 per alert records preferences for future tuning.

### Config

- Telegram Account: shows masked phone, session path, and connected chats.
- Alerts & Notifications: choose `dm`, `channel`, or `both`; set target channel; set hourly/daily digests and Top‑N.
- Channels Management:
  - Add channels using the "+ ADD" flow (discover via `/api/telegram/chats`).
  - Each channel has: keywords, VIP senders, reaction/reply thresholds, and rate limit per hour.
  - Delete a channel from the table (atomic YAML update, no restart).
- Private Users Management:
  - Add private chat users to monitor (`/api/telegram/users` helps discovery).
  - Delete users from the list to stop monitoring those private chats.
- Save Configuration: writes YAML atomically and hot‑reloads the worker.
- Clean Database: clears message and feedback tables and Redis stream/cache.
- Restart Sentinel: tries a container restart (`docker-compose restart sentinel`).

### Analytics

- Live metrics: messages/min, semantic latency, CPU/memory, Redis depth.
- Keyword frequency across configured channels.
- Anomalies (24h): high volume / high importance / high alert‑rate signals.

**Anomaly Detection Configuration:**

Anomaly thresholds can be customized via environment variables:

```bash
# Standard deviation mode (recommended)
ANOMALY_USE_STDDEV=true
ANOMALY_STDDEV_MULTIPLIER=2.0

# Or use fixed thresholds
ANOMALY_VOLUME_THRESHOLD=50
ANOMALY_IMPORTANCE_THRESHOLD=3.0
ANOMALY_ALERT_RATE=0.3
```

### Profiles (Interests)

- Profiles are named "interest" definitions persisted to `data/profiles.json`.
- Actions:
  - Toggle enabled/disabled.
  - View/edit: description, positive/negative samples, threshold, weight, keywords/channels/tags, notify‑always, include‑digest.
  - Rename (save with new name; original deleted).
  - Export/Import: YAML file of interests.
  - Test: sample text vs. interest returns a similarity score.
- Backwards‑compatible with legacy `interests[]` from YAML — the UI will auto‑create a profile for a legacy interest when needed.

### Console

- Diagnostics export: download anonymized JSON snapshot with summary, health, channel counts, and recent alerts metadata.
- Realtime log stream placeholder (Socket.IO).

### Docs

- Shows API base URL and links to documentation.

---

## Alert Profiles System

TGSentinel implements a comprehensive alert system based on **10 categories of important messages**, providing intelligent detection across multiple contexts including private chats, group discussions, and channel announcements.

### The 10 Categories of Important Messages

#### 1. Messages Requiring Direct Action ⚡

##### Highest-priority items that demand immediate response

Detection Methods:

- **Private chats** get automatic priority boost (+0.5 score)
- **Direct questions** in private chats (+1.2): "Can you...?", "Could you...?", "I need..."
- **Action keywords** (+1.0 for private, +0.8 for groups):
  - Request verbs: "can you", "could you", "please", "need you to"
  - Urgency markers: "urgent request", "asap", "immediately"
  - Coordination: "confirm", "appointment", "meeting", "schedule", "deadline"

Configuration:

```yaml
channels:
  - id: 12345
    name: "My Channel"
    action_keywords:
      - "can you"
      - "need help with"
      - "please review"
      - "asap"
```

#### 2. Decisions, Voting, and Direction Changes 🗳️

##### Critical in group or community contexts

Detection Methods:

- **Decision keywords** (+1.1):
  - Governance: "poll", "vote", "voting", "proposal"
  - Outcomes: "approved", "rejected", "consensus", "resolution"
  - Changes: "policy change", "new rule", "updated procedure"
- **Polls detected** automatically (+1.0)

Configuration:

```yaml
channels:
  - id: 12345
    decision_keywords:
      - "vote"
      - "proposal"
      - "governance"
    detect_polls: true # Default: true
```

#### 3. Direct Mentions and Replies 📢

##### Always high-value - easy to detect

Detection Methods:

- **Direct mentions** (+2.0) - HIGHEST PRIORITY
- **Replies to your messages** (+1.5)
- **Threaded discussions** where you're quoted

Notes:

- Automatically detected via Telegram's `mentioned` flag
- No configuration needed - always active

#### 4. Messages With Key Importance Indicators 🚨

##### Semantic detection of importance

Detection Methods:

- **Urgency keywords** (+1.5 - highest priority):
  - "urgent", "emergency", "critical", "immediate", "asap"
  - "breaking", "alert", "warning", "attention required"
- **Importance keywords** (+0.9):
  - "important", "crucial", "essential", "significant"
  - "heads up", "fyi", "please note", "be aware"
  - "update:", "notice:", "announcement:"

Configuration:

```yaml
channels:
  - id: 12345
    urgency_keywords:
      - "urgent"
      - "critical"
      - "breaking"
    importance_keywords:
      - "important"
      - "announcement"
      - "must read"
```

#### 5. Updates Related to Your Interests or Projects 📦

##### Tailored to your ecosystem

Detection Methods:

- **Release keywords** (+0.8):
  - Version indicators: "v1.", "v2.", "release", "version"
  - Announcements: "update available", "changelog", "launched"
- **Security keywords** (+1.2 - high priority):
  - Vulnerabilities: "CVE", "vulnerability", "exploit", "zero-day"
  - Incidents: "breach", "hack", "compromised", "malware"
  - Protection: "patch", "fix", "advisory"

Configuration:

```yaml
channels:
  - id: 12345
    name: "Algorand Dev"
    release_keywords:
      - "v1."
      - "new release"
      - "changelog"
      - "deployment"
    security_keywords:
      - "CVE"
      - "vulnerability"
      - "patch"
      - "advisory"
```

#### 6. Messages Containing Structured or Sensitive Data 🔐

##### High-risk, high-value content

Detection Methods:

- **OTP/Code detection** (+1.3):
  - 6-digit codes: `\b\d{6}\b`
  - Phrases: "verification code", "OTP", "one-time", "passcode", "token"
- **Media attachments** (+0.7):
  - Documents, PDFs, contracts
  - Photos (screenshots)
  - Voice messages
  - Video notes

Configuration:

```yaml
channels:
  - id: 12345
    detect_codes: true # Default: true
    detect_documents: true # Default: true
```

#### 7. Personal Context Changes 💬

##### From private chats or small groups

Detection Methods:

- **Private chat boost** (+0.5 automatically applied)
- Enhanced scoring for all heuristics in 1-on-1 conversations
- Questions in private chats get higher priority (+1.2 vs +0.8)

Notes:

- Detected automatically based on chat_id (positive = private)
- Future enhancement: rare sender detection (low-frequency contacts)

#### 8. Risk or Incident-Related Messages ⚠️

##### Safety and conflict detection

Detection Methods:

- **Risk keywords** (+1.0):
  - Problems: "danger", "risk", "problem", "issue", "bug", "error"
  - Incidents: "outage", "incident", "failure", "escalation"
  - Legal: "legal", "lawsuit", "violation", "banned", "suspended"

Configuration:

```yaml
channels:
  - id: 12345
    name: "Security Feeds"
    risk_keywords:
      - "breach"
      - "compromised"
      - "incident"
      - "outage"
      - "critical error"
```

#### 9. Opportunity-Driven Messages 🎁

##### Not urgent, but high-value

Detection Methods:

- **Opportunity keywords** (+0.6):
  - Invitations: "invitation", "invite", "exclusive"
  - Professional: "opportunity", "position", "hiring", "job"
  - Early access: "beta", "early access", "limited", "presale"
  - Offers: "discount", "free", "giveaway", "contest"

Configuration:

```yaml
channels:
  - id: 12345
    name: "Wu Blockchain News"
    opportunity_keywords:
      - "airdrop"
      - "presale"
      - "early access"
      - "beta"
```

#### 10. Meta-Important Messages (MTProto Metadata) 📌

##### Structural importance signals

Detection Methods:

- **Pinned messages** (+1.2)
- **Admin/Moderator messages** (+0.9)
- **Polls** (+1.0)
- **High engagement** (legacy):
  - Reactions threshold (+0.5)
  - Replies threshold (+0.5)

Configuration:

```yaml
channels:
  - id: 12345
    prioritize_pinned: true # Default: true
    prioritize_admin: true # Default: true
    detect_polls: true # Default: true
    reaction_threshold: 5
    reply_threshold: 3
```

### AI Scoring Principle

A message is considered important if it:

1. **Affects you directly** (mentions, replies, private messages)
2. **Changes state** (decisions, plans, tasks, releases)
3. **Carries risk** (security, incidents, problems)
4. **Reduces uncertainty** (announcements, updates, clarifications)
5. **Creates opportunity** (invitations, offers, early access)

### Scoring Summary

| Trigger Type              | Score | Priority  |
| ------------------------- | ----- | --------- |
| Direct Mention            | +2.0  | Highest   |
| Reply to You              | +1.5  | Highest   |
| Urgency Keywords          | +1.5  | Highest   |
| OTP/Codes Detected        | +1.3  | Very High |
| Security Keywords         | +1.2  | Very High |
| Pinned Message            | +1.2  | Very High |
| Direct Question (private) | +1.2  | Very High |
| Decision Keywords         | +1.1  | High      |
| VIP Sender                | +1.0  | High      |
| Action Required           | +1.0  | High      |
| Risk Keywords             | +1.0  | High      |
| Poll Detected             | +1.0  | High      |
| Admin Message             | +0.9  | High      |
| Importance Keywords       | +0.9  | High      |
| Release Keywords          | +0.8  | Medium    |
| Custom Keywords           | +0.8  | Medium    |
| Media (documents)         | +0.7  | Medium    |
| Opportunity Keywords      | +0.6  | Medium    |

### Alert Profile Backtesting

Test your profiles against historical messages to validate effectiveness:

#### Via API

```bash
curl -X POST http://localhost:5001/api/profiles/alert/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "id": "channel_-100123456789",
    "hours_back": 24,
    "max_messages": 100
  }' | jq
```

#### Example Result

```json
{
  "status": "ok",
  "profile_name": "Algorand Dev",
  "stats": {
    "total_messages": 87,
    "matched_messages": 15,
    "match_rate": 17.2,
    "precision": 86.7,
    "true_positives": 13,
    "false_positives": 2
  },
  "recommendations": [
    "🎯 Good precision - profile is well-tuned",
    "Consider adding more security keywords"
  ],
  "matches": [
    {
      "chat_title": "Algorand Dev",
      "score": 3.2,
      "triggers": ["keywords:urgent,CVE", "security"],
      "text_preview": "Critical vulnerability found in...",
      "would_alert": true
    }
  ]
}
```

#### Understanding Results

**Match Rate:**

- **< 5%**: Too restrictive, may miss important messages
- **5-20%**: Good balance (recommended)
- **> 40%**: Too broad, likely many false positives

**Precision:**

- **> 85%**: Excellent - profile is well-tuned
- **70-85%**: Good - minor refinement recommended
- **< 70%**: Needs work - add negative examples or tighten keywords

### Alert Profile Management

#### Create/Update Profile

```bash
curl -X POST http://localhost:5001/api/profiles/alert/upsert \
  -H "Content-Type: application/json" \
  -d '{
    "id": "channel_-100123456789",
    "name": "Algorand Dev",
    "type": "channel",
    "channel_id": -100123456789,
    "enabled": true,
    "security_keywords": ["CVE", "vulnerability", "exploit", "zero-day"],
    "urgency_keywords": ["urgent", "critical", "breaking", "emergency"],
    "release_keywords": ["v1.", "v2.", "release", "hotfix"],
    "vip_senders": [11111, 22222],
    "reaction_threshold": 8,
    "reply_threshold": 10,
    "detect_codes": true,
    "prioritize_pinned": true,
    "rate_limit_per_hour": 5
  }'
```

**What happens next:**

1. Profile saved to `data/alert_profiles.json`
2. Synced to `config/tgsentinel.yml`
3. Reload marker touched
4. Sentinel picks up changes automatically (within 5 seconds)
5. New rules active!

#### Disable Profile Temporarily

```bash
curl -X POST http://localhost:5001/api/profiles/alert/toggle \
  -H "Content-Type: application/json" \
  -d '{"id": "channel_-100123456789", "enabled": false}'
```

---

## Interest Profiles System

Interest profiles use semantic/AI-based matching for global topic detection across all channels.

### Features

- **Global semantic understanding** - works across all channels
- **ML model training** with positive/negative examples
- **Context and meaning detection** - not just keywords
- **Configurable thresholds** and weights

### Testing Interest Profiles

```bash
curl -X POST http://localhost:5001/api/profiles/interest/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "name": "algorand core development",
    "hours_back": 24,
    "max_messages": 100
  }' | jq
```

### Managing Interest Profiles

Via the Profiles page (`/profiles`):

- Toggle enabled/disabled
- View/edit description, samples, threshold, weight
- Rename profiles
- Export/Import YAML
- Test sample text against profile

**Data persistence:** `data/profiles.json` (YAML format with file locking)

---

## Advanced Capabilities

- **Participants Info**: the UI can fetch channel/user details on demand via the worker (Redis‑mediated request/response cache). Useful for contextual data like roles and rights.
- **Webhooks**: configure third‑party webhook targets in `config/webhooks.yml` via UI API:
  - List: `GET /api/webhooks`
  - Create: `POST /api/webhooks` (service, url, secret)
  - Delete: `DELETE /api/webhooks/<service_name>`
  - Secrets are masked when reading back.

---

## Backups and Maintenance

Files to back up:

- `data/tgsentinel.session` (Telegram login session)
- `data/sentinel.db` (alerts/history)
- `config/tgsentinel.yml` (rules) and `.env` (secrets)
- `data/profiles.json` (profiles store)
- `data/alert_profiles.json` (alert profiles store)

Housekeeping:

- Clean DB from Config page when you want a fresh start.
- Keep embeddings disabled if you prefer lower resource usage.

---

## Troubleshooting

- **No alerts arriving:**
  - Ensure your channels exist in YAML and the IDs are correct.
  - If using `channel`/`both`, set a valid `ALERT_CHANNEL`.
  - Check `docker compose logs -f sentinel` for errors.
- **Stuck on login or "session not authorized":**
  - Re‑run `docker compose exec sentinel python -m tgsentinel.main`.
  - If needed, remove old session files under `data/` and re‑authenticate.
- **High CPU/memory:**
  - Clear `EMBEDDINGS_MODEL` to disable semantic scoring.
  - Reduce number of interests and/or tighten channel rules.
- **Redis offline:**
  - Live feed may be limited; UI still shows DB‑based data.

---

## Quick Reference

Useful commands:

```bash
# Start/stop
docker compose up -d
docker compose down

# Logs
docker compose logs -f sentinel
docker compose logs -f ui

# Restart core after changes
docker compose restart sentinel

# Run tests
docker compose run --rm sentinel python -m pytest -q
```

Entity IDs reminder:

- Users have positive IDs.
- Groups/channels are negative; supergroups/channels usually look like `-100…`.

That's it — you're ready to use TG Sentinel day‑to‑day via the web UI.
