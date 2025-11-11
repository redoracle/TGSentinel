# TG Sentinel Docker Usage Guide

This guide covers running TG Sentinel end-to-end with Docker, including first-time setup, configuration, testing, and ongoing operations.

## 1. Prerequisites

- Docker Engine 24+ and Docker Compose v2
- Telegram API credentials with user-session access (not a bot)
- Basic familiarity with shell commands

## 2. Clone the Repository

```bash
git clone https://github.com/your-org/TGSentinel.git
cd TGSentinel
```

## 3. Configuration

### 3.1 Telegram API Credentials

You must register as a Telegram developer to obtain `api_id` and `api_hash`.

### 3.2 Environment Variables

Copy `.env.sample` to `.env` and adjust the values:

```bash
cp .env.sample .env
```

Key variables:

- `TG_API_ID` / `TG_API_HASH`: Telegram API credentials
- `REDIS_HOST` / `REDIS_PORT`: Redis connection
- `DB_URI`: Storage backend (defaults to SQLite under `./data`)
- `EMBEDDINGS_MODEL`: Optional Sentence-Transformers model to enable semantic scoring
- `SIMILARITY_THRESHOLD`: Minimum semantic similarity needed to auto-alert

### 3.3 YAML App Configuration

`config/tgsentinel.yml` defines channel-specific rules:

- `telegram.session`: Path to the Telethon session file (auto-created after first login)
- `alerts`: Delivery mode (DM/channel/both) and digest schedule
- `channels`: Per-chat rules for VIP senders, keyword triggers, rate limits, and thresholds
- `interests`: Topics used for semantic similarity when embeddings are enabled

Adjust this file to match the channels you want to monitor.

## 4. Launch with Docker Compose

1. Build the image and start Redis plus TG Sentinel in the background:

   ```bash
   docker compose up --build -d
   ```

   The Compose file mounts `./config` and `./data` so configuration and state persist on the host.

2. Complete the first-time Telegram login inside the running container:

   ```bash
   docker compose exec sentinel python -m tgsentinel.main
   ```

   Follow the prompts to enter your phone number and verification codes. Telethon writes the session file to the `telegram.session` path in `config/tgsentinel.yml` (default `data/tgsentinel.session`). When you see “Signed in successfully,” press `Ctrl+C` to exit.

3. Restart the application container so it runs headless using the saved session:

   ```bash
   docker compose restart sentinel
   ```

   Future restarts only require `docker compose up -d`—no additional login unless you revoke the session.

## 5. Operating the Stack

- **Stream logs**

  ```bash
  docker compose logs -f sentinel
  ```

- **Stop services**

  ```bash
  docker compose down
  ```

- **Apply image or dependency updates**

  ```bash
  docker compose pull
  docker compose build --no-cache
  docker compose up -d
  ```

## 6. Run Tests inside Docker

Execute the pytest suite using the application image:

```bash
docker compose run --rm sentinel python -m pytest -q
```

The repository is bind-mounted into the container, so tests operate on your working copy.

## 7. Monitoring & Metrics

TG Sentinel emits log-based metrics such as `metric alerts_total{chat=-100123456789} 5 ts=...`. Forward container logs to your aggregation system, or extend `metrics.py` to export to Prometheus or another backend.

## 8. Maintenance Tips

- Keep `.env`, `config/tgsentinel.yml`, and the `data/` directory under backup—they hold credentials, rules, and runtime state.
- Adjust rules or interests by editing the YAML file, then reload with `docker compose restart sentinel`.
- If you enable embeddings, ensure the specified model fits within your system’s memory/CPU budget.

With this setup, TG Sentinel runs fully in Docker, simplifying upgrades, restarts, and deployment across hosts.
