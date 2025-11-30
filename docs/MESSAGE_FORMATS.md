# Message Formats System

TG Sentinel uses a configurable message format system that allows customization of alert and digest message templates through YAML configuration and a web-based editor.

## Overview

The message formats system provides:

- **YAML-based templates** for DM Notification, Saved Messages, Digests, and Webhooks
- **Live preview** with sample data via `FormatterContext`
- **Test sending** to verify formats before saving
- **Export/Import** functionality for backup and sharing
- **Version control** with automatic backup before changes
- **Format Registry** for centralized template metadata and variable discovery
- **Diagnostic mode** for debugging template rendering issues

## Architecture

The message format system consists of three core components:

### FormatterContext (`message_formats/context.py`)

A unified context builder that creates rendering context dictionaries for both live preview and actual message rendering:

```python
from tgsentinel.message_formats.context import FormatterContext

# From a real delivery payload
context = FormatterContext.from_payload(delivery_payload).build()

# From sample data (for preview)
context = FormatterContext.from_sample().build()
```

### FormatRegistry (`message_formats/registry.py`)

Centralized registry of template metadata with auto-discovery of available variables:

```python
from tgsentinel.message_formats.registry import GLOBAL_REGISTRY

# Get metadata for a format type
spec = GLOBAL_REGISTRY.get_format("dm_alerts")
print(spec.description)
print(spec.variables)  # List of VariableSpec objects

# Validate a template
is_valid, errors = GLOBAL_REGISTRY.validate_template("dm_alerts", template)

# List all format types
all_formats = GLOBAL_REGISTRY.get_all_formats()
```

### LineBuildResult (`message_formats/line_builder.py`)

Optional diagnostic mode for debugging template rendering:

```python
from tgsentinel.message_formats.line_builder import build_with_diagnostics

result = build_with_diagnostics(context)
# result.lines: dict of generated *_line variables
# result.diagnostics: list of LineDiagnostic for each variable
for diag in result.diagnostics:
    print(f"{diag.variable}: source={diag.source_value}, rendered={diag.rendered}")
```

## Template Types

### DM Notification

Format for instant alerts sent to you or a target channel.

**Default template:**

```bash
**🔔 {chat_title}** (Score: {score:.2f})
{?semantic_score_line}{semantic_score_line}
{?keyword_score_line}{keyword_score_line}
**From:** {?sender_line} {?reactions_line} {?vip_line} 📅 {timestamp|relative} 🕐 {timestamp|time}
📝 {message_text}
{?profile_line}
{?triggers_line} {?message_link_line}
```

**Available variables:**

| Variable                | Description                                               |
| ----------------------- | --------------------------------------------------------- |
| `{chat_title}`          | Title of the source channel/chat                          |
| `{message_text}`        | The message content                                       |
| `{sender_name}`         | Name of the message sender                                |
| `{score}`               | Relevance score (0.0-1.0)                                 |
| `{profile_name}`        | Name of the matching profile                              |
| `{triggers}`            | Matched trigger keywords                                  |
| `{timestamp}`           | Message timestamp                                         |
| `{sender_line}`         | Pre-formatted line: `👤 {sender_name}` (optional)         |
| `{vip_line}`            | Pre-formatted line: `🧘 VIP` (optional, if sender is VIP) |
| `{triggers_line}`       | Pre-formatted line: `⚡ {triggers_formatted}` (optional)  |
| `{message_link_line}`   | Pre-formatted line: `🔗 [View]({link})` (optional)        |
| `{reactions_line}`      | Pre-formatted line: `👍 {reactions}` (optional)           |
| `{semantic_score_line}` | Pre-formatted line: `🧠 {semantic_score:.2f}` (optional)  |
| `{keyword_score_line}`  | Pre-formatted line: `🔑 {keyword_score:.2f}` (optional)   |

### Saved Messages

Format for messages saved to Telegram's Saved Messages.

**Default template:**

```markdown
**🔔 {chat_title}** (Score: {score:.2f})
{?semantic_score_line}{semantic_score_line}
{?keyword_score_line}{keyword_score_line}
**From:** {?sender_line} {?reactions_line} {?vip_line} 📅 {timestamp|relative} 🕐 {timestamp|time}
📝 {message_text}
{?profile_line}
{?triggers_line} {?message_link_line}
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
{rank}. **From:** {?sender_line} {?reactions_line} {?vip_line} 📅 {timestamp|relative} 🕐 {timestamp|time}
**{chat_title}** (Score: {score:.2f})
{?semantic_score_line}{semantic_score_line}
{?keyword_score_line}{keyword_score_line}
📝 {message_text}
{?profile_line}
{?triggers_line}{triggers_line}
{?message_link_line}{message_link_line}

---
```

> **Note:** This template uses **conditional rendering** (`{?variable}`) and **pipe formatters** (`{variable|formatter}`). See the "Format Specifiers" section below for detailed explanations.

**Additional digest entry variables:**

| Variable                | Description                                                                         |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `{rank}`                | The 1-based position of the item in the digest list (e.g., `1`, `2`, `3`)           |
| `{profile_line}`        | Pre-formatted: `🎯 {profile_name}` when a digest profile matched (optional)         |
| `{sender_line}`         | Pre-formatted: `👤 {sender_name}` (optional, includes newline when present)         |
| `{vip_line}`            | Pre-formatted: `🧘 VIP` (optional, only if sender is a VIP)                         |
| `{triggers_line}`       | Pre-formatted: `⚡ {triggers_formatted}` (optional, omitted if empty)               |
| `{message_link_line}`   | Pre-formatted: `🔗 [View](https://...)` (optional, only when `message_link` exists) |
| `{reactions_line}`      | Pre-formatted: `👍 {reactions}` (optional, only when reaction count is provided)    |
| `{semantic_score_line}` | Pre-formatted: `🧠 {semantic_score:.2f}` (optional, AI similarity score with icon)  |
| `{keyword_score_line}`  | Pre-formatted: `🔑 {keyword_score:.2f}` (optional, keyword match score with icon)   |

## Available Variables - Quick Reference

All message format templates support **formatted line variables** that are automatically generated when their base values are present. These pre-formatted variables include icons and proper formatting, making templates cleaner and more consistent.

### Formatted Line Variables (`*_line`)

These variables are **optional** and auto-generated. Use the `{?variable}` syntax to conditionally include them:

| Variable                | Format                      | When Available                          |
| ----------------------- | --------------------------- | --------------------------------------- |
| `{sender_line}`         | `👤 {sender_name}`          | When `sender_name` is present           |
| `{vip_line}`            | `🧘 VIP`                    | When `is_vip` is `true`                 |
| `{profile_line}`        | `🎯 {profile_name}`         | When `profile_name` is present (digest) |
| `{triggers_line}`       | `⚡ {triggers_formatted}`   | When triggers are matched               |
| `{message_link_line}`   | `🔗 [View]({message_link})` | When `message_link` exists              |
| `{reactions_line}`      | `👍 {reactions}`            | When `reactions` count exists           |
| `{semantic_score_line}` | `🧠 {semantic_score:.2f}`   | When `semantic_score` is available      |
| `{keyword_score_line}`  | `🔑 {keyword_score:.2f}`    | When `keyword_score` is available       |

**Usage example:**

```markdown
# Template with formatted line variables

{rank}. **{chat_title}** (Score: {score:.2f})
{?sender_line}{sender_line}{?vip_line}{vip_line}📝 {message_text}
{?triggers_line}{triggers_line}
{?semantic_score_line}{semantic_score_line}{?keyword_score_line}{keyword_score_line}
{?message_link_line}{message_link_line}{?reactions_line}{reactions_line}

# Output when all variables present:

1. **Tech News** (Score: 0.95)
   👤 Alice Smith
   🧘 VIP
   📝 Breaking: New AI model released...
   ⚡ 🔑 AI, 🔑 Apple, 🔑 iOS
   🧠 0.94
   🔑 0.82
   🔗 [View](https://t.me/c/5555555555/200)
   👍 28

# Output when only basic variables present:

1. **Tech News** (Score: 0.95)
   📝 Breaking: New AI model released...
```

> **Score rules:** semantic/keyword lines only render when their source values exist and are non-zero. These formatted lines do **not** append a newline, so add `\n` (or your own line break) wherever you need separation.

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

Preview a rendered message format. Uses `FormatterContext` internally for consistent rendering.

**Request body:**

```json
{
  "format_type": "dm_alerts",
  "template": "optional custom template",
  "sample_data": {...}
}
```

**Response:**

```json
{
  "status": "ok",
  "data": {
    "rendered": "...",
    "context": {...}
  }
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

### GET /api/message-formats/registry

Get format registry metadata (available formats, variables, descriptions).

**Response:**

```json
{
  "status": "ok",
  "data": {
    "formats": {
      "dm_alerts": {
        "description": "Format for DM alerts",
        "variables": [
          {
            "name": "chat_title",
            "description": "Title of the source channel/chat",
            "required": true
          },
          {
            "name": "sender_line",
            "description": "Pre-formatted sender line",
            "required": false
          }
        ]
      }
    }
  }
}
```

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

### Conditional Rendering: `{?variable}`

The `{?variable}` syntax enables **conditional rendering** of literal text that follows it. The text immediately after the conditional token is included in the output **only if** the variable is present and truthy (not `None`, empty string, `0`, or `False`).

**How it works:**

- `{?variable_name}` checks if `variable_name` exists and is truthy
- If truthy: the **literal text** that follows (up to the next variable or newline) is included
- If falsy or missing: the following literal text is **omitted**

**Examples:**

```markdown
# With timestamp present (e.g., "2025-12-07T10:30:00")

{?timestamp}📅 {timestamp|relative}
→ Output: "📅 2 hours ago"

# With timestamp missing (None or empty)

{?timestamp}📅 {timestamp|relative}
→ Output: "" (entire line omitted)

# Sender line conditional

{?sender_line}{sender_line}
→ If sender_line = "👤 John Doe": Output includes "👤 John Doe"
→ If sender_line = None or "": Output omits the entire line

# Profile line conditional (controls newline)

{?profile_line}
{rank}. **{chat_title}**
→ If profile_line exists: includes newline before rank
→ If profile_line is None: omits newline, rank starts immediately
```

**Common use cases:**

- `{?timestamp}📅 {timestamp|relative}` - Show timestamp emoji/label only when timestamp exists
- `{?sender_line}{sender_line}` - Include sender information only for channels/groups
- `{?triggers_line}{triggers_line}` - Show matched triggers only when present
- `{?message_link_line}{message_link_line}` - Include message link only when available
- `{?reactions_line}{reactions_line}` - Show reactions only when present

### Pipe Format Options: `{variable|formatter}`

The `{variable|formatter}` syntax applies **transformation functions** to variable values before rendering. This is especially useful for formatting timestamps, numbers, or other data types.

**Available formatters:**

#### `relative` - Relative time formatter

Converts timestamps to human-readable relative time strings (e.g., "2 hours ago", "yesterday").

```markdown
{timestamp|relative}

# Input: "2025-12-07T08:30:00" (current time: 10:30:00)

# Output: "2 hours ago"

# Input: "2025-12-06T14:00:00" (yesterday)

# Output: "yesterday at 2:00 PM"

# Input: "2025-12-01T09:00:00" (6 days ago)

# Output: "6 days ago"
```

#### `time` - Time-only formatter

Extracts just the time portion from a timestamp in 24-hour format.

```markdown
{timestamp|time}

# Input: "2025-12-07T14:30:00"

# Output: "14:30"

# Input: "2025-12-07T09:05:00"

# Output: "09:05"
```

**Combining conditionals with formatters:**

```markdown
# Show both relative and exact time, but only if timestamp exists

{?timestamp}📅 {timestamp|relative} 🕐 {timestamp|time}{?profile_line}

# With timestamp = "2025-12-07T08:30:00" (current time: 10:30:00)

→ Output: "📅 2 hours ago 🕐 08:30"

# With timestamp = None

→ Output: "" (entire line omitted)
```

**Custom formatter examples:**

```markdown
# Digest header with formatted delivery time

📬 **Daily Digest** - {delivery_time|time}
→ Output: "📬 **Daily Digest** - 09:00"

# Message with relative timestamp

📅 Received {timestamp|relative}
→ Output: "📅 Received 3 hours ago"

# Combined usage in entry template

{?timestamp}📅 {timestamp|relative} 🕐 {timestamp|time}{?profile_line}
{rank}. **{chat_title}** (Score: {score:.2f})
{?sender_line}{sender_line}📝 {message_text}

→ With all variables present:
"📅 2 hours ago 🕐 08:30

1.  **Tech News** (Score: 0.95)
    👤 Alice Smith
    📝 Breaking: New AI model released..."

→ With timestamp missing:
"1. **Tech News** (Score: 0.95)
👤 Alice Smith
📝 Breaking: New AI model released..."
```

**Formatter notes:**

- Formatters are applied **after** the variable value is retrieved
- If the variable is `None` or missing, the formatter is not applied
- Always use conditionals (`{?variable}`) when the variable might be absent
- Formatters can be chained with Python format specifiers: `{score|round:.2f}` (if custom formatters support it)

## Error Handling

The system includes robust error handling:

- **Validation** on save to prevent invalid templates (via `FormatRegistry.validate_template()`)
- **Fallback** to defaults if config is corrupted
- **Safe substitution** - unmatched placeholders remain in output
- **Automatic backup** before changes
- **Diagnostic mode** - use `build_with_diagnostics()` to trace rendering issues

## Debugging Template Issues

When templates don't render as expected, use the diagnostic mode:

```python
from tgsentinel.message_formats.line_builder import build_with_diagnostics
from tgsentinel.message_formats.context import FormatterContext

# Build context from sample or real data
ctx = FormatterContext.from_sample().build()

# Get detailed rendering diagnostics
result = build_with_diagnostics(ctx)

# Check each variable
for diag in result.diagnostics:
    if not diag.rendered:
        print(f"⚠️ {diag.variable}: source={diag.source_value}, reason: missing/empty")
    else:
        print(f"✅ {diag.variable}: {result.lines[diag.variable]}")
```

## File Structure

```text
src/tgsentinel/message_formats/
├── __init__.py          # Package exports
├── context.py           # FormatterContext - unified context builder
├── defaults.py          # SAMPLE_DATA and DEFAULT_FORMATS
├── formatter.py         # Template rendering engine
├── line_builder.py      # *_line variable generation + diagnostics
├── registry.py          # FormatRegistry + GLOBAL_REGISTRY
└── storage.py           # YAML persistence layer
```

## Best Practices

1. **Test changes** using the Preview feature before saving
2. **Export configuration** before making major changes
3. **Use sample data** to verify all variables are correctly placed
4. **Keep webhook templates** as valid JSON
5. **Use format specifiers** for consistent number formatting
6. **Use FormatterContext** for consistent context building across preview and rendering
7. **Use FormatRegistry.validate_template()** before saving custom templates
8. **Enable diagnostics** when debugging rendering issues
