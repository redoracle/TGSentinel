# TG Sentinel: Schedule-Driven Digest Architecture

**Date:** 2025-11-20  
**Status:** Design Proposal  
**Scope:** Per-profile digest scheduling with aggregation & deduplication

---

## 1. Executive Summary

This design implements **schedule-aware alert profiles** where each profile (global, per-channel, or per-user) can specify **up to 3 digest schedules** for batched delivery. The system discovers all due digests for each schedule window, collects matching messages, aggregates across profiles, deduplicates, and dispatches a single consolidated digest per schedule.

### Key Features

- **Per-profile scheduling**: Each profile specifies when its messages should be digested
- **Multiple schedules per profile**: Support up to 3 schedules (e.g., hourly + daily)
- **Unified discovery**: Single scheduler discovers all due digests across all profiles
- **Smart aggregation**: Deduplicates messages appearing in multiple profiles
- **Schedule consolidation**: Messages from different profiles with same schedule → single digest
- **Backward compatible**: Existing configs continue to work

---

## 2. Current Architecture Analysis

### 2.1 Current Config Structure

```python
@dataclass
class DigestCfg:
    hourly: bool = True
    daily: bool = False
    top_n: int = 10

@dataclass
class AlertsCfg:
    mode: str = "dm"  # dm|channel|both
    target_channel: str = ""
    min_score: float = 5.0
    digest: DigestCfg = field(default_factory=DigestCfg)
```

**Limitations:**

- Global digest settings only (not per-profile)
- Binary hourly/daily flags (no custom intervals)
- No profile-level digest preferences
- Separate workers for hourly vs daily digests
- No deduplication across overlapping profiles

### 2.2 Current Digest Flow

```bash
main.py startup:
  ├─ Send initial hourly digest (if enabled)
  └─ Send initial daily digest (if enabled)

worker_orchestrator.py:
  ├─ periodic_digest() → Every 1 hour → Send 1h digest
  └─ daily_digest() → Every 24 hours → Send 24h digest

digest.py:
  └─ send_digest(since_hours, top_n, channels_config)
      ├─ Query messages WHERE alerted=1 AND created_at >= since
      ├─ Fetch message details from Telegram
      ├─ Format digest with trigger annotations
      └─ Send to DM / channel
```

**Issues:**

- No awareness of which profile triggered the alert
- Cannot filter messages by profile
- Cannot schedule different profiles differently
- Messages matching multiple profiles appear multiple times

---

## 3. Proposed Architecture

### 3.1 Schedule Definition

```python
from enum import Enum
from typing import List, Optional

class DigestSchedule(str, Enum):
    """Predefined digest schedules."""
    NONE = "none"           # Instant alerts only, no digest
    HOURLY = "hourly"       # Every 1 hour
    EVERY_4H = "every_4h"   # Every 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
    EVERY_6H = "every_6h"   # Every 6 hours (00:00, 06:00, 12:00, 18:00)
    EVERY_12H = "every_12h" # Every 12 hours (00:00, 12:00)
    DAILY = "daily"         # Once per day at configured hour (default: 08:00 UTC)
    WEEKLY = "weekly"       # Once per week on configured day (default: Monday 08:00 UTC)

@dataclass
class ScheduleConfig:
    """Configuration for a single digest schedule."""

    schedule: DigestSchedule
    enabled: bool = True

    # Optional overrides for schedule-specific settings
    top_n: Optional[int] = None  # Max messages in this digest (inherits from profile if None)
    min_score: Optional[float] = None  # Min score for this digest (inherits from profile if None)

    # For DAILY schedule: hour (0-23, default 8 = 08:00 UTC)
    daily_hour: int = 8

    # For WEEKLY schedule: day (0=Monday, 6=Sunday) and hour
    weekly_day: int = 0  # Monday
    weekly_hour: int = 8

@dataclass
class ProfileDigestConfig:
    """Digest configuration for a profile (up to 3 schedules)."""

    schedules: List[ScheduleConfig] = field(default_factory=list)  # Max 3

    # Global settings (apply to all schedules unless overridden)
    top_n: int = 10
    min_score: float = 0.0  # Minimum score for messages to appear in digest
    mode: str = "dm"  # dm|channel|both
    target_channel: str = ""  # For mode=channel or both

    def __post_init__(self):
        """Validate digest configuration."""
        if len(self.schedules) > 3:
            raise ValueError(f"Max 3 schedules allowed, got {len(self.schedules)}")
        if self.min_score < 0.0 or self.min_score > 10.0:
            raise ValueError(f"min_score must be 0.0-10.0, got {self.min_score}")
```

### 3.2 Updated Profile & Config Structures

```python
@dataclass
class ProfileDefinition:
    """Global profile definition."""
    id: str
    name: str = ""

    # ... existing keyword fields ...

    # NEW: Digest configuration for this profile
    digest: ProfileDigestConfig = field(default_factory=ProfileDigestConfig)

@dataclass
class ChannelOverrides:
    """Per-channel overrides."""
    # ... existing fields ...

    # NEW: Override digest settings for this channel
    digest: Optional[ProfileDigestConfig] = None

@dataclass
class ChannelRule:
    """Channel monitoring rule."""
    # ... existing fields ...

    # NEW: Per-channel digest configuration (overrides bound profiles)
    digest: Optional[ProfileDigestConfig] = None

@dataclass
class MonitoredUser:
    """Monitored user configuration."""
    # ... existing fields ...

    # NEW: Per-user digest configuration
    digest: Optional[ProfileDigestConfig] = None

@dataclass
class AlertsCfg:
    """Global alert configuration (fallback for profiles without digest config)."""
    mode: str = "dm"
    target_channel: str = ""
    min_score: float = 5.0

    # NEW: Default digest config (used when profile has no digest config)
    digest: ProfileDigestConfig = field(default_factory=lambda: ProfileDigestConfig(
        schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)]
    ))
```

### 3.3 Database Schema Changes

```sql
-- Add profile tracking to messages table
ALTER TABLE messages ADD COLUMN matched_profiles TEXT;  -- JSON array of profile IDs
ALTER TABLE messages ADD COLUMN digest_schedule TEXT;   -- The schedule this message is assigned to
ALTER TABLE messages ADD COLUMN digest_processed INTEGER DEFAULT 0;  -- 1 = included in digest

-- Add index for efficient digest queries
CREATE INDEX IF NOT EXISTS idx_messages_digest ON messages(digest_schedule, digest_processed, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_profiles ON messages(matched_profiles);
```

**Message Storage Flow:**

```python
# When processing a message that matches multiple profiles
upsert_message(
    chat_id=123,
    msg_id=456,
    score=8.5,
    matched_profiles=["security", "critical_updates"],  # JSON serialized
    digest_schedule="hourly",  # Determined by profile resolution
    digest_processed=0,
    ...
)
```

### 3.4 Profile Resolution with Digest Awareness

```python
@dataclass
class ResolvedProfile:
    """Fully resolved profile with digest configuration."""

    # ... existing fields (keywords, weights, etc.) ...

    # NEW: Resolved digest configuration
    digest: ProfileDigestConfig
    digest_source: str  # "profile", "channel_override", "user_override", "global_default"

    # NEW: Profile IDs that contributed to this resolution
    profile_ids: List[str]

class ProfileResolver:
    def resolve_for_channel(self, channel: ChannelRule) -> ResolvedProfile:
        """Resolve profile with digest configuration."""

        # 1. Resolve keywords (existing logic)
        # ...

        # 2. Resolve digest configuration (NEW)
        digest_config = self._resolve_digest_config(channel)

        return ResolvedProfile(
            # ... existing fields ...
            digest=digest_config,
            digest_source=source,
            profile_ids=[p.id for p in bound_profiles]
        )

    def _resolve_digest_config(self, channel: ChannelRule) -> ProfileDigestConfig:
        """Resolve digest configuration with precedence."""

        # Precedence (highest to lowest):
        # 1. Channel-level digest override
        if channel.digest:
            return channel.digest

        # 2. Channel overrides from bound profiles
        if channel.overrides.digest:
            return channel.overrides.digest

        # 3. First bound profile with digest config
        for profile_id in channel.profiles:
            profile = self.global_profiles.get(profile_id)
            if profile and profile.digest.schedules:
                return profile.digest

        # 4. Global default from AlertsCfg
        return self.alerts_cfg.digest
```

---

## 4. Schedule Discovery & Execution

### 4.1 Unified Digest Scheduler

```python
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

class DigestScheduler:
    """Discovers and executes due digests across all profiles."""

    def __init__(self, cfg: AppCfg, profile_resolver: ProfileResolver):
        self.cfg = cfg
        self.profile_resolver = profile_resolver
        self.last_run: Dict[str, datetime] = {}  # schedule -> last run timestamp

    def get_due_schedules(self, now: datetime = None) -> List[DigestSchedule]:
        """Determine which schedules are due to run now."""
        if now is None:
            now = datetime.now(timezone.utc)

        due = []

        # Check each schedule type
        if self._is_hourly_due(now):
            due.append(DigestSchedule.HOURLY)

        if self._is_every_4h_due(now):
            due.append(DigestSchedule.EVERY_4H)

        if self._is_every_6h_due(now):
            due.append(DigestSchedule.EVERY_6H)

        if self._is_every_12h_due(now):
            due.append(DigestSchedule.EVERY_12H)

        if self._is_daily_due(now):
            due.append(DigestSchedule.DAILY)

        if self._is_weekly_due(now):
            due.append(DigestSchedule.WEEKLY)

        return due

    def _is_hourly_due(self, now: datetime) -> bool:
        """Check if hourly digest is due."""
        last = self.last_run.get(DigestSchedule.HOURLY)
        if last is None:
            return True  # First run

        # Run every hour, on the hour (e.g., 14:00, 15:00, 16:00)
        return now.hour != last.hour

    def _is_every_4h_due(self, now: datetime) -> bool:
        """Check if 4-hour digest is due."""
        last = self.last_run.get(DigestSchedule.EVERY_4H)
        if last is None:
            return now.hour in (0, 4, 8, 12, 16, 20)

        # Run at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
        return now.hour in (0, 4, 8, 12, 16, 20) and now.hour != last.hour

    def _is_daily_due(self, now: datetime) -> bool:
        """Check if daily digest is due (respects configured hour)."""
        last = self.last_run.get(DigestSchedule.DAILY)
        if last is None:
            # Run if we're at the configured hour
            daily_hour = self._get_daily_hour()
            return now.hour == daily_hour

        # Run once per day at configured hour
        return now.date() > last.date() and now.hour >= self._get_daily_hour()

    def _get_daily_hour(self) -> int:
        """Get configured daily digest hour from any profile."""
        # Scan all profiles for daily schedule config
        for profile in self.cfg.global_profiles.values():
            for sched_cfg in profile.digest.schedules:
                if sched_cfg.schedule == DigestSchedule.DAILY:
                    return sched_cfg.daily_hour
        return 8  # Default: 08:00 UTC

    def discover_profile_schedules(self, schedule: DigestSchedule) -> List[Tuple[str, ProfileDigestConfig]]:
        """Discover all profiles that have the given schedule enabled.

        Returns:
            List of (profile_id, digest_config) tuples
        """
        results = []

        # 1. Scan global profiles
        for profile_id, profile in self.cfg.global_profiles.items():
            for sched_cfg in profile.digest.schedules:
                if sched_cfg.schedule == schedule and sched_cfg.enabled:
                    results.append((profile_id, profile.digest))

        # 2. Scan channel-level digest configs
        for channel in self.cfg.channels:
            if channel.digest:
                for sched_cfg in channel.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        # Use channel ID as identifier
                        results.append((f"channel_{channel.id}", channel.digest))

            # Check channel overrides
            elif channel.overrides.digest:
                for sched_cfg in channel.overrides.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append((f"channel_{channel.id}_override", channel.overrides.digest))

        # 3. Scan user-level digest configs
        for user in self.cfg.monitored_users:
            if user.digest:
                for sched_cfg in user.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append((f"user_{user.id}", user.digest))

            # Check user overrides
            elif user.overrides.digest:
                for sched_cfg in user.overrides.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append((f"user_{user.id}_override", user.overrides.digest))

        return results

    def mark_schedule_run(self, schedule: DigestSchedule):
        """Mark that a schedule has been executed."""
        self.last_run[schedule] = datetime.now(timezone.utc)
```

### 4.2 Message Collection & Deduplication

```python
@dataclass
class DigestMessage:
    """A message to include in a digest."""
    chat_id: int
    msg_id: int
    score: float
    chat_title: str
    sender_name: str
    message_text: str
    trigger_annotations: str
    created_at: datetime
    matched_profiles: List[str]  # Profile IDs that matched this message

    def dedup_key(self) -> Tuple[int, int]:
        """Unique key for deduplication."""
        return (self.chat_id, self.msg_id)

class DigestCollector:
    """Collects and deduplicates messages for a digest schedule."""

    def __init__(self, engine: Engine, schedule: DigestSchedule, since_hours: int):
        self.engine = engine
        self.schedule = schedule
        self.since_hours = since_hours
        self.messages: Dict[Tuple[int, int], DigestMessage] = {}  # dedup_key -> message

    def collect_for_profiles(self, profile_ids: List[str], min_score: float = 0.0):
        """Collect messages matching the given profiles.

        Args:
            profile_ids: List of profile IDs to match
            min_score: Minimum score threshold
        """
        since = datetime.now(timezone.utc) - timedelta(hours=self.since_hours)
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")

        # Query messages that:
        # - Match any of the target profiles
        # - Meet score threshold
        # - Haven't been processed for THIS schedule yet
        # - Are within time window

        query = """
        SELECT
            chat_id, msg_id, score, chat_title, sender_name,
            message_text, trigger_annotations, created_at, matched_profiles
        FROM messages
        WHERE alerted = 1
          AND created_at >= :since
          AND score >= :min_score
          AND digest_processed = 0
          AND (
            -- Match any of the target profiles
            -- JSON contains any profile_id
            """ + " OR ".join([f"matched_profiles LIKE '%{pid}%'" for pid in profile_ids]) + """
          )
        ORDER BY score DESC, created_at DESC
        """

        with self.engine.begin() as con:
            rows = con.execute(
                text(query),
                {"since": since_str, "min_score": min_score}
            ).fetchall()

        # Deduplicate and merge profile lists
        for row in rows:
            msg = DigestMessage(
                chat_id=row.chat_id,
                msg_id=row.msg_id,
                score=row.score,
                chat_title=row.chat_title or f"Chat {row.chat_id}",
                sender_name=row.sender_name or "Unknown",
                message_text=row.message_text or "",
                trigger_annotations=row.trigger_annotations or "",
                created_at=row.created_at,
                matched_profiles=json.loads(row.matched_profiles or "[]")
            )

            key = msg.dedup_key()
            if key in self.messages:
                # Merge profile lists
                existing = self.messages[key]
                existing.matched_profiles = list(set(
                    existing.matched_profiles + msg.matched_profiles
                ))
                # Keep higher score
                if msg.score > existing.score:
                    existing.score = msg.score
            else:
                self.messages[key] = msg

    def get_top_messages(self, top_n: int) -> List[DigestMessage]:
        """Get top N messages by score."""
        sorted_messages = sorted(
            self.messages.values(),
            key=lambda m: (m.score, m.created_at),
            reverse=True
        )
        return sorted_messages[:top_n]

    def mark_as_processed(self, message_ids: List[Tuple[int, int]]):
        """Mark messages as processed for this schedule."""
        if not message_ids:
            return

        placeholders = ", ".join([f"({cid}, {mid})" for cid, mid in message_ids])

        with self.engine.begin() as con:
            con.execute(text(f"""
                UPDATE messages
                SET digest_processed = 1
                WHERE (chat_id, msg_id) IN ({placeholders})
            """))
```

### 4.3 Unified Digest Worker

```python
class WorkerOrchestrator:
    """Orchestrates all background workers."""

    def __init__(self, ...):
        # ... existing fields ...
        self.digest_scheduler = DigestScheduler(self.cfg, self.profile_resolver)

    async def unified_digest_worker(self) -> None:
        """Single worker that handles all digest schedules.

        Replaces periodic_digest() and daily_digest() with a unified approach.
        """
        log.info("[DIGEST-SCHEDULER] Starting unified digest worker")

        while True:
            await self.handshake_gate.wait()

            # Check which schedules are due
            now = datetime.now(timezone.utc)
            due_schedules = self.digest_scheduler.get_due_schedules(now)

            if not due_schedules:
                # No digests due, sleep for 5 minutes and check again
                await asyncio.sleep(300)
                continue

            # Process each due schedule
            for schedule in due_schedules:
                try:
                    await self._process_digest_schedule(schedule, now)
                    self.digest_scheduler.mark_schedule_run(schedule)
                except Exception as e:
                    log.error(
                        f"[DIGEST-SCHEDULER] Failed to process {schedule}: {e}",
                        exc_info=True
                    )

            # Sleep until next check (5 minutes)
            await asyncio.sleep(300)

    async def _process_digest_schedule(self, schedule: DigestSchedule, now: datetime):
        """Process a single digest schedule.

        Args:
            schedule: The schedule to process (HOURLY, DAILY, etc.)
            now: Current timestamp
        """
        log.info(f"[DIGEST-SCHEDULER] Processing {schedule} digest")

        # 1. Discover all profiles with this schedule
        profiles_with_schedule = self.digest_scheduler.discover_profile_schedules(schedule)

        if not profiles_with_schedule:
            log.info(f"[DIGEST-SCHEDULER] No profiles configured for {schedule}")
            return

        # 2. Determine time window for this schedule
        since_hours = self._get_schedule_window_hours(schedule)

        # 3. Collect messages (with deduplication)
        collector = DigestCollector(self.engine, schedule, since_hours)

        # Aggregate min_score and top_n from all profiles
        min_score = min(
            (cfg.min_score for _, cfg in profiles_with_schedule),
            default=0.0
        )
        top_n = max(
            (cfg.top_n for _, cfg in profiles_with_schedule),
            default=10
        )

        # Collect all profile IDs
        all_profile_ids = [pid for pid, _ in profiles_with_schedule]

        collector.collect_for_profiles(all_profile_ids, min_score)

        # 4. Get top messages
        top_messages = collector.get_top_messages(top_n)

        if not top_messages:
            log.info(f"[DIGEST-SCHEDULER] No messages for {schedule} digest")
            return

        # 5. Build and send digest
        current_client = self.client_ref()

        # Use first profile's delivery config (or aggregate)
        delivery_cfg = profiles_with_schedule[0][1]

        await self._send_unified_digest(
            client=current_client,
            schedule=schedule,
            messages=top_messages,
            mode=delivery_cfg.mode,
            target_channel=delivery_cfg.target_channel,
            since_hours=since_hours
        )

        # 6. Mark messages as processed
        message_ids = [msg.dedup_key() for msg in top_messages]
        collector.mark_as_processed(message_ids)

        log.info(
            f"[DIGEST-SCHEDULER] Sent {schedule} digest with {len(top_messages)} messages"
        )

    def _get_schedule_window_hours(self, schedule: DigestSchedule) -> int:
        """Get the time window for a schedule in hours."""
        windows = {
            DigestSchedule.HOURLY: 1,
            DigestSchedule.EVERY_4H: 4,
            DigestSchedule.EVERY_6H: 6,
            DigestSchedule.EVERY_12H: 12,
            DigestSchedule.DAILY: 24,
            DigestSchedule.WEEKLY: 168,  # 7 days
        }
        return windows.get(schedule, 1)

    async def _send_unified_digest(
        self,
        client: TelegramClient,
        schedule: DigestSchedule,
        messages: List[DigestMessage],
        mode: str,
        target_channel: str,
        since_hours: int
    ):
        """Send a unified digest for a schedule."""
        # Build digest message
        schedule_name = schedule.value.replace("_", " ").title()
        lines = [f"🗞️ **{schedule_name} Digest — Top {len(messages)} highlights**\n"]

        for idx, msg in enumerate(messages, 1):
            # Format trigger annotations
            triggers_formatted = format_alert_triggers(msg.trigger_annotations)
            trigger_line = f"\n🎯 {triggers_formatted}" if triggers_formatted else ""

            # Format profile badges
            profile_badges = " ".join([f"`{p}`" for p in msg.matched_profiles[:3]])
            profile_line = f"\n📋 {profile_badges}" if msg.matched_profiles else ""

            # Build message link
            if str(msg.chat_id).startswith("-100"):
                clean_id = str(msg.chat_id)[4:]
                msg_link = f"https://t.me/c/{clean_id}/{msg.msg_id}"
            else:
                msg_link = f"tg://openmessage?chat_id={msg.chat_id}&message_id={msg.msg_id}"

            # Truncate message text
            text = msg.message_text[:150] + "..." if len(msg.message_text) > 150 else msg.message_text
            text = text.replace("\n", " ")

            lines.append(
                f"**{idx}. [{msg.chat_title}]({msg_link})** — Score: {msg.score:.2f}\n"
                f"👤 {msg.sender_name} • 🕐 {msg.created_at.strftime('%H:%M')}\n"
                f"💬 _{text}_{trigger_line}{profile_line}"
            )

        digest_text = "\n".join(lines)

        # Split into chunks if needed (Telegram 4096 char limit)
        MAX_LENGTH = 4000
        if len(digest_text) > MAX_LENGTH:
            chunks = self._split_digest(lines[0], lines[1:], MAX_LENGTH)
        else:
            chunks = [digest_text]

        # Send to configured destination(s)
        try:
            if mode in ("dm", "both"):
                for i, chunk in enumerate(chunks):
                    part_header = f"[Part {i+1}/{len(chunks)}]\n" if len(chunks) > 1 and i > 0 else ""
                    await client.send_message("me", part_header + chunk, link_preview=False)
                log.info(f"[DIGEST-SCHEDULER] Sent {schedule} digest to DM")

            if mode in ("channel", "both") and target_channel:
                for i, chunk in enumerate(chunks):
                    part_header = f"[Part {i+1}/{len(chunks)}]\n" if len(chunks) > 1 and i > 0 else ""
                    await client.send_message(target_channel, part_header + chunk, link_preview=False)
                log.info(f"[DIGEST-SCHEDULER] Sent {schedule} digest to channel {target_channel}")

        except Exception as e:
            log.error(f"[DIGEST-SCHEDULER] Failed to send {schedule} digest: {e}", exc_info=True)

    def _split_digest(self, header: str, entries: List[str], max_len: int) -> List[str]:
        """Split digest into multiple messages if too long."""
        chunks = []
        current = header

        for entry in entries:
            if len(current) + len(entry) + 1 > max_len:
                chunks.append(current)
                current = entry
            else:
                current += "\n" + entry

        if current:
            chunks.append(current)

        return chunks
```

---

## 5. Configuration Examples

### 5.1 Global Profile with Multiple Schedules

```yaml
# config/profiles.yml
profiles:
  security:
    name: "Security & Vulnerabilities"

    security_keywords:
      - CVE
      - vulnerability
      - exploit
      - breach

    urgency_keywords:
      - critical
      - urgent
      - emergency

    # Digest configuration for this profile
    digest:
      # Send security alerts in both hourly and daily digests
      schedules:
        - schedule: "hourly"
          enabled: true
          top_n: 5 # Only top 5 in hourly
          min_score: 7.0 # High threshold for hourly

        - schedule: "daily"
          enabled: true
          top_n: 20 # More in daily summary
          min_score: 5.0 # Lower threshold for daily

      # Delivery settings (apply to all schedules)
      mode: "both" # Send to DM and channel
      target_channel: "@security_alerts"

  releases:
    name: "Software Releases"

    release_keywords:
      - release
      - launched
      - version
      - update

    digest:
      # Only daily digest for releases
      schedules:
        - schedule: "daily"
          enabled: true
          daily_hour: 9 # 09:00 UTC

      top_n: 15
      min_score: 4.0
      mode: "dm"
```

### 5.2 Channel-Specific Override

```yaml
# config/tgsentinel.yml
channels:
  - id: -1001234567890
    name: "Crypto Trading Signals"

    # Bind multiple profiles
    profiles:
      - trading
      - market_analysis

    # Override digest: more frequent for this channel
    digest:
      schedules:
        - schedule: "hourly"
          enabled: true

        - schedule: "every_4h"
          enabled: true
          top_n: 8

      mode: "dm"
      target_channel: "@my_trading_alerts"
```

### 5.3 User-Specific Digest

```yaml
# config/tgsentinel.yml
monitored_users:
  - id: 123456789
    name: "Important Executive"
    username: "ceo"

    profiles:
      - executive_comms

    # Send all messages from this user in a daily digest
    digest:
      schedules:
        - schedule: "daily"
          daily_hour: 18 # 18:00 UTC (end of day summary)

      top_n: 50 # Include many messages
      min_score: 0.0 # Include all messages
      mode: "dm"
```

---

## 6. Migration Path

### 6.1 Backward Compatibility

**Old config format (still supported):**

```yaml
alerts:
  digest:
    hourly: true
    daily: false
    top_n: 10
```

**Auto-conversion at load time:**

```python
def _convert_legacy_digest(old_digest: DigestCfg) -> ProfileDigestConfig:
    """Convert old digest config to new format."""
    schedules = []

    if old_digest.hourly:
        schedules.append(ScheduleConfig(schedule=DigestSchedule.HOURLY))

    if old_digest.daily:
        schedules.append(ScheduleConfig(schedule=DigestSchedule.DAILY))

    return ProfileDigestConfig(
        schedules=schedules,
        top_n=old_digest.top_n
    )
```

### 6.2 Migration Tool

```bash
# tools/migrate_digest_schedules.py
python tools/migrate_digest_schedules.py --dry-run  # Preview
python tools/migrate_digest_schedules.py --apply    # Execute
```

**Features:**

- Converts global `digest.hourly/daily` to new schedule format
- Preserves existing behavior
- Adds comments explaining new options
- Creates timestamped backup

---

## 7. Testing Strategy

### 7.1 Unit Tests

```python
# tests/unit/test_digest_scheduler.py

def test_schedule_discovery():
    """Test that scheduler discovers all profiles with a schedule."""
    # Given: Config with multiple profiles and schedules
    # When: discover_profile_schedules(DigestSchedule.HOURLY)
    # Then: Returns all profiles with hourly schedule enabled

def test_schedule_due_detection():
    """Test schedule due logic for each schedule type."""
    # Test HOURLY: due at each hour change
    # Test EVERY_4H: due at 00:00, 04:00, 08:00, etc.
    # Test DAILY: due once per day at configured hour

def test_message_deduplication():
    """Test that messages matching multiple profiles appear once."""
    # Given: Message matches "security" and "critical" profiles
    # When: Collecting for both profiles
    # Then: Message appears once with both profile IDs

def test_schedule_config_validation():
    """Test that invalid configs are rejected."""
    # Max 3 schedules per profile
    # Valid score ranges
    # Valid hour ranges
```

### 7.2 Integration Tests

```python
# tests/integration/test_digest_e2e.py

async def test_hourly_digest_with_multiple_profiles():
    """Test hourly digest collection across profiles."""
    # Given: Messages matching different profiles
    # When: Hourly digest runs
    # Then: All profiles' messages collected and deduplicated

async def test_profile_specific_scoring():
    """Test that per-profile min_score is respected."""
    # Given: Messages with scores 5.0, 7.0, 9.0
    # When: Profile A (min_score=6.0) and Profile B (min_score=8.0)
    # Then: Digest includes only messages meeting lowest threshold
```

---

## 8. Implementation Checklist

### Phase 1: Data Model (Week 1)

- [ ] Add `DigestSchedule` enum
- [ ] Add `ScheduleConfig` dataclass
- [ ] Add `ProfileDigestConfig` dataclass
- [ ] Update `ProfileDefinition` with digest field
- [ ] Update `ChannelRule` with digest field
- [ ] Update `MonitoredUser` with digest field
- [ ] Add migration for backward compatibility
- [ ] Add database columns: `matched_profiles`, `digest_schedule`, `digest_processed`
- [ ] Write unit tests for new structures

### Phase 2: Profile Resolution (Week 2)

- [ ] Update `ProfileResolver` to resolve digest configs
- [ ] Add digest precedence logic (channel > profile > global)
- [ ] Update `ResolvedProfile` with digest fields
- [ ] Track profile IDs in resolved profiles
- [ ] Write unit tests for digest resolution

### Phase 3: Message Tracking (Week 2-3)

- [ ] Update `upsert_message()` to accept `matched_profiles`
- [ ] Store profile IDs in database
- [ ] Create `DigestCollector` class
- [ ] Implement profile-aware message queries
- [ ] Implement deduplication logic
- [ ] Write unit tests for collection

### Phase 4: Scheduler (Week 3-4)

- [ ] Create `DigestScheduler` class
- [ ] Implement `get_due_schedules()`
- [ ] Implement schedule-specific due checks
- [ ] Implement `discover_profile_schedules()`
- [ ] Add schedule run tracking
- [ ] Write unit tests for scheduler

### Phase 5: Worker Integration (Week 4-5)

- [ ] Replace `periodic_digest()` and `daily_digest()` with `unified_digest_worker()`
- [ ] Implement `_process_digest_schedule()`
- [ ] Implement `_send_unified_digest()`
- [ ] Update digest formatting to show profile badges
- [ ] Handle digest_processed flag
- [ ] Write integration tests

### Phase 6: Migration & Documentation (Week 5-6)

- [ ] Create migration tool for existing configs
- [ ] Update `config/profiles.yml` examples
- [ ] Update `config/tgsentinel.yml` schema
- [ ] Write migration guide
- [ ] Update user documentation
- [ ] Update API documentation

### Phase 7: Testing & Validation (Week 6)

- [ ] End-to-end testing with real messages
- [ ] Performance testing (large message volumes)
- [ ] Memory profiling
- [ ] Load testing (many profiles)
- [ ] Backward compatibility verification
- [ ] User acceptance testing

---

## 9. Performance Considerations

### 9.1 Database Queries

**Optimization strategies:**

- Index on `(matched_profiles, digest_schedule, digest_processed, created_at)`
- Use JSON containment for profile matching (SQLite JSON1 extension)
- Batch mark operations
- Consider partitioning for very high volumes

### 9.2 Memory Usage

**Mitigation:**

- Stream messages instead of loading all into memory
- Limit top_n to prevent unbounded growth
- Clear processed messages regularly
- Use generator patterns for large result sets

### 9.3 Deduplication Performance

**Approach:**

- Use hash-based dedup (chat_id, msg_id tuple)
- O(1) lookup via dictionary
- Merge profile lists only when needed

---

## 10. Future Enhancements

### 10.1 Custom Schedule Expressions

```yaml
# Future: cron-like expressions
digest:
  schedules:
    - schedule: "custom"
      cron: "0 */6 * * *" # Every 6 hours
      enabled: true
```

### 10.2 Profile-Specific Delivery Channels

```yaml
# Future: Different channels per profile
digest:
  schedules:
    - schedule: "hourly"
      mode: "channel"
      target_channel: "@security_hourly"

    - schedule: "daily"
      mode: "channel"
      target_channel: "@security_daily"
```

### 10.3 Digest Templates

```yaml
# Future: Custom digest formatting
digest:
  template: "compact" # or "detailed", "executive_summary"
  include_attachments: true
  group_by: "profile" # or "channel", "score"
```

---

## 11. Summary

This design provides:

✅ **Per-profile scheduling** with up to 3 schedules each  
✅ **Unified discovery** of due digests across all profiles  
✅ **Smart aggregation** with deduplication  
✅ **Schedule consolidation** for efficient delivery  
✅ **Backward compatibility** with existing configs  
✅ **Extensibility** for future enhancements

**Implementation effort:** ~6 weeks (4 engineers)  
**Test coverage target:** 90%+  
**Performance impact:** <5% overhead on message processing  
**Memory footprint:** O(n) where n = messages in current digest window
