# Profiles System — Developer Quick Reference

**Full Docs:** See `PROFILES_ARCHITECTURE_EVALUATION.md`, `PROFILES_IMPLEMENTATION_CHECKLIST.md`

---

## 📁 File Locations

```bash
config/
  ├── profiles.yml              # Global profile definitions (NEW)
  └── tgsentinel.yml            # Entity bindings + overrides

src/tgsentinel/
  ├── config.py                 # ProfileDefinition, ChannelOverrides dataclasses
  ├── profile_resolver.py       # ProfileResolver, ResolvedProfile (NEW)
  ├── heuristics.py             # Enhanced with trigger_annotations
  ├── worker.py                 # Calls resolver.resolve_for_channel()
  ├── store.py                  # trigger_annotations column
  ├── digest.py                 # format_alert_triggers()
  └── notifier.py               # Updated templates

ui/
  ├── services/
  │   └── profiles_service.py   # Global profile management
  ├── routes/
  │   └── profiles.py           # Profile CRUD endpoints
  ├── templates/
  │   ├── profiles.html         # Global profile editor UI
  │   ├── config.html           # Profile binding UI
  │   └── alerts.html           # Formatted trigger display
  └── static/
      └── js/profiles.js        # Profile UI logic (NEW)

tools/
  ├── migrate_profiles.py       # Config migration script (NEW)
  └── rollback_profiles.py      # Rollback script (NEW)

tests/
  ├── unit/
  │   ├── test_config_profiles.py
  │   ├── test_profile_resolver.py (NEW)
  │   └── test_heuristics.py    # Updated for resolved profiles
  ├── integration/
  │   ├── test_profile_migration.py (NEW)
  │   ├── test_scoring_pipeline.py (NEW)
  │   └── test_profile_ui_flow.py (NEW)
  └── performance/
      └── test_profile_resolution.py (NEW)
```

---

## 🔧 Key Classes & Functions

### Config Layer (`src/tgsentinel/config.py`)

```python
@dataclass
class ProfileDefinition:
    id: str
    keywords: list[str]
    action_keywords: list[str]
    # ... all keyword categories
    scoring_weights: dict[str, float]

@dataclass
class ChannelOverrides:
    keywords_extra: list[str] = field(default_factory=list)
    scoring_weights: dict[str, float] = field(default_factory=dict)
    min_score: float | None = None

@dataclass
class ChannelRule:
    id: int
    name: str
    profiles: list[str] = field(default_factory=list)  # NEW
    overrides: ChannelOverrides = field(default_factory=ChannelOverrides)  # NEW
    # Legacy fields for backward compat
    keywords: list[str] = field(default_factory=list)  # Deprecated
```

### Profile Resolution (`src/tgsentinel/profile_resolver.py`)

```python
@dataclass
class ResolvedProfile:
    """Effective profile after merging globals + overrides."""
    keywords: list[str]
    action_keywords: list[str]
    # ... all categories
    scoring_weights: dict[str, float]
    keyword_sources: dict[str, str]  # keyword → "global:security" | "local"

class ProfileResolver:
    def __init__(self, cfg: AppCfg):
        self.global_profiles = cfg.global_profiles
        self.channels = {c.id: c for c in cfg.channels}

    @functools.lru_cache(maxsize=128)  # Cached for performance
    def resolve_for_channel(self, channel_id: int) -> ResolvedProfile:
        """Merge global profiles + channel overrides."""
        # 1. Merge all bound profiles
        # 2. Apply channel overrides
        # 3. Build keyword source map
        # 4. Return ResolvedProfile
```

### Enhanced Heuristics (`src/tgsentinel/heuristics.py`)

```python
@dataclass
class HeuristicResult:
    important: bool
    reasons: list[str]
    content_hash: str
    pre_score: float
    trigger_annotations: dict[str, str]  # NEW: trigger → source

def run_heuristics(
    text: str,
    # ... existing params ...
    resolved_profile: ResolvedProfile,  # NEW
) -> HeuristicResult:
    reasons, score, annotations = [], 0.0, {}

    # Use resolved profile keywords
    if _check_keywords(text, resolved_profile.urgency_keywords):
        reasons.append("urgent")
        # Apply weight from resolved profile (not hardcoded!)
        weight = resolved_profile.scoring_weights.get("urgency", 1.5)
        score += weight
        # Track source
        annotations["urgent"] = resolved_profile.keyword_sources.get(matched_kw, "unknown")

    return HeuristicResult(..., trigger_annotations=annotations)
```

### Worker Integration (`src/tgsentinel/worker.py`)

```python
# Initialize once at startup
resolver = ProfileResolver(cfg)

async def process_stream_message(...):
    rid = _to_int(payload["chat_id"])

    # Resolve profile for this entity
    resolved = resolver.resolve_for_channel(rid) if rid < 0 else resolver.resolve_for_user(rid)

    # Pass to heuristics
    hr = run_heuristics(
        text=str(payload["text"]),
        # ... existing params ...
        resolved_profile=resolved,  # NEW
    )

    # Store annotations
    upsert_message(
        engine, rid, msg_id, hr.content_hash, score,
        chat_title, sender_name, message_text,
        triggers=", ".join(hr.reasons),
        trigger_annotations=json.dumps(hr.trigger_annotations),  # NEW
        sender_id=sender_id
    )
```

---

## 📝 Config File Examples

### `config/profiles.yml` (Global Profiles)

```yaml
profiles:
  security:
    keywords:
      - "cve"
      - "vulnerability"
      - "exploit"
      - "zero-day"
    action_keywords:
      - "patch now"
      - "urgent fix"
    urgency_keywords:
      - "critical"
      - "emergency"
    security_keywords:
      - "CVE-"
      - "CVSS"
    scoring_weights:
      keywords: 0.8
      action: 1.0
      urgency: 1.5
      security: 1.2
    detect_codes: true
    prioritize_pinned: true

  releases:
    keywords:
      - "release"
      - "version"
      - "changelog"
    release_keywords:
      - "v1.0"
      - "stable"
      - "beta"
    scoring_weights:
      keywords: 0.6
      release: 0.8
```

### `config/tgsentinel.yml` (Entity Bindings)

```yaml
channels:
  - id: -1001234567890
    name: "Security Channel"
    profiles:
      - security # Bind global profile
      - releases
    overrides:
      keywords_extra: # Add channel-specific keywords
        - "kernel bug"
        - "CVE-2025"
      scoring_weights: # Override global weights
        urgency: 2.0 # Increase from 1.5 → 2.0
    vip_senders: [123456789]
    reaction_threshold: 5

  - id: -1009876543210
    name: "DevOps Channel"
    profiles:
      - security # Reuse same profile (no duplication!)
    overrides: {} # No customization

monitored_users:
  - id: 123456789
    name: "Alice"
    profiles:
      - security
    overrides:
      min_score: 3.0 # Lower threshold for DMs from Alice
```

---

## 🔀 Profile Resolution Flow

```bash
1. Message arrives (chat_id=-1001234567890)
        ↓
2. resolver.resolve_for_channel(-1001234567890)
        ↓
3. Lookup channel config:
   profiles: [security, releases]
   overrides: {keywords_extra: ["kernel bug"], scoring_weights: {urgency: 2.0}}
        ↓
4. Merge global profiles:
   security.keywords + releases.keywords
   security.weights + releases.weights
        ↓
5. Apply overrides:
   keywords += ["kernel bug"]
   weights["urgency"] = 2.0  (override)
        ↓
6. Build keyword sources:
   {"cve": "global:security", "kernel bug": "local", ...}
        ↓
7. Return ResolvedProfile(
     keywords=[...],
     scoring_weights={...},
     keyword_sources={...}
   )
```

---

## 🧪 Testing Patterns

### Unit Test: Profile Resolution

```python
def test_resolve_with_overrides():
    cfg = AppCfg(
        global_profiles={
            "security": ProfileDefinition(
                id="security",
                keywords=["cve"],
                scoring_weights={"urgency": 1.5}
            )
        },
        channels=[
            ChannelRule(
                id=-10011111,
                profiles=["security"],
                overrides=ChannelOverrides(
                    keywords_extra=["kernel bug"],
                    scoring_weights={"urgency": 2.0}
                )
            )
        ]
    )

    resolver = ProfileResolver(cfg)
    resolved = resolver.resolve_for_channel(-10011111)

    assert "cve" in resolved.keywords
    assert "kernel bug" in resolved.keywords
    assert resolved.scoring_weights["urgency"] == 2.0  # Overridden
    assert resolved.keyword_sources["cve"] == "global:security"
    assert resolved.keyword_sources["kernel bug"] == "local"
```

### Integration Test: End-to-End Scoring

```python
async def test_scoring_with_profiles():
    # Setup: Load config with profiles
    cfg = load_config("tests/fixtures/tgsentinel_with_profiles.yml")
    resolver = ProfileResolver(cfg)

    # Process message
    payload = {
        "chat_id": -10011111,
        "text": "Critical CVE-2025-1234 in kernel bug",
        # ...
    }

    resolved = resolver.resolve_for_channel(-10011111)
    hr = run_heuristics(
        text=payload["text"],
        resolved_profile=resolved,
        # ...
    )

    # Validate
    assert "urgent" in hr.reasons
    assert "security" in hr.reasons
    assert hr.trigger_annotations["urgent"] == "global:security"
    assert "kernel bug" in hr.trigger_annotations.values()
```

---

## 🚀 Migration Commands

### Dry-Run (Preview Changes)

```bash
python tools/migrate_profiles.py --dry-run --config config/tgsentinel.yml
```

**Output:**

```bash
[DRY-RUN] Would create profiles.yml with 3 profiles:
  - security (15 keywords, used by 5 channels)
  - releases (8 keywords, used by 3 channels)
  - opportunities (6 keywords, used by 2 channels)

[DRY-RUN] Would update tgsentinel.yml:
  - Channel -10011111: profiles=[security, releases], overrides={}
  - Channel -10022222: profiles=[security], overrides={keywords_extra: ["kernel bug"]}

[DRY-RUN] Backup would be created: config/backups/tgsentinel_20250115_100000.yml
```

### Apply Migration

```bash
python tools/migrate_profiles.py --config config/tgsentinel.yml --yes
```

**Output:**

```bash
✓ Created backup: config/backups/tgsentinel_20250115_100000.yml
✓ Created profiles.yml with 3 profiles
✓ Updated tgsentinel.yml with profile bindings
✓ Migration complete!

Next steps:
  1. Review config/profiles.yml
  2. Restart Sentinel: docker compose restart sentinel
  3. Test scoring: curl http://localhost:8080/api/channels/-10011111/effective-config
```

### Rollback

```bash
python tools/rollback_profiles.py --backup config/backups/tgsentinel_20250115_100000.yml
```

---

## 📊 Monitoring Queries

### Profile Resolution Latency (Prometheus)

```promql
histogram_quantile(0.95,
  rate(profile_resolution_duration_seconds_bucket[5m])
)
```

### Cache Hit Rate

```promql
rate(profile_cache_hits_total[5m]) /
rate(profile_cache_requests_total[5m])
```

### Alert Count by Profile

```sql
-- SQL query on sentinel.db
SELECT
  json_extract(trigger_annotations, '$.urgent') AS profile_source,
  COUNT(*) AS alert_count
FROM messages
WHERE timestamp >= datetime('now', '-1 hour')
  AND trigger_annotations IS NOT NULL
GROUP BY profile_source
ORDER BY alert_count DESC;
```

---

## 🐛 Debugging Tips

### Profile Not Resolving

```python
# In worker.py, add debug logging:
resolved = resolver.resolve_for_channel(rid)
log.debug(f"Resolved profile for {rid}: keywords={resolved.keywords}, weights={resolved.scoring_weights}")
```

### Trigger Annotations Missing

```sql
-- Check if annotations are being stored
SELECT chat_id, msg_id, triggers, trigger_annotations
FROM messages
WHERE trigger_annotations IS NOT NULL
ORDER BY timestamp DESC
LIMIT 10;
```

### Cache Not Working

```python
# Check cache stats
info = resolver.resolve_for_channel.cache_info()
print(f"Cache hits: {info.hits}, misses: {info.misses}, size: {info.currsize}")

# Clear cache manually
resolver.resolve_for_channel.cache_clear()
```

---

## 🔑 Key Design Decisions

1. **Why Two-Layer?**

   - Deduplication: Define keywords once, reuse everywhere
   - Flexibility: Override per entity without duplicating base config
   - Transparency: Track which profile triggered alert

2. **Why Caching?**

   - Performance: Profile resolution is called for every message
   - Target: <1ms per message (cache hit: ~0.01ms, miss: ~0.5ms)

3. **Why Annotations?**

   - User transparency: Show which profile/keyword triggered alert
   - Debugging: Trace scoring decisions back to config
   - Analytics: Understand profile effectiveness

4. **Why Backward Compat?**
   - Minimize disruption: Existing configs continue working
   - Gradual migration: Users can migrate at their own pace
   - Rollback safety: Can revert to old config if needed

---

## 📚 Further Reading

- **Full Evaluation:** `PROFILES_ARCHITECTURE_EVALUATION.md` (68KB)
- **Implementation Plan:** `PROFILES_IMPLEMENTATION_CHECKLIST.md` (32KB)
- **Visual Guide:** `PROFILES_ARCHITECTURE_DIAGRAMS.md` (24KB)
- **Summary:** `PROFILES_IMPLEMENTATION_SUMMARY.md` (8KB)

---

**Quick Start:** Read `PROFILES_IMPLEMENTATION_SUMMARY.md` → Review `PROFILES_ARCHITECTURE_DIAGRAMS.md` → Follow `PROFILES_IMPLEMENTATION_CHECKLIST.md`
