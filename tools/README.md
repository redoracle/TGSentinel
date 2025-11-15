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
# 1. Generate session file
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

### `populate_history.py`

Populate Redis with historical message data for testing and development.

### `simulate_digest_from_history.py`

Simulate digest generation from historical data without live Telegram connection.

### `test_digest.py`

Test digest generation logic independently.

### `run_tests.py`

Run the full test suite with coverage reporting.

**Usage:**

```bash
python tools/run_tests.py
```

### `verify_config_ui.py`

Verify configuration UI endpoints and data integrity.

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
