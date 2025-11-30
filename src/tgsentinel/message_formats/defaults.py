"""
Default message format templates.

These defaults are used when no custom formats are configured,
or when a specific format is missing from the YAML configuration.

Template Syntax:
- {var} - Required variable
- {var:.2f} - With format specifier (e.g., 2 decimal places)
- {?var} - Optional: renders empty if var is None/missing
- {?var:.2f} - Optional with format specifier
- {var|filter} - Apply filter to value
- {?var|filter:.2f} - Combine all features

Available Filters:
- |date - Format timestamp as "Jan 15, 2024"
- |time - Format timestamp as "14:30"
- |datetime - Format as "Jan 15, 2024 14:30"
- |relative - Format as "2h ago", "3d ago"
- |link - Format URL as Markdown link "[View](url)"
- |upper, |lower, |title - Text case transforms

Examples:
- {?semantic_score:.2f} - Show semantic score with 2 decimals, or nothing if missing
- {timestamp|relative} - Show "2h ago" instead of ISO timestamp
- {message_link|link} - Show "[View](https://t.me/...)" clickable link
- 🚀 {?semantic_score:.2f} 🔑 {?keyword_score:.2f} - Only shows scores that exist
"""

# Default format templates - these match the current production config
DEFAULT_FORMATS = {
    "version": "1.0",
    "dm_alerts": {
        "template": """
🔔 {chat_title} 📅 {timestamp|relative} 🕐 {timestamp|time}

👤 {sender_name} 🧘 vip: {is_vip}
📝 {message_text}
🔗 {message_link|link} 👍 reactions: {reactions}

🎯 {profile_name}
⚡ {triggers} 🧠 {?semantic_score:.2f} 🔑 {?keyword_score:.2f} 📊 {score:.2f}
""",
        "description": "Format for direct message alerts sent to user",
        "variables": {
            # Message / Chat Metadata
            "chat_id": "Chat or channel numeric ID",
            "chat_title": "Title of the channel or chat",
            "msg_id": "Message ID within the chat",
            "message_link": "Link to original message (use {message_link|link} for clickable Markdown link)",
            "message_text": "Full message content",
            "message_preview": "Truncated preview of message (configurable length)",
            "timestamp": "Message timestamp (use |date, |time, |datetime, |relative filters)",
            # Sender Information
            "sender_id": "Numeric ID of the message sender",
            "sender_name": "Name of the message sender",
            "is_vip": "Whether sender is a VIP (true/false)",
            # Profile Matching
            "profile_id": "ID of the matching or digest profile",
            "profile_name": "Name of the matching or digest profile",
            # Scoring & Ranking
            "score": "Combined relevance score (0.0-1.0) - use {score:.2f} for formatting",
            "keyword_score": "Keyword/heuristic match score (optional - use {?keyword_score:.2f})",
            "semantic_score": "Semantic similarity score from AI (optional - use {?semantic_score:.2f})",
            "reactions": "Number of reactions on the message",
            "rank": "Message rank (1-based); present when available for alert types including dm_alerts and digests",
            # Triggers
            "triggers": "Comma-separated matched trigger keywords",
            "triggers_json": "Triggers as JSON array (for webhooks)",
            "triggers_formatted": "Formatted trigger list with icons",
        },
    },
    "saved_messages": {
        "template": """📅 {timestamp|relative} 🕐 {timestamp|time}
**🔔 Alert from {chat_title}** **Score:** {score:.2f}

**From:** 👤 {sender_name} 🧘 vip: {is_vip}
📝 {message_text}

🎯 {profile_name}
⚡ {triggers_formatted}
🔗 {message_link|link} 👍 reactions: {reactions}
""",
        "description": "Format for messages saved to Saved Messages",
        "variables": {
            # Message / Chat Metadata
            "chat_id": "Chat or channel numeric ID",
            "chat_title": "Title of the channel or chat",
            "msg_id": "Message ID within the chat",
            "message_link": "Link to original message (use {message_link|link} for clickable Markdown link)",
            "message_text": "Full message content",
            "message_preview": "Truncated preview of message (configurable length)",
            "timestamp": "Message timestamp (use |date, |time, |datetime, |relative filters)",
            # Sender Information
            "sender_id": "Numeric ID of the message sender",
            "sender_name": "Name of the message sender",
            "is_vip": "Whether sender is a VIP (true/false)",
            # Profile Matching
            "profile_id": "ID of the matching or digest profile",
            "profile_name": "Name of the matching or digest profile",
            # Scoring & Ranking
            "score": "Combined relevance score (0.0-1.0) - use {score:.2f} for formatting",
            "keyword_score": "Keyword/heuristic match score (optional - use {?keyword_score:.2f})",
            "semantic_score": "Semantic similarity score from AI (optional - use {?semantic_score:.2f})",
            "reactions": "Number of reactions on the message",
            # Triggers
            "triggers": "Comma-separated matched trigger keywords",
            "triggers_json": "Triggers as JSON array (for webhooks)",
            "triggers_formatted": "Formatted trigger list with icons",
        },
    },
    "digest": {
        "header": {
            "template": """🗞️ **Digest — Top {top_n} messages from {channel_count} channels**
📅 {schedule} | Profile: {profile_name}
━━━━━━━━━━━━━━━━━━━━
""",
            "description": "Header template for digest messages",
            "variables": {
                "top_n": "Number of top messages included",
                "channel_count": "Number of unique channels",
                "schedule": "Digest schedule (hourly/daily)",
                "profile_name": "Name of the digest profile",
                "profile_id": "ID of the digest profile",
                "timestamp": "Digest generation timestamp",
                "time_range": "Time range covered (e.g., 'last 24h')",
            },
        },
        "entry": {
            "template": """📅 {timestamp|relative} 🕐 {timestamp|time}  🎯 {profile_name}
{rank}. 💬 **{chat_title}** (Score: {score:.2f})
👤 {sender_name}
📝 {message_text}
🎯 ⚡ {triggers_formatted}
🔗 {message_link|link} 👍 reactions: {reactions}

---""",
            "description": "Template for each message entry in digest",
            "variables": {
                # Message / Chat Metadata
                "chat_id": "Chat or channel numeric ID",
                "chat_title": "Title of the channel or chat",
                "msg_id": "Message ID within the chat",
                "message_link": "Link to original message (use {message_link|link} for clickable Markdown link)",
                "message_text": "Full message content",
                "message_preview": "Truncated preview of message (configurable length)",
                "timestamp": "Message timestamp (use |date, |time, |datetime, |relative filters)",
                # Sender Information
                "sender_id": "Numeric ID of the message sender",
                "sender_name": "Name of the message sender",
                "is_vip": "Whether sender is a VIP (true/false)",
                # Profile Matching
                "profile_id": "ID of the matching or digest profile",
                "profile_name": "Name of the matching or digest profile",
                # Scoring & Ranking
                "rank": "Message rank (1-based)",
                "score": "Combined relevance score (0.0-1.0) - use {score:.2f} for formatting",
                "keyword_score": "Keyword/heuristic match score (optional - use {?keyword_score:.2f})",
                "semantic_score": "Semantic similarity score from AI (optional - use {?semantic_score:.2f})",
                "reactions": "Number of reactions on the message",
                # Triggers
                "triggers": "Comma-separated matched trigger keywords",
                "triggers_formatted": "Formatted trigger list with icons",
            },
        },
        "trigger_format": {
            "template": "{icon} {trigger}",
            "description": "Format for individual trigger display",
            "variables": {
                "icon": "Trigger type icon",
                "trigger": "Trigger keyword/pattern",
            },
        },
    },
    "webhook_payload": {
        "template": """{
  "event": "alert",
  "chat_title": "{chat_title}",
  "sender_name": "{sender_name}",
  "message_text": "{message_text}",
  "score": {score},
  "profile_name": "{profile_name}",
  "triggers": {triggers_json},
  "timestamp": "{timestamp}"
}""",
        "description": "JSON payload template for webhook notifications",
        "variables": {
            # Message / Chat Metadata
            "chat_id": "Chat or channel numeric ID",
            "chat_title": "Title of the channel or chat",
            "msg_id": "Message ID within the chat",
            "message_link": "Link to original message",
            "message_text": "Full message content (JSON escaped)",
            "message_preview": "Truncated preview of message (configurable length)",
            "timestamp": "ISO 8601 timestamp",
            # Sender Information
            "sender_id": "Numeric ID of the message sender",
            "sender_name": "Name of the message sender",
            "is_vip": "Whether sender is a VIP (true/false)",
            # Profile Matching
            "profile_id": "ID of the matching or digest profile",
            "profile_name": "Name of the matching or digest profile",
            # Scoring & Ranking
            "score": "Combined relevance score (0.0-1.0)",
            "keyword_score": "Keyword/heuristic match score (optional)",
            "semantic_score": "Semantic similarity score from AI (optional)",
            "reactions": "Number of reactions on the message",
            "rank": "Message rank (if applicable)",
            # Triggers
            "triggers": "Comma-separated matched trigger keywords",
            "triggers_json": "Triggers as JSON array (recommended for webhooks)",
        },
    },
}

# Sample data for preview and testing
SAMPLE_DATA = {
    "dm_alerts": {
        "chat_title": "Crypto Trading Signals",
        "message_text": (
            "🚀 BTC showing strong bullish momentum. "
            "Key resistance at $45k. Watch for breakout confirmation."
        ),
        "message_preview": (
            "🚀 BTC showing strong bullish momentum. "
            "Key resistance at $45k. Watch for breakout..."
        ),
        "sender_name": "TradingBot",
        "sender_id": "123456789",
        "score": 0.85,
        "keyword_score": 0.75,
        "semantic_score": 0.92,
        "profile_name": "Crypto Alerts",
        "profile_id": "crypto-alerts",
        "triggers": ["BTC", "bullish", "breakout"],
        "triggers_formatted": "🔑 BTC, 🔑 bullish, 🔑 breakout",
        "timestamp": "2024-01-15T10:30:00Z",
        "message_link": "https://t.me/c/1234567890/42",
        "chat_id": "-1001234567890",
        "msg_id": "42",
        "reactions": 15,
        "is_vip": "false",
        "rank": 1,
    },
    "saved_messages": {
        "chat_title": "Security Incidents",
        "message_text": (
            "⚠️ Critical vulnerability discovered in popular npm package. "
            "CVE-2024-1234 affects versions < 2.0. Update immediately."
        ),
        "message_preview": (
            "⚠️ Critical vulnerability discovered in popular npm package. "
            "CVE-2024-1234 affects..."
        ),
        "sender_name": "SecurityBot",
        "sender_id": "987654321",
        "score": 0.92,
        "keyword_score": 0.88,
        "semantic_score": 0.95,
        "profile_name": "Security Monitoring",
        "profile_id": "security-monitoring",
        "triggers": ["CVE", "vulnerability", "critical"],
        "triggers_formatted": "🔑 CVE, 🔑 vulnerability, 🔑 critical",
        "timestamp": "2024-01-15T11:45:00Z",
        "message_link": "https://t.me/c/9876543210/101",
        "chat_id": "-1009876543210",
        "msg_id": "101",
        "reactions": 42,
        "is_vip": "true",
        "rank": 2,
    },
    "digest_header": {
        "top_n": 10,
        "channel_count": 5,
        "schedule": "daily",
        "profile_name": "Market Intelligence",
        "profile_id": "market-intel",
        "timestamp": "2024-01-15T18:00:00Z",
        "time_range": "last 24h",
    },
    "digest_entry": {
        "rank": 1,
        "chat_title": "Tech News",
        "message_preview": (
            "Apple announces new AI features for iOS 18. "
            "Integration with Siri expected to revolutionize..."
        ),
        "message_text": (
            "Apple announces new AI features for iOS 18. "
            "Integration with Siri expected to revolutionize how users interact with their devices. "
            "The new features include advanced natural language processing and contextual awareness."
        ),
        "sender_name": "TechReporter",
        "sender_id": "555666777",
        "score": 0.89,
        "keyword_score": 0.82,
        "semantic_score": 0.94,
        "triggers": ["AI", "Apple", "iOS"],
        "triggers_formatted": "🔑 AI, 🔑 Apple, 🔑 iOS",
        "timestamp": "2024-01-15T14:20:00Z",
        "message_link": "https://t.me/c/5555555555/200",
        "chat_id": "-1005555555555",
        "msg_id": "200",
        "reactions": 28,
        "profile_name": "Tech Updates",
        "profile_id": "tech-updates",
        "is_vip": "true",
    },
    "webhook_payload": {
        "chat_title": "DevOps Alerts",
        "message_text": "Deployment to production completed successfully. Version 2.1.0 is now live.",
        "sender_name": "CI/CD Bot",
        "sender_id": "111222333",
        "score": 0.75,
        "keyword_score": 0.70,
        "semantic_score": 0.80,
        "profile_name": "DevOps Monitoring",
        "profile_id": "devops-monitoring",
        "triggers": ["deployment", "production"],
        "triggers_json": '["deployment", "production"]',
        "timestamp": "2024-01-15T16:00:00Z",
        "message_link": "https://t.me/c/7777777777/55",
        "chat_id": "-1007777777777",
        "msg_id": "55",
        "reactions": 5,
        "is_vip": "false",
    },
}

# Trigger type icons mapping
TRIGGER_ICONS = {
    "keyword": "🔑",
    "regex": "📊",
    "semantic": "🧠",
    "phrase": "💬",
    "hashtag": "#️⃣",
    "mention": "@",
    "default": "•",
}
