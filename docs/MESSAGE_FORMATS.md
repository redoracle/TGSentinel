# Message Formats System

TG Sentinel uses a configurable message format system that allows customization of alert and digest message templates through YAML configuration and a web-based editor.

## Overview

The message formats system provides:

- **YAML-based templates** for DM alerts, Saved Messages, Digests, and Webhooks
- **Live preview** with sample data
- **Test sending** to verify formats before saving
- **Export/Import** functionality for backup and sharing
- **Version control** with automatic backup before changes

## Template Types

### DM Alerts

Format for instant alerts sent to you or a target channel.

**Default template:**

```bash
🔔 {chat_title}
{message_text}
```

**Available variables:**

| Variable         | Description                      |
| ---------------- | -------------------------------- |
| `{chat_title}`   | Title of the source channel/chat |
| `{message_text}` | The message content              |
| `{sender_name}`  | Name of the message sender       |
| `{score}`        | Relevance score (0.0-1.0)        |
| `{profile_name}` | Name of the matching profile     |
| `{triggers}`     | Matched trigger keywords         |
| `{timestamp}`    | Message timestamp                |

### Saved Messages

Format for messages saved to Telegram's Saved Messages.

**Default template:**

```markdown
**🔔 Alert from {chat_title}**

**Score:** {score:.2f}
**From:** {sender_name}

{message_text}

{triggers_formatted}
```

### Digest Header

Header shown at the top of digest messages.

**Default template:**

```bash
🗞️ **Digest — Top {top_n} messages from {channel_count} channels**
📅 {schedule} | Profile: {profile_name}
━━━━━━━━━━━━━━━━━━━━
```

**Available variables:**

| Variable          | Description                     |
| ----------------- | ------------------------------- |
| `{top_n}`         | Number of top messages included |
| `{channel_count}` | Number of unique channels       |
| `{schedule}`      | Digest schedule (hourly/daily)  |
| `{profile_name}`  | Name of the digest profile      |
| `{timestamp}`     | Digest generation timestamp     |

### Digest Entry

Format for each message in the digest.

**Default template:**

```markdown
{rank}. **{chat_title}** (Score: {score:.2f})
👤 {sender_name}
📝 {message_preview}
{trigger_display}

---
```

### Webhook Payload

JSON structure sent to webhook endpoints.

**Default template:**

```json
{
  "event": "alert",
  "chat_title": "{chat_title}",
  "sender_name": "{sender_name}",
  "message_text": "{message_text}",
  "score": {score},
  "profile_name": "{profile_name}",
  "triggers": {triggers_json},
  "timestamp": "{timestamp}"
}
```

## Configuration

### YAML File Location

Templates are stored in: `config/message_formats.yml`

The file is automatically created with defaults if it doesn't exist.

### Example Configuration

```yaml
version: "1.0"
dm_alerts:
  template: "🔔 {chat_title}\n{message_text}"
  description: Format for direct message alerts
  variables:
    chat_title: Title of the source channel/chat
    message_text: The message content

saved_messages:
  template: |
    **🔔 Alert from {chat_title}**

    **Score:** {score:.2f}
    **From:** {sender_name}

    {message_text}

digest:
  header:
    template: "🗞️ **Digest — Top {top_n}**\n{schedule} | {profile_name}"
  entry:
    template: |
      {rank}. **{chat_title}** ({score:.2f})
      {message_preview}
```

## API Endpoints

### GET /api/message-formats

Get current message format templates.

**Response:**

```json
{
  "status": "ok",
  "data": {
    "formats": {...},
    "defaults": {...},
    "sample_data": {...}
  }
}
```

### PUT /api/message-formats

Update message format templates.

**Request body:**

```json
{
  "formats": {
    "dm_alerts": {"template": "..."},
    ...
  }
}
```

### POST /api/message-formats/preview

Preview a rendered message format.

**Request body:**

```json
{
  "format_type": "dm_alerts",
  "template": "optional custom template",
  "sample_data": {...}
}
```

### POST /api/message-formats/test

Send a test message using the specified format.

### GET /api/message-formats/export

Export formats as downloadable YAML file.

### POST /api/message-formats/import

Import formats from uploaded YAML file.

### POST /api/message-formats/reset

Reset formats to defaults (requires admin auth).

## UI Editor

Access the Message Formats Editor at: **Developer → Message Formats**

Features:

- **Monaco Editor** with syntax highlighting
- **Live preview** with sample data
- **Test sending** to Saved Messages
- **Export/Import** YAML files
- **Reset to defaults** with backup

## Format Specifiers

Templates support Python-style format specifiers:

- `{score:.2f}` - Float with 2 decimal places
- `{rank:02d}` - Integer with zero padding

## Error Handling

The system includes robust error handling:

- **Validation** on save to prevent invalid templates
- **Fallback** to defaults if config is corrupted
- **Safe substitution** - unmatched placeholders remain in output
- **Automatic backup** before changes

## Best Practices

1. **Test changes** using the Preview feature before saving
2. **Export configuration** before making major changes
3. **Use sample data** to verify all variables are correctly placed
4. **Keep webhook templates** as valid JSON
5. **Use format specifiers** for consistent number formatting
