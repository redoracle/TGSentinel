# 🔧 Feedback Learning System - Operator's Guide

System administration guide for operators and DevOps engineers.

---

## System Architecture

### Components

1. **FeedbackAggregator** (`feedback_aggregator.py`)

   - In-memory counters per **Interest** profile
   - Evaluates feedback and recommends threshold/sample actions
   - Runs decay every 24 hours

2. **AlertFeedbackAggregator** (`alert_feedback_aggregator.py`)

   - In-memory counters per **Alert** profile
   - Evaluates feedback and recommends min_score raises only
   - Separate from interest aggregator (no race conditions)
   - Shares decay schedule with interest aggregator

3. **ProfileTuner** (`profile_tuner.py`)

   - Applies threshold/min_score adjustments
   - Manages pending sample buffer (interest profiles only)
   - Atomic YAML saves
   - Supports both profile types via `profile_type` parameter

4. **BatchFeedbackProcessor** (`feedback_processor.py`)

   - Batches centroid recomputations (interest profiles only)
   - Runs every 10 minutes OR when 5+ profiles pending
   - Background asyncio task

5. **Semantic Scoring** (`semantic.py`)
   - Weighted centroid calculation (interest profiles only)
   - Cache management
   - Model: `all-MiniLM-L6-v2`

### Data Flow

```bash
User Feedback
    ↓
POST /api/feedback → Store in DB
    ↓
FeedbackAggregator.record_feedback()
    ↓
Evaluate counters → Recommend action
    ↓
ProfileTuner.apply_adjustment() → Update YAML
    ↓
BatchFeedbackProcessor.schedule_recompute() → Add to queue
    ↓
[10 min interval or 5+ profiles]
    ↓
process_batch() → Clear semantic caches
    ↓
Next scoring request → Recompute centroids with new samples/threshold
```

---

## Configuration

### Master Config (`config/tgsentinel.yml`)

```yaml
alerts:
  enabled: true
  min_score: 0.7
  feedback_learning:
    enabled: true
    # Alert-specific feedback parameters (min_score adjustments only)
    min_negative_feedback: 3 # Need 3 false positives to trigger adjustment
    negative_rate_threshold: 0.3 # 30% false positive rate threshold
    min_score_delta: 0.1 # How much to raise min_score per adjustment
    max_min_score_delta: 0.5 # Maximum cumulative drift allowed
    feedback_window_days: 7 # Consider feedback from last 7 days

feedback_learning:
  enabled: true
  # Interest profile feedback parameters (threshold + sample augmentation)
  aggregation:
    borderline_fp_threshold: 3
    severe_fp_threshold: 2
    strong_tp_threshold: 2
    feedback_window_days: 7
    decay_interval_hours: 24

  drift_caps:
    max_threshold_delta: 0.25
    max_negative_weight_delta: 0.1
# Note: batch_processing config is hardcoded in BatchFeedbackProcessor class:
# - BATCH_INTERVAL_SECONDS = 600 (10 minutes)
# - BATCH_SIZE_THRESHOLD = 5 (process when 5+ profiles pending)
```

### Profile-Level Config (`config/profiles_interest.yml`)

```yaml
3000:
  # ... existing fields ...

  auto_tuning:
    enabled: true
    feedback_sample_weight: 0.4
    pending_commit_threshold: 3
    max_feedback_samples: 20
    max_delta_threshold: 0.25
    max_delta_negative_weight: 0.1
```

---

## Monitoring

### Health Endpoints

````bash
# Overall system status (includes batch processor queue, aggregator stats)
curl http://localhost:8080/api/feedback-learning/status

# Interest profile stats (feedback counts, cumulative drift, adjustment history)
curl http://localhost:8080/api/profiles/interest/3000/feedback-stats

# Alert profile stats (negative rate, approval rate, cumulative drift)
curl http://localhost:8080/api/profiles/alert/1000/feedback-stats?days=30

# Batch processing history (last N batches with timing and profile counts)
curl http://localhost:8080/api/feedback-learning/batch-history?limit=20

# Pending samples for review
curl http://localhost:8080/api/profiles/interest/3000/pending-samples

# Commit pending samples (after review)
curl -X POST http://localhost:8080/api/profiles/interest/3000/pending-samples/commit \
  -H "Content-Type: application/json" \
  -d '{"category": "negative"}'
### Dashboard

The feedback learning system is monitored via API endpoints. There is no dedicated UI dashboard currently, but you can monitor via:

- `/api/feedback-learning/status` - Overall system health
- `/api/feedback-learning/batch-history` - Recent batch processing activity
- Logs with tags `[FEEDBACK-AGG]`, `[ALERT-FEEDBACK-AGG]`, `[BATCH-PROCESSOR]`, `[TUNER]`

Key metrics to monitor:

- Batch queue size (pending_count in status endpoint)
- Time since last batch (seconds_since_last_batch)
- Profiles with recent feedback (from database queries)
# Manually trigger batch processing (admin only)
curl -X POST http://localhost:8080/api/feedback-learning/trigger-batch \
  -H "X-Admin-Token: your-admin-token"

### Dashboard

Access monitoring dashboard:

```bash
http://localhost:5001/admin/feedback-learning-monitor
````

Shows:

- Batch queue size
- Time since last batch
- Profiles with recent feedback
- Profiles near drift cap

### Logs

**Key log patterns:**

````bash
# Interest feedback aggregation
docker compose logs sentinel | grep "\[FEEDBACK-AGG\]"

# Alert feedback aggregation
docker compose logs sentinel | grep "\[ALERT-FEEDBACK-AGG\]"

# Alert feedback processing
docker compose logs sentinel | grep "\[ALERT-FEEDBACK\]"

# Batch processing (interest profiles only)
docker compose logs sentinel | grep "BATCH-PROCESSOR"

# Auto-tuning actions (both types)
docker compose logs sentinel | grep "AUTO-TUNING"

# Profile tuner
#### `feedback`

```sql
CREATE TABLE feedback(
  chat_id INTEGER,
  msg_id INTEGER,
  label INTEGER,  -- 1=thumbs up, 0=thumbs down
  semantic_type TEXT DEFAULT 'alert_keyword',
  semantic_score REAL,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id)
);
```Database

#### `feedback_profiles`

```sql
CREATE TABLE feedback_profiles(
  chat_id INTEGER,
  msg_id INTEGER,
  profile_id TEXT,
  label TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chat_id, msg_id, profile_id)
);
```
#### `profile_adjustments`

```sql
CREATE TABLE profile_adjustments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT NOT NULL,
  profile_type TEXT NOT NULL,  -- 'interest' or 'alert'
  adjustment_type TEXT NOT NULL,  -- 'threshold' or 'min_score'
  old_value REAL NOT NULL,
  new_value REAL NOT NULL,
  adjustment_reason TEXT,
  feedback_count INTEGER DEFAULT 1,
  trigger_chat_id INTEGER,
  trigger_msg_id INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
````

#### `profile_sample_additions`

```sql
CREATE TABLE profile_sample_additions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT NOT NULL,
  profile_type TEXT NOT NULL,  -- 'interest' or 'alert'
  sample_category TEXT NOT NULL,  -- 'positive' or 'negative'
  sample_text TEXT NOT NULL,
  sample_weight REAL DEFAULT 0.4,
  sample_status TEXT DEFAULT 'pending',  -- 'pending', 'committed', 'rolled_back'
  feedback_chat_id INTEGER,
  feedback_msg_id INTEGER,
  semantic_score REAL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  committed_at DATETIME
);
```

#### `profile_sample_additions` (Updated Schema)

```sql
CREATE TABLE profile_sample_additions(
  id INTEGER PRIMARY KEY,
  profile_id TEXT,
  profile_type TEXT,
  sample_category TEXT,
  sample_text TEXT,
  sample_weight REAL,
  sample_status TEXT,
  feedback_chat_id INTEGER,
  feedback_msg_id INTEGER,
  semantic_score REAL,
  created_at DATETIME,
  committed_at DATETIME
);
```

#### `batch_history`

````sql
CREATE TABLE batch_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at DATETIME NOT NULL,
  completed_at DATETIME,
  profiles_processed INTEGER NOT NULL,
  profile_ids TEXT NOT NULL,  -- Comma-separated list
  elapsed_seconds REAL NOT NULL,
  trigger_type TEXT NOT NULL,  -- 'automatic' or 'manual'
**Profiles near drift cap:**

```sql
-- Interest profiles (drift cap: 0.25, warn at 0.20)
SELECT
  profile_id,
  SUM(new_value - old_value) as cumulative_delta,
  COUNT(*) as adjustment_count,
  MAX(created_at) as last_adjustment
FROM profile_adjustments
WHERE adjustment_type = 'threshold' AND profile_type = 'interest'
GROUP BY profile_id
HAVING cumulative_delta >= 0.20
ORDER BY cumulative_delta DESC;

-- Alert profiles (drift cap: 0.5, warn at 0.40)
SELECT
  profile_id,
  SUM(new_value - old_value) as cumulative_delta,
  COUNT(*) as adjustment_count,
  MAX(created_at) as last_adjustment
FROM profile_adjustments
WHERE adjustment_type = 'min_score' AND profile_type = 'alert'
GROUP BY profile_id
HAVING cumulative_delta >= 0.40
ORDER BY cumulative_delta DESC;
**Recent feedback summary:**

```sql
SELECT
  fp.profile_id,
  CASE WHEN f.label = 1 THEN 'positive' ELSE 'negative' END as feedback_type,
  f.semantic_type,
  COUNT(*) as count,
  AVG(f.semantic_score) as avg_score,
  MIN(f.created_at) as first_feedback,
  MAX(f.created_at) as last_feedback
FROM feedback f
JOIN feedback_profiles fp ON f.chat_id = fp.chat_id AND f.msg_id = fp.msg_id
WHERE f.created_at > datetime('now', '-7 days')
GROUP BY fp.profile_id, f.label, f.semantic_type
**Pending samples by profile:**

```sql
SELECT
  profile_id,
  profile_type,
  sample_category,
  COUNT(*) as pending_count,
  MIN(created_at) as oldest_pending,
  MAX(created_at) as newest_pending
FROM profile_sample_additions
WHERE sample_status = 'pending'
GROUP BY profile_id, profile_type, sample_category
ORDER BY pending_count DESC;
````

**Batch processing history:**

````sql
SELECT
  started_at,
  completed_at,
  profiles_processed,
  profile_ids,
  elapsed_seconds,
  trigger_type,
  status
FROM batch_history
ORDER BY started_at DESC
LIMIT 20;
```UP BY profile_id
HAVING cumulative_delta >= 0.40
ORDER BY cumulative_delta DESC;
````

**Recent feedback summary:**

```sql
SELECT
  fp.profile_id,
  f.label,
  COUNT(*) as count,
  AVG(f.semantic_score) as avg_score
FROM feedback f
JOIN feedback_profiles fp ON f.chat_id = fp.chat_id AND f.msg_id = fp.msg_id
WHERE f.updated_at > datetime('now', '-7 days')
GROUP BY fp.profile_id, f.label
ORDER BY count DESC;
```

**Pending samples by profile:**

```sql
SELECT
  profile_id,
  sample_category,
  COUNT(*) as pending_count
FROM profile_sample_additions
WHERE sample_status = 'pending'
GROUP BY profile_id, sample_category
ORDER BY pending_count DESC;
```

---

## Batch Troubleshooting

### Issue: Batch queue growing indefinitely

**Symptoms:**

- Pending count > 20
- Last batch time > 15 minutes (should run every 10 minutes)

**Diagnosis:**

```bash
curl http://localhost:8080/api/feedback-learning/status | jq '.data.batch_processor'
```

**Possible causes:**

- Batch processor task crashed or not started
- Semantic model loading failure
- YAML write lock or permission issues
- Service restart without graceful shutdown

**Resolution:**

1. Check if batch processor is running:

   ```bash
   curl http://localhost:8080/api/feedback-learning/status | jq '.data.batch_processor.running'
   ```

2. Check logs for batch processor errors:

   ```bash
   docker compose logs sentinel | grep "BATCH-PROCESSOR"
   ```

3. Restart sentinel service (this will restart batch processor):

### Issue: Adjustments not persisting

**Symptoms:**

- Adjustment appears in logs but YAML unchanged
- Database shows adjustment but profile behavior unchanged

**Diagnosis:**

```bash
# Check YAML permissions
docker exec tgsentinel-sentinel-1 ls -la /app/config/profiles_interest.yml
docker exec tgsentinel-sentinel-1 ls -la /app/config/profiles_alert.yml

# Check for temp file remnants
docker exec tgsentinel-sentinel-1 ls /app/config/ | grep -E "profiles_(interest|alert).*\.yml"
```

**Resolution:**

1. Verify write permissions:

   ```bash
   docker exec tgsentinel-sentinel-1 touch /app/config/test_write
   docker exec tgsentinel-sentinel-1 rm /app/config/test_write
   ```

2. Check for stale temp files (created by atomic save but not cleaned):

   ```bash
   docker exec tgsentinel-sentinel-1 rm /app/config/profiles_interest_*.yml
   docker exec tgsentinel-sentinel-1 rm /app/config/profiles_alert_*.yml
   ```

3. Check logs for specific save errors:

   ```bash
   docker compose logs sentinel | grep -E "TUNER.*Failed to save"
   ```

4. Verify write permissions:

   ```bash
   docker exec tgsentinel-sentinel-1 touch /app/config/test_write
   docker exec tgsentinel-sentinel-1 rm /app/config/test_write
   ```

### Issue: Feedback decay not running

**Symptoms:**

- Feedback older than 7 days still counted in aggregator stats
- `last_decay` timestamp not updating (check via status endpoint)

**Diagnosis:**

```bash
curl http://localhost:8080/api/feedback-learning/status | jq '.data.aggregator.last_decay'
```

**Resolution:**

1. Check if decay task is running:

   ```bash
   curl http://localhost:8080/api/feedback-learning/status | jq '.data.aggregator.decay_running'
   docker compose logs sentinel | grep "FEEDBACK-AGG.*decay task"
   ```

2. Restart sentinel to restart decay task:

   ```bash
   docker compose restart sentinel
   ```

3. Verify decay is now running after restart:

   ```bash
   # Wait 24 hours or check logs for next scheduled decay
   docker compose logs sentinel | grep "FEEDBACK-AGG.*Running feedback decay"
   ```

### Issue: Performance degradation

**Symptoms:**

- Slow feedback submission (> 500ms)
- High CPU during batch processing
- Slow semantic scoring

**Diagnosis:**

```bash
# Check batch size and timing
curl http://localhost:8080/api/feedback-learning/status | jq '.data.batch_processor'

# Check model cache and profile count
curl http://localhost:8080/api/health/semantic

# Check recent batch history for slow batches
curl http://localhost:8080/api/feedback-learning/batch-history?limit=10 | jq '.data[] | select(.elapsed_seconds > 5)'
```

**Resolution:**

1. Check if batch sizes are too large:

   ```bash
   # If profiles_processed consistently > 20, consider adjusting threshold
   curl http://localhost:8080/api/feedback-learning/batch-history?limit=10
   ```

2. Batch processor settings are hardcoded in `BatchFeedbackProcessor` class:

   - `BATCH_INTERVAL_SECONDS = 600` (10 minutes)
   - `BATCH_SIZE_THRESHOLD = 5` (process when 5+ profiles pending)

   To adjust, modify `src/tgsentinel/feedback_processor.py` and rebuild.

3. Clear semantic cache by restarting (happens automatically on restart):

   ```bash
   docker compose restart sentinel
   ```

4. Check for concurrent feedback submissions creating lock contention:

   ```bash
   docker compose logs sentinel | grep "FEEDBACK.*Failed" | wc -l
   ```

5. Clear semantic cache periodically:

   ```bash
   docker compose restart sentinel
   ```

6. Increase batch size threshold (batch less frequently):

   ```yaml
   batch_processing:
     size_threshold: 10 # 10 profiles instead of 5
   ```

---

## Rollback Procedures

### Rollback Automatic Adjustment

**Scenario:** Bad threshold adjustment or sample addition

**Steps:**

1. **Identify adjustment:**

   ```sql
   SELECT * FROM profile_adjustments
   WHERE profile_id = '3000'
   ORDER BY created_at DESC
   LIMIT 1;
   ```

2. **Revert YAML manually:**

   ```bash
   docker exec -it tgsentinel-sentinel-1 nano /app/config/profiles_interest.yml
   # Change threshold back to old value
   ```

3. **Clear semantic cache:**

   ```bash
   docker compose restart sentinel
   ```

4. **Reset aggregator stats (optional):**

   - Via API (if implemented):

     ```bash
     curl -X POST http://localhost:8080/api/profiles/interest/3000/reset-stats
     ```

   - Or restart sentinel to clear in-memory stats

---

### Rollback Pending Samples

**Scenario:** Bad samples in pending buffer

**Steps:**

1. **Via UI:**

   - Go to profile settings
   - Click "Rollback" button in pending samples section

2. **Via API:**

   ```bash
   curl -X POST http://localhost:8080/api/profiles/interest/3000/pending-samples/rollback \
     -H "Content-Type: application/json" \
     -d '{"category": "negative"}'
   ```

---

### Full System Reset

**Scenario:** Feedback learning system misbehaving, need clean slate

**Steps:**

1. **Backup data:**

   ```bash
   docker exec tgsentinel-sentinel-1 sqlite3 /app/data/sentinel.db \
     ".backup /app/data/sentinel_backup.db"
   cp config/profiles_interest.yml config/profiles_interest_backup.yml
   ```

2. **Clear feedback data:**

   ```sql
   DELETE FROM feedback;
   DELETE FROM feedback_profiles;
   DELETE FROM profile_adjustments;
   DELETE FROM profile_sample_additions;
   ```

3. **Reset YAML to defaults:**

   ```bash
   cp config/profiles_interest_backup.yml config/profiles_interest.yml
   ```

4. **Restart:**

   ```bash
   docker compose restart sentinel
   ```

---

## Performance Tuning

### Target Metrics

- Feedback submission: < 100ms p99
- Batch processing (10 profiles): < 2s
- Profile load (50 profiles): < 500ms
- Semantic scoring: < 50ms per message

### Tuning Knobs

**1. Batch interval** (trade-off: freshness vs CPU):

- Lower = fresher centroids, more frequent processing
- Higher = less CPU overhead, stale centroids longer

**2. Batch size threshold** (trade-off: latency vs batching efficiency):

- Lower = faster recomputation, less batching benefit
- Higher = better batching, longer wait for recomputation

**3. Feedback window** (trade-off: adaptation speed vs stability):

- Shorter = faster adaptation to changing preferences
- Longer = more stable adjustments, less twitchy

**4. Aggregation thresholds** (trade-off: safety vs responsiveness):

- Higher = safer (more feedback needed), slower adjustments
- Lower = faster adjustments, riskier

---

## Security Considerations

### Data Privacy

- Feedback data contains message text
- Ensure proper access controls on database
- Consider GDPR implications for feedback retention

### Access Control

- Feedback endpoints should be authenticated
- Admin endpoints (trigger batch, reset stats) require elevated permissions
- Monitor for abuse (excessive feedback submissions)

### Audit Trail

- All adjustments logged to `profile_adjustments` table
- Track who triggered manual overrides
- Retain adjustment history for compliance

---

## Troubleshooting

### Issue: Alert adjustments not triggering

**Symptoms:**

- Many negative feedbacks recorded
- No min_score adjustments applied
- Alert aggregator stats show high negative count

**Diagnosis:**

```bash
# Check alert feedback stats
curl http://localhost:8080/api/profiles/alert/1000/feedback-stats

# Check negative rate
docker compose logs sentinel | grep "ALERT-FEEDBACK.*negative rate"
```

**Possible causes:**

- Negative rate below 30% threshold (too many positive feedbacks)
- Less than 3 negative feedbacks
- Drift cap reached (0.5)

**Resolution:**

1. Check negative rate in stats:

   ```bash
   curl http://localhost:8080/api/profiles/alert/1000/feedback-stats | jq '.data.aggregator_stats.negative_rate'
   ```

2. If rate < 0.30, more negative feedbacks needed
3. If drift cap reached, manual min_score adjustment required
4. Check recommendation field in stats for suggested action

---

## Maintenance Tasks

### Daily

- [ ] Check monitoring dashboard for warnings
- [ ] Review profiles near drift cap
- [ ] Verify batch processing running regularly

### Weekly

- [ ] Review recent adjustments for anomalies
- [ ] Check feedback submission rates
- [ ] Verify decay task removing old feedback

### Monthly

- [ ] Analyze performance metrics
- [ ] Review and optimize configuration
- [ ] Clean up old adjustment history (if needed)

---

## Related Documentation

- [User Guide](USER_GUIDE_FEEDBACK_LEARNING.md) - End-user documentation
- [Engineering Guidelines](ENGINEERING_GUIDELINES.md) - Architecture details
- [Phase Implementation Plans](FEEDBACK_LEARNING_PHASE1.md) - Development roadmap
