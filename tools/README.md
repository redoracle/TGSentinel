# TG Sentinel Tools

Utility scripts for development, testing, and session management.

## Session Management

### `generate_session.py`

Generate a portable Telegram session file that can be uploaded to the TG Sentinel UI for authentication without SMS codes.

**Usage:**

```bash
python tools/generate_session.py --phone +1234567890
```

**Features:**

- Interactive authentication flow (SMS code + optional 2FA)
- Generates valid Telethon session files
- Verifies session integrity before saving
- Creates portable files that work across installations
- No rate limit impact (uses your own credentials)

**Options:**

```bash
--phone      Phone number in international format (required)
--output     Output path (default: ./tgsentinel_upload.session)
--api-id     Telegram API ID (default: from TG_API_ID env)
--api-hash   Telegram API hash (default: from TG_API_HASH env)
```

**Example Workflow:**

```bash
# 1. Generate session file (requires venv activation)
source .venv/bin/activate
python tools/generate_session.py --phone +1234567890

# 2. Enter SMS code when prompted
# 3. Enter 2FA password if enabled
# 4. Session saved to: ./tgsentinel_upload.session

# 5. Upload to TG Sentinel:
#    - Open UI → Authenticate Telegram Session
#    - Click "Upload Session" tab
#    - Select the .session file
#    - Click "Upload & Restore"
```

**Benefits:**

- ✅ Avoid SMS code rate limits
- ✅ Quick account switching
- ✅ Backup/restore capability
- ✅ Development/testing environments
- ✅ Session portability across servers

**Security Notes:**

- Session files contain your authorization key
- Keep them secure (treat like passwords)
- Don't share or commit to version control
- Add `*.session` to `.gitignore`

---

## Rate Limit Management

### `check_rate_limit.py`

Check current Telegram rate limit status for authentication operations.

**Usage:**

```bash
# Requires virtual environment activation
source .venv/bin/activate
python tools/check_rate_limit.py
```

**Shows:**

- Worker authorization status
- Active rate limits per action (send_code, resend_code, sign_in)
- Time remaining until rate limits expire
- Redis TTL information

**Example Output:**

```bash
Worker Status:
  Authorized: True
  Status: authorized
  Timestamp: 2025-11-15 10:30:45

Rate Limits:
  send_code: 49585 seconds remaining (~13.8 hours)
    Expires: 2025-11-16 00:06:30

No other rate limits active
```

---

## Testing & Development

### `run_tests.py`

Run the full test suite with coverage reporting.

**Usage:**

```bash
# Recommended: Use Makefile (handles venv automatically)
make test

# Or activate venv manually
source .venv/bin/activate
python tools/run_tests.py
```

Executes all unit tests, integration tests, and contract tests with detailed coverage metrics.

---

### `simulate_populate_history.py`

Populate Redis with historical messages from monitored channels.

**Usage:**

```bash
# Requires virtual environment activation
source .venv/bin/activate
python tools/simulate_populate_history.py [--limit 100] [--channel-id CHANNEL_ID]
```

**Description:**

Fetches the latest messages from configured Telegram channels and populates Redis as if they were just received. Useful for testing the importance scoring, semantic analysis, and alert generation systems.

---

### `simulate_digest_from_history.py`

Simulate digest generation using historical messages that were populated into Redis.

**Usage:**

```bash
# Requires virtual environment activation
source .venv/bin/activate
python tools/simulate_digest_from_history.py [--hours 24] [--top-n 10] [--limit 500]
```

**Description:**

Reads recent entries from the configured Redis stream, scores them with the same heuristics used by the worker (but without sending per-message alerts), writes important ones into the DB as alerted, and finally composes and sends a digest.

**Typical Flow:**

1. Run `simulate_populate_history.py` to seed the Redis stream from Telegram history
2. Run this script to score + mark messages and send a digest

---

### `simulate_digest.py`

Test script to trigger digest delivery immediately with test messages.

**Usage:**

```bash
# Requires virtual environment activation
source .venv/bin/activate
python tools/simulate_digest.py
```

**Description:**

Creates test messages in the database and immediately sends a digest. Useful for testing the digest delivery mechanism without waiting for real messages.

---

### `simulate_live_feed.py`

Simulate a live Telegram feed by sending messages to users, groups, or channels.

**Two modes available:**

1. **Single-session mode (recommended):** Send messages from one authenticated account to any target. You only need one session file and a target identifier.

2. **Dual-session mode (legacy):** Bidirectional messaging between two accounts you control. Requires two session files.

**Single-session examples:**

```bash
# Send to a user by username
python tools/simulate_live_feed.py \
    --session ./my_account.session \
    --target @username \
    --count 10

# Send to a channel by link
python tools/simulate_live_feed.py \
    --session ./my_account.session \
    --target https://t.me/channelname \
    --messages-file ./test_messages.txt

# Send to a user by phone number (must be in contacts)
python tools/simulate_live_feed.py \
    --session ./my_account.session \
    --target +1234567890 \
    --interval 3 \
    --count 5

# Send to a channel/group by numeric ID
python tools/simulate_live_feed.py \
    --session ./my_account.session \
    --target -1001234567890 \
    --count 20
```

**Dual-session example (bidirectional):**

```bash
python tools/simulate_live_feed.py \
    --session-a ./account_a.session \
    --session-b ./account_b.session \
    --direction both \
    --count 10
```

**Using a text file of messages:**

```bash
python tools/simulate_live_feed.py \
    --session ./my_account.session \
    --target @username \
    --messages-file ./messages.txt
```

**Target formats (single-session mode):**

| Format     | Example                | Description                             |
| ---------- | ---------------------- | --------------------------------------- |
| @username  | `@johndoe`             | Username (user, bot, or public channel) |
| +phone     | `+1234567890`          | Phone number (must be in your contacts) |
| t.me link  | `https://t.me/channel` | Telegram link (channel, group, or user) |
| Numeric ID | `-1001234567890`       | Channel/supergroup ID (negative)        |
| User ID    | `1234567890`           | User ID (positive)                      |

**Description:**

Useful for testing TG Sentinel's ingestion using real Telegram traffic without needing two separate accounts.

**Common options:**

- `--session`: Path to session file (single-session mode)
- `--target`: Target to send to (single-session mode)
- `--session-a` / `--session-b`: Session files (dual-session mode)
- `--direction`: `a-to-b`, `b-to-a`, or `both` (dual-session mode)
- `--api-id` / `--api-hash`: Telegram API credentials (or use env vars)
- `--interval`: Seconds between messages (default: 5)
- `--count`: Number of messages to send (default: 20)
- `--messages-file`: Text file with messages (one per line)
- `--prefix`: Message prefix for auto-generated messages (default: 'TEST')

---

## Migration & Maintenance

### `migrate_profiles.py`

Migration script to convert old-style keywords to two-layer profiles.

**Usage:**

```bash
# Dry run (preview changes)
python tools/migrate_profiles.py --config config/tgsentinel.yml --dry-run

# Apply changes
python tools/migrate_profiles.py --config config/tgsentinel.yml --apply
```

**Description:**

This script:

1. Analyzes existing keywords in all channels/users
2. Groups them into logical profiles (security, releases, opportunities, etc.)
3. Generates profiles.yml with global profiles
4. Updates tgsentinel.yml to bind profiles instead of duplicating keywords
5. Creates backups before making changes

---

## Formatting

### `format.sh`

Format Python code using black and isort.

**Usage:**

```bash
bash tools/format.sh
```

---

## Getting API Credentials

To use `generate_session.py`, you need Telegram API credentials:

1. Go to <https://my.telegram.org/apps>
2. Log in with your phone number
3. Click "API development tools"
4. Create an app (or use existing)
5. Copy the `api_id` and `api_hash`

**Set as environment variables:**

```bash
export TG_API_ID=12345678
export TG_API_HASH=abcdef1234567890abcdef1234567890
```

Or provide via command line:

```bash
python tools/generate_session.py \
  --phone +1234567890 \
  --api-id 12345678 \
  --api-hash abcdef1234567890abcdef1234567890
```

---

## Tips

### Backup Your Session

```bash
# Generate and backup
python tools/generate_session.py --phone +1234567890 --output backup.session
cp backup.session ~/safe-location/
```

### Test Before Upload

The script automatically verifies the session file after creation. Look for:

```bash
✓ Session file is valid and can be uploaded to TG Sentinel
```

### Multiple Accounts

```bash
# Account 1
python tools/generate_session.py --phone +1111111111 --output account1.session

# Account 2
python tools/generate_session.py --phone +2222222222 --output account2.session
```

Then upload whichever account you want to use in TG Sentinel.

---

## Troubleshooting

### telethon is not installed

```bash
pip install telethon
```

### API ID and API Hash are required

- Set `TG_API_ID` and `TG_API_HASH` environment variables
- Or provide via `--api-id` and `--api-hash` flags

### SessionPasswordNeededError

- This means 2FA is enabled
- The script will automatically prompt for your 2FA password

### Session file verification failed

- The session may not have been saved properly
- Try running the script again
- Check file permissions in the output directory

### Rate limit exceeded

- Use an existing session file if available
- Wait for the rate limit to expire (check with `check_rate_limit.py`)
- Use a different phone number for testing

---

## File Locations

After running `generate_session.py`:

- Default output: `./tgsentinel_upload.session` (current directory)
- Temporary files: Automatically cleaned up
- No files left in `~/.telethon/` directory

**Recommended:** Store session files outside the repository:

```bash
python tools/generate_session.py \
  --phone +1234567890 \
  --output ~/Documents/telegram_sessions/tgsentinel.session
```
