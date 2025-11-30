# Batch Processor Persistence Architecture

## Data Flow Diagram

```text
┌─────────────────────────────────────────────────────────────────┐
│                    Batch Processing Lifecycle                   │
└─────────────────────────────────────────────────────────────────┘

                     INITIALIZATION
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  BatchFeedbackProcessor.__init__()   │
        │  - Check if redis_client provided    │
        └──────────────────┬───────────────────┘
                           │
                  ┌────────┴────────┐
                  │  Redis client?  │
                  └────────┬────────┘
                           │
                   ┌───────┴───────┐
                   │      YES      │
                   ▼               ▼
    ┌──────────────────────┐   ┌──────────────────┐
    │ _load_state_from_    │   │  Continue with   │
    │      redis()         │   │  empty queue     │
    │                      │   │  (memory-only)   │
    │ • Read queue JSON    │   └──────────────────┘
    │ • Parse profiles     │
    │ • Read last_batch_   │
    │   time ISO string    │
    │ • Restore state      │
    └──────────────────────┘


                    RUNTIME OPERATIONS
                           │
        ┌──────────────────┴───────────────────┐
        │                                      │
        ▼                                      ▼
┌──────────────────┐              ┌──────────────────────┐
│  schedule_       │              │   process_batch()    │
│  recompute()     │              │                      │
│                  │              │  1. Get profiles     │
│  1. Add to queue │              │  2. Clear queue      │
│  2. Log count    │              │  3. Update time      │
│  3. Save state   │              │  4. Save state       │
└────────┬─────────┘              │  5. Clear caches     │
         │                        │  6. Record history   │
         │                        └──────────┬───────────┘
         │                                   │
         └───────────┬───────────────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │ _save_state_to_redis()│
         │                       │
         │  • Serialize queue    │
         │    to JSON            │
         │  • Format time to ISO │
         │  • SET both keys      │
         │  • Log or handle err  │
         └───────────────────────┘


                    RESTART RECOVERY
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  Service restart (docker compose     │
        │  restart sentinel)                   │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  New BatchFeedbackProcessor instance │
        │  created in main.py                  │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  _load_state_from_redis() called     │
        │                                      │
        │  Redis GET tgsentinel:batch_         │
        │    processor:queue                   │
        │  → ["3000", "3001", "3002"]          │
        │                                      │
        │  Redis GET tgsentinel:batch_         │
        │    processor:last_batch_time         │
        │  → "2025-12-07T12:30:00.123456"      │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  Queue and timestamp RESTORED        │
        │  - profiles_pending: {3000,3001,3002}│
        │  - last_batch_time: 2025-12-07 12:30 │
        │                                      │
        │  "Last Batch" card shows correct time│
        │  Pending profiles resume processing  │
        └──────────────────────────────────────┘
```

## Redis Schema

```text
Redis Database (decode_responses=True)
│
├─ tgsentinel:batch_processor:queue
│   Type: String (JSON array)
│   Value: ["3000", "3001", "3002", ...]
│   TTL: None (persistent)
│   Updated: On schedule_recompute() and process_batch()
│
└─ tgsentinel:batch_processor:last_batch_time
    Type: String (ISO 8601 datetime)
    Value: "2025-12-07T12:30:00.123456"
    TTL: None (persistent)
    Updated: On process_batch()
```

## State Transitions

```text
┌─────────────┐
│   STARTUP   │ ─────► Redis keys exist? ──Yes──► Restore state
└──────┬──────┘                            │
       │                                   No
       │                                   │
       ▼                                   ▼
┌─────────────┐                    ┌──────────────┐
│ Empty Queue │                    │Queue restored│
│ last_batch= │                    │last_batch=   │
│   now()     │                    │  restored    │
└──────┬──────┘                    └──────┬───────┘
       │                                  │
       └──────────────┬───────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │   RUNNING     │
              │ (periodic     │
              │  batch task)  │
              └───────┬───────┘
                      │
         ┌────────────┼────────────┐
         │                         │
         ▼                         ▼
  ┌─────────────┐          ┌──────────────┐
  │ Feedback    │          │   Batch      │
  │  received   │          │   triggered  │
  │             │          │   (manual or │
  │ schedule_   │          │    auto)     │
  │ recompute() │          │              │
  │             │          │ process_     │
  │ Add to queue│          │ batch()      │
  │ Save state  │          │              │
  └─────────────┘          │ Clear queue  │
                           │ Update time  │
                           │ Save state   │
                           └──────────────┘
```

## Error Handling

```text
┌──────────────────────────┐
│  Redis Operation Failed  │
└────────┬─────────────────┘
         │
    ┌────┴─────┐
    │          │
    ▼          ▼
┌────────┐  ┌─────────┐
│  LOAD  │  │  SAVE   │
│  Error │  │  Error  │
└───┬────┘  └────┬────┘
    │            │
    ▼            ▼
┌────────┐  ┌──────────┐
│ Log    │  │ Log      │
│ warning│  │ warning  │
│        │  │          │
│Continue│  │ Continue │
│with    │  │ with     │
│empty   │  │ in-memory│
│queue   │  │ state    │
└────────┘  └──────────┘
```

Both failures are **non-fatal** — the processor continues operating with in-memory state only.

## API Integration

```text
UI Request: GET /api/feedback-learning/status
    │
    ▼
┌─────────────────────────────────┐
│  api.py: get_feedback_learning_ │
│          status()               │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  get_batch_processor()          │
│  .get_queue_status()            │
└──────────────┬──────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│  Returns:                        │
│  {                               │
│    "pending_count": 3,           │
│    "pending_profiles": [...],    │
│    "last_batch_time": "...",     │
│    "seconds_since_last_batch": N │
│  }                               │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│  UI renders:                     │
│  • Batch Queue card (count)      │
│  • Last Batch card (time ago)    │
│  • Queue table (profile IDs)     │
└──────────────────────────────────┘
```

---

**Key Insight**: State persistence happens **eagerly** (after each change), not lazily. This ensures Redis always reflects the current in-memory state, minimizing data loss window.
