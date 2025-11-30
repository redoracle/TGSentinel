# Batch Processor Persistence Implementation

## Overview

The batch feedback processor now includes **Redis persistence** for full reliability across service restarts. Previously, the pending queue and last batch timestamp were stored only in memory, leading to data loss on restart.

## What Was Fixed

### Before

- ❌ Pending profiles queue: **in-memory only** (lost on restart)
- ❌ Last batch timestamp: **in-memory only** (reset to current time on restart)
- ✅ Batch history: correctly persisted to `batch_history` table

### After

- ✅ Pending profiles queue: **persisted to Redis** (`tgsentinel:batch_processor:queue`)
- ✅ Last batch timestamp: **persisted to Redis** (`tgsentinel:batch_processor:last_batch_time`)
- ✅ Batch history: still correctly persisted to `batch_history` table

## Redis Keys

Following the TG Sentinel Redis key conventions (`tgsentinel:*`):

| Key                                          | Format              | Purpose                                   | Example                        |
| -------------------------------------------- | ------------------- | ----------------------------------------- | ------------------------------ |
| `tgsentinel:batch_processor:queue`           | JSON array          | List of profile IDs pending recomputation | `["3000", "3001", "3002"]`     |
| `tgsentinel:batch_processor:last_batch_time` | ISO datetime string | Timestamp of last completed batch         | `"2025-12-07T12:30:00.123456"` |

## Implementation Details

### State Persistence Flow

1. **On Initialization** (`__init__`):

   - If Redis client is provided, call `_load_state_from_redis()`
   - Restore `profiles_pending` set from JSON array
   - Restore `last_batch_time` from ISO datetime string
   - Log successful restoration or gracefully handle errors

2. **On Profile Scheduling** (`schedule_recompute`):

   - Add profile to in-memory queue
   - Immediately call `_save_state_to_redis()` to persist

3. **After Batch Processing** (`process_batch`):
   - Clear in-memory queue
   - Update `last_batch_time` to now
   - Immediately call `_save_state_to_redis()` to persist

### Error Handling

The implementation gracefully handles Redis failures:

- **Load failures**: Log warning, continue with empty queue (default state)
- **Save failures**: Log warning, continue operating (in-memory queue still works)
- **No Redis client**: Processor works in memory-only mode (backward compatible)

This ensures the system remains operational even if Redis is temporarily unavailable.

### Thread Safety

All Redis operations are protected by the existing `RecomputeQueue.lock` (threading.RLock):

- Queue reads/writes are atomic
- State persistence calls happen within lock-protected sections
- No race conditions between scheduler and batch processor

## Code Changes

### Modified Files

1. **`src/tgsentinel/feedback_processor.py`**:

   - Added `json` and `Redis` imports
   - Added Redis key constants (`BATCH_QUEUE_KEY`, `BATCH_LAST_TIME_KEY`)
   - Added `redis_client` parameter to `__init__`
   - Added `_load_state_from_redis()` method
   - Added `_save_state_to_redis()` method
   - Call `_save_state_to_redis()` after scheduling and batch processing
   - Updated `get_batch_processor()` signature to accept `redis_client`

2. **`src/tgsentinel/main.py`**:

   - Pass Redis client `r` to `get_batch_processor(engine, config_dir, r)`

3. **`tests/unit/tgsentinel/test_batch_feedback_processor.py`** (NEW):
   - 10 comprehensive unit tests for persistence behavior
   - Tests for load/save operations
   - Tests for error handling
   - Tests for state restoration accuracy

## Testing

### Unit Tests (10 tests, all passing)

```bash
python -m pytest tests/unit/tgsentinel/test_batch_feedback_processor.py -v
```

Tests verify:

- ✅ Processor initializes without Redis (backward compatible)
- ✅ Processor handles empty Redis state
- ✅ Queue restoration from Redis
- ✅ Timestamp restoration from Redis
- ✅ State saved after scheduling
- ✅ State saved after batch processing
- ✅ Graceful handling of Redis save errors
- ✅ Graceful handling of Redis load errors
- ✅ Full state restoration (queue + timestamp)
- ✅ Queue status reflects persisted state

### Integration Testing

To validate persistence across restarts:

1. **Schedule profiles for recomputation**:

   ```bash
   # Via feedback API
   curl -X POST http://localhost:8080/api/feedback \
     -H "Content-Type: application/json" \
     -d '{"profile_id": "3000", "chat_id": 123, "msg_id": 456, "label": 1}'
   ```

2. **Verify queue state in Redis**:

   ```bash
   docker exec -it tgsentinel-redis-1 redis-cli
   > GET tgsentinel:batch_processor:queue
   > GET tgsentinel:batch_processor:last_batch_time
   ```

3. **Restart Sentinel service**:

   ```bash
   docker compose restart sentinel
   ```

4. **Check logs for restoration**:

   ```bash
   docker compose logs sentinel | grep "BATCH-PROCESSOR.*Restored"
   ```

   Expected output:

   ```bash
   [BATCH-PROCESSOR] Restored 3 profiles from Redis
   [BATCH-PROCESSOR] Restored last batch time: 2025-12-07T12:30:00.123456
   ```

5. **Verify queue status via API**:

   ```bash
   curl http://localhost:8080/api/feedback-learning/status
   ```

   Check `batch_processor.pending_count` and `batch_processor.seconds_since_last_batch`.

## Benefits

1. **Reliability**: No data loss on restart
2. **Accuracy**: "Last Batch" card shows correct time after restart
3. **Consistency**: Queue state survives crashes and deployments
4. **Backward Compatibility**: Works without Redis (memory-only mode)
5. **Observability**: Clear logs for state restoration

## Architecture Compliance

This implementation follows TG Sentinel architectural guidelines:

- ✅ Redis keys use `tgsentinel:*` namespace
- ✅ No sensitive data in Redis (only profile IDs and timestamps)
- ✅ Graceful degradation if Redis unavailable
- ✅ Thread-safe state management
- ✅ Structured logging with `[BATCH-PROCESSOR]` tags
- ✅ Unit tests with `@pytest.mark.unit` marker
- ✅ No cross-service violations (Sentinel-only feature)

## References

- **Instruction Files**:

  - `.github/instructions/DB_Architecture.instructions.md`: Redis key conventions
  - `.github/instructions/Concurrency.instructions.md`: Thread safety patterns
  - `.github/instructions/TESTS.instructions.md`: Test structure and markers

- **Related Code**:
  - `src/tgsentinel/redis_operations.py`: Redis client wrapper
  - `src/tgsentinel/digest_scheduler.py`: Similar Redis persistence pattern
  - `src/tgsentinel/api.py`: Batch status endpoints

---

**Implementation Date**: December 7, 2025  
**Status**: ✅ Complete and Tested
