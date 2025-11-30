# 📋 Feedback Learning System - Quick Reference

**For:** Developers implementing the feedback learning system  
**Last Updated:** December 2025

---

## 🎯 What Gets Built (High-Level)

```bash
User clicks 👎 on message
    ↓
Feedback aggregates (need 3 FPs)
    ↓
System auto-raises threshold by 0.1
    ↓
Fewer false positives appear in feed
```

**Key insight:** No immediate changes. Aggregate first, act later.

---

## 📁 File Structure

```bash
src/tgsentinel/
├── feedback_aggregator.py         # NEW - Phase 1
│   └── FeedbackAggregator class (counters, thresholds)
├── profile_tuner.py                # NEW - Phase 1
│   └── ProfileTuner class (apply adjustments, atomic saves)
├── feedback_processor.py           # NEW - Phase 3
│   └── BatchFeedbackProcessor (queue, batch recompute)
├── semantic.py                     # MODIFY - Phase 2
│   └── Add weighted centroid support
├── api.py                          # MODIFY - All phases
│   └── Update /api/feedback, add new endpoints
└── store.py                        # MODIFY - Phase 1
    └── Add profile_adjustments table

config/
├── tgsentinel.yml                  # MODIFY - Phase 1
│   └── Add feedback_learning section
└── profiles_interest.yml           # MODIFY - Phase 2
    └── Add feedback_*_samples, pending_*_samples

ui/
├── templates/profiles/
│   └── interest_profiles.html      # MODIFY - Phase 1 & 2
│       └── Add stats display, pending buffer UI
└── static/js/profiles/
    └── interest_profiles.js        # MODIFY - Phase 1 & 2
        └── Add stats refresh, commit/rollback functions

tests/
├── unit/tgsentinel/
│   ├── test_feedback_aggregator.py # NEW - Phase 1
│   ├── test_profile_tuner.py       # NEW - Phase 1
│   └── test_feedback_processor.py  # NEW - Phase 3
├── integration/
│   └── test_feedback_learning_flow.py # NEW - Phase 4
└── benchmarks/
    └── test_feedback_performance.py # NEW - Phase 4

docs/
├── FEEDBACK_LEARNING_ROADMAP.md         # Overview
├── FEEDBACK_LEARNING_PHASE1.md          # Week 1 implementation
├── FEEDBACK_LEARNING_PHASE2.md          # Week 2 implementation
├── FEEDBACK_LEARNING_PHASE3.md          # Week 3 implementation
├── FEEDBACK_LEARNING_PHASE4.md          # Week 4 implementation
├── USER_GUIDE_FEEDBACK_LEARNING.md      # User docs
└── OPERATORS_GUIDE_FEEDBACK_LEARNING.md # Operator docs
```

---

## 🔢 Key Numbers (Memorize These)

| Threshold | Meaning                                          |
| --------- | ------------------------------------------------ |
| **3**     | Borderline FPs needed before threshold raise     |
| **2**     | Severe FPs needed before add to negative samples |
| **2**     | Strong TPs needed before add to positive samples |
| **0.1**   | Threshold increase amount (when triggered)       |
| **0.4**   | Weight for feedback samples (vs 1.0 for curated) |
| **0.25**  | Max cumulative threshold drift (cap)             |
| **3**     | Pending samples needed before commit             |
| **7**     | Feedback window in days (decay after)            |
| **10**    | Batch processing interval in minutes             |
| **5**     | Profiles pending before early batch trigger      |

---

## 🧩 Core Classes

### FeedbackAggregator

**Purpose:** Track feedback counters per profile, recommend actions

**Key Methods:**

```python
aggregator = FeedbackAggregator()

# Record feedback
recommendation = aggregator.record_feedback(
    profile_id="3000",
    label="down",          # "up" or "down"
    semantic_score=0.50,
    threshold=0.45
)
# Returns: {"action": "raise_threshold", "delta": 0.1, ...}

# Reset after action taken
aggregator.reset_stats("3000", "raise_threshold")

# Get current stats
stats = aggregator.get_stats("3000")
# Returns: FeedbackStats(borderline_fp=2, cumulative_threshold_delta=0.1, ...)
```

**State:**

- In-memory dictionary: `profile_id → FeedbackStats`
- Thread-safe with `RLock`
- Runs decay every 24 hours

---

### ProfileTuner

**Purpose:** Apply threshold adjustments, manage pending samples

**Key Methods:**

```python
tuner = ProfileTuner(engine, config_dir)

# Adjust threshold
adjustment = tuner.apply_threshold_adjustment(
    profile_id="3000",
    profile_type="interest",
    delta=0.1,
    reason="negative_feedback",
    feedback_count=3
)
# Returns: ThresholdAdjustment(old_value=0.45, new_value=0.55, ...)

# Add to pending buffer
tuner.add_to_pending_samples(
    profile_id="3000",
    sample_category="negative",
    sample_text="PUMP IT NOW 🚀",
    semantic_score=0.70,
    feedback_chat_id=-123,
    feedback_msg_id=456
)

# Commit pending
committed = tuner.commit_pending_samples("3000", "interest", "negative")

# Rollback pending
rolled_back = tuner.rollback_pending_samples("3000", "interest", "negative")
```

**Features:**

- Atomic YAML saves (temp file + `os.replace()`)
- Database recording (`profile_adjustments`, `profile_sample_additions`)
- Drift cap enforcement
- Duplicate detection

---

### BatchFeedbackProcessor

**Purpose:** Queue profiles for centroid recomputation, batch process

**Key Methods:**

```python
processor = BatchFeedbackProcessor(engine, config_dir)

# Start background task
await processor.start()

# Schedule profile for recompute
processor.schedule_recompute("3000")

# Get queue status
status = processor.get_queue_status()
# Returns: {"pending_count": 3, "pending_profiles": ["3000", "3001", "3002"], ...}

# Stop background task
await processor.stop()
```

**Behavior:**

- Background asyncio task runs every 10 minutes
- Processes batch if: interval elapsed OR 5+ profiles pending
- Batch processing = clear semantic caches for all queued profiles
- Thread-safe with `RLock`

---

## 🗄️ Database Schema

### feedback

```sql
CREATE TABLE feedback(
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  msg_id INTEGER NOT NULL,
  label TEXT NOT NULL,              -- "up" or "down"
  semantic_type TEXT,                -- "interest" or "alert"
  semantic_score REAL,               -- Score that triggered feedback
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### feedback_profiles

```sql
CREATE TABLE feedback_profiles(
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  msg_id INTEGER NOT NULL,
  profile_id TEXT NOT NULL          -- "3000", "3001", etc.
);
```

### profile_adjustments (NEW)

```sql
CREATE TABLE profile_adjustments(
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL,
  profile_type TEXT NOT NULL,       -- "interest" or "alert"
  adjustment_type TEXT NOT NULL,    -- "threshold" or "min_score"
  old_value REAL NOT NULL,
  new_value REAL NOT NULL,
  adjustment_reason TEXT,           -- "negative_feedback", "manual"
  feedback_count INTEGER DEFAULT 1,
  trigger_chat_id INTEGER,
  trigger_msg_id INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### profile_sample_additions (NEW)

```sql
CREATE TABLE profile_sample_additions(
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL,
  profile_type TEXT NOT NULL,
  sample_category TEXT NOT NULL,    -- "positive" or "negative"
  sample_text TEXT NOT NULL,
  sample_weight REAL DEFAULT 0.4,
  sample_status TEXT DEFAULT 'pending', -- "pending", "committed", "rolled_back"
  feedback_chat_id INTEGER,
  feedback_msg_id INTEGER,
  semantic_score REAL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  committed_at DATETIME
);
```

---

## 🔌 API Endpoints

### Existing (Modified)

#### POST /api/feedback

```javascript
// Request
{
  "chat_id": -123,
  "msg_id": 456,
  "label": "down",              // "up" or "down"
  "semantic_type": "interest",  // "interest" or "alert"
  "profile_ids": ["3000", "3001"],
  "semantic_score": 0.50        // Optional
}

// Response
{
  "status": "ok",
  "data": {
    "chat_id": -123,
    "msg_id": 456,
    "label": "down"
  }
}
```

**Changes:** Now calls `FeedbackAggregator` → `ProfileTuner` → `BatchFeedbackProcessor`

---

### New Endpoints

**GET /api/profiles/interest/:id/feedback-stats**

```javascript
// Response
{
  "status": "ok",
  "stats": {
    "profile_id": "3000",
    "borderline_fp": 2,
    "severe_fp": 1,
    "strong_tp": 5,
    "cumulative_threshold_delta": 0.1
  },
  "history": [
    {
      "adjustment_type": "threshold",
      "old_value": 0.45,
      "new_value": 0.55,
      "adjustment_reason": "negative_feedback",
      "created_at": "2025-12-01T10:00:00"
    }
  ]
}
```

**GET /api/profiles/interest/:id/pending-samples**

```javascript
// Response
{
  "status": "ok",
  "data": {
    "profile_id": "3000",
    "pending_positive": ["Sample 1", "Sample 2"],
    "pending_negative": ["PUMP IT 🚀", "TO THE MOON"],
    "auto_tuning": {
      "pending_commit_threshold": 3,
      "max_feedback_samples": 20
    }
  }
}
```

**POST /api/profiles/interest/:id/pending-samples/commit**

```javascript
// Request
{
  "category": "negative"  // "positive" or "negative"
}

// Response
{
  "status": "ok",
  "data": {
    "profile_id": "3000",
    "category": "negative",
    "committed_count": 2
  }
}
```

**POST /api/profiles/interest/:id/pending-samples/rollback**

```javascript
// Request
{
  "category": "negative"
}

// Response
{
  "status": "ok",
  "data": {
    "profile_id": "3000",
    "category": "negative",
    "rolled_back_count": 2
  }
}
```

#### GET /api/feedback-learning/status

```javascript
// Response
{
  "status": "ok",
  "data": {
    "aggregator": {
      "decay_running": true,
      "last_decay": "2025-12-01T00:00:00",
      "stats_summary": {
        "total_profiles": 15,
        "profiles_with_feedback": 8,
        "profiles_near_drift_cap": 1
      }
    },
    "batch_processor": {
      "pending_count": 3,
      "pending_profiles": ["3000", "3001", "3002"],
      "last_batch_time": "2025-12-01T09:50:00",
      "seconds_since_last_batch": 300
    }
  }
}
```

---

## 🧪 Testing Shortcuts

### Unit Test Template

```python
@pytest.mark.unit
def test_feedback_aggregation():
    aggregator = FeedbackAggregator()

    # Submit 3 feedbacks
    for i in range(3):
        rec = aggregator.record_feedback(
            profile_id="3000",
            label="down",
            semantic_score=0.50,
            threshold=0.45
        )

    # Third should trigger
    assert rec["action"] == "raise_threshold"
    assert rec["delta"] == 0.1
```

### Integration Test Template

```python
@pytest.mark.integration
def test_complete_flow(tmp_path):
    # Setup
    engine = init_db("sqlite:///:memory:")
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Create test profile
    profiles = {"3000": {...}}
    with open(config_dir / "profiles_interest.yml", "w") as f:
        yaml.safe_dump(profiles, f)

    # Execute flow
    aggregator = FeedbackAggregator()
    tuner = ProfileTuner(engine, config_dir)

    for i in range(3):
        rec = aggregator.record_feedback("3000", "down", 0.50, 0.45)

    if rec["action"] == "raise_threshold":
        tuner.apply_threshold_adjustment("3000", "interest", rec["delta"])

    # Verify
    with open(config_dir / "profiles_interest.yml", "r") as f:
        updated = yaml.safe_load(f)

    assert updated["3000"]["threshold"] == 0.55
```

### Performance Benchmark Template

```python
@pytest.mark.benchmark
def test_throughput():
    aggregator = FeedbackAggregator()

    start = time.time()
    for i in range(100):
        aggregator.record_feedback("3000", "down", 0.50, 0.45)
    elapsed = time.time() - start

    # Should handle 100 req/sec
    assert elapsed < 1.0
```

---

## 🐛 Common Gotchas

### 1. Forgetting to Reset Stats

**Problem:** After applying adjustment, counters still high, triggers again

```python
# WRONG
tuner.apply_threshold_adjustment(...)
# Counters still at 3, will trigger again on next feedback!

# RIGHT
tuner.apply_threshold_adjustment(...)
aggregator.reset_stats(profile_id, "raise_threshold")  # ✓
```

### 2. Not Clearing Semantic Cache

**Problem:** Adjusted threshold but scoring unchanged

```python
# WRONG
tuner.commit_pending_samples(...)
# Semantic cache still has old centroids!

# RIGHT
tuner.commit_pending_samples(...)
processor.schedule_recompute(profile_id)  # ✓ Batch will clear cache
```

### 3. Concurrent YAML Writes

**Problem:** Two adjustments at same time corrupt YAML

```python
# WRONG
with open(profiles_path, "w") as f:
    yaml.safe_dump(profiles, f)
# If crash happens here, YAML is corrupt!

# RIGHT (atomic save)
temp_fd, temp_path = tempfile.mkstemp(...)
with os.fdopen(temp_fd, "w") as f:
    yaml.safe_dump(profiles, f)
os.replace(temp_path, profiles_path)  # ✓ Atomic
```

### 4. Mixing Feedback Sample Weights

**Problem:** Feedback samples overriding curated intent

```python
# WRONG
all_samples = curated_samples + feedback_samples
centroid = np.mean([model.encode(s) for s in all_samples], axis=0)
# All samples treated equally!

# RIGHT (weighted centroid)
curated_vecs = model.encode(curated_samples)
feedback_vecs = model.encode(feedback_samples)

weighted_sum = (
    np.sum(curated_vecs, axis=0) * 1.0 +      # Curated weight = 1.0
    np.sum(feedback_vecs, axis=0) * 0.4       # Feedback weight = 0.4
)
total_weight = len(curated_vecs) + len(feedback_vecs) * 0.4
centroid = weighted_sum / total_weight  # ✓ Downweighted feedback
```

### 5. Drift Cap Math

**Problem:** Applying adjustment when already at cap

```python
# WRONG
if borderline_fp >= 3:
    tuner.apply_threshold_adjustment(delta=0.1)
# Might exceed cap!

# RIGHT
stats = aggregator.get_stats(profile_id)
if stats.cumulative_threshold_delta + 0.1 <= MAX_DRIFT:  # ✓ Check cap first
    tuner.apply_threshold_adjustment(delta=0.1)
```

---

## 📝 Logging Patterns

### Standard Log Format

```python
log.info(
    "[COMPONENT-TAG] Human-readable message",
    extra={
        "profile_id": profile_id,
        "action": "raise_threshold",
        "old_value": 0.45,
        "new_value": 0.55,
        "delta": 0.1,
        "reason": "negative_feedback",
        "feedback_count": 3
    }
)
```

### Component Tags

- `[FEEDBACK-AGG]` - FeedbackAggregator
- `[TUNER]` - ProfileTuner
- `[BATCH-PROCESSOR]` - BatchFeedbackProcessor
- `[AUTO-TUNING]` - General auto-tuning actions
- `[SEMANTIC]` - Semantic scoring changes

### What to Log

- ✅ All adjustments (threshold, samples)
- ✅ Batch processing start/end
- ✅ Feedback decay runs
- ✅ Drift cap warnings
- ✅ Errors (with stack traces)
- ❌ Individual feedback submissions (too noisy)
- ❌ Cache hits/misses (debug only)
- ❌ Sensitive data (session paths, scores)

---

## ⚡ Performance Tips

1. **Batch semantic operations:**

   ```python
   # SLOW
   for sample in samples:
       vec = model.encode(sample)

   # FAST
   vecs = model.encode(samples)  # Batch encode
   ```

2. **Reuse aggregator instance:**

   ```python
   # SLOW
   for feedback in feedbacks:
       agg = FeedbackAggregator()  # New instance each time!
       agg.record_feedback(...)

   # FAST
   agg = get_feedback_aggregator()  # Singleton
   for feedback in feedbacks:
       agg.record_feedback(...)
   ```

3. **Lazy profile loading:**

   ```python
   # SLOW
   profiles = load_all_profiles()  # Load all upfront

   # FAST
   profile = load_profile_on_demand(profile_id)  # Load only when needed
   ```

4. **Database connection pooling:**

   ```python
   # SLOW
   for adjustment in adjustments:
       with engine.begin() as con:  # New connection each time
           con.execute(...)

   # FAST
   with engine.begin() as con:  # Reuse connection
       for adjustment in adjustments:
           con.execute(...)
   ```

---

## 🆘 Emergency Commands

### Disable Feedback Learning

```yaml
# config/tgsentinel.yml
feedback_learning:
  enabled: false # ← Set to false
```

### Reset All Feedback

```sql
DELETE FROM feedback;
DELETE FROM feedback_profiles;
DELETE FROM profile_adjustments;
DELETE FROM profile_sample_additions;
```

### Reset Single Profile

```sql
-- Revert threshold manually in YAML, then:
DELETE FROM profile_adjustments WHERE profile_id = '3000';
DELETE FROM profile_sample_additions WHERE profile_id = '3000';
```

### Clear Batch Queue

```bash
# Restart sentinel
docker compose restart sentinel
# Queue is in-memory, will be cleared
```

### Force Batch Processing

```bash
curl -X POST http://localhost:8080/api/feedback-learning/trigger-batch
```

---

## 📞 When You Get Stuck

1. **Check phase docs:** Each phase has detailed implementation guide
2. **Review architecture:** See `FEEDBACK_LEARNING_ROADMAP.md`
3. **Read logs:** Look for component tags (`[FEEDBACK-AGG]`, `[TUNER]`)
4. **Query database:** Check `profile_adjustments`, `feedback` tables
5. **Test in isolation:** Use unit tests to reproduce issue
6. **Ask for help:** Open GitHub issue with logs and minimal reproduction

---

**🎯 Ready to code? Start with [Phase 1 Implementation](FEEDBACK_LEARNING_PHASE1.md)**
