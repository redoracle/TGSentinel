# TG Sentinel — AI Agent Instructions

## Architecture Overview

TG Sentinel is a Dockerized Telegram monitoring service with strict **dual-database architecture** and **single-owner session pattern**. Two main containers communicate via HTTP/Redis:

- **Sentinel container** (`src/tgsentinel/`): Owns Telegram session, runs Telethon client, exposes HTTP API on port 8080
- **UI container** (`ui/`): Flask web interface, owns `ui.db`, delegates all Telegram operations to Sentinel

**Critical Rule**: UI NEVER directly accesses `tgsentinel.session` or imports Telethon. All communication goes through:
- HTTP API: `http://sentinel:8080/api/*`
- Redis IPC: `tgsentinel:auth_queue`, `tgsentinel:request:*`, `tgsentinel:response:*`

## Database Ownership (Non-Negotiable)

```
sentinel container:
  - tgsentinel.session (Telethon SQLite, Sentinel-only)
  - sentinel.db (app data, owned by Sentinel)
  Volume: tgsentinel_sentinel_data

ui container:
  - ui.db (UI state, settings, cached alerts)
  Volume: tgsentinel_ui_data
```

**Violations to avoid**: UI code importing `from tgsentinel.store`, opening session files directly, mounting sentinel volumes.

## Key Workflows

### Running Tests
```bash
make test              # All tests via tools/run_tests.py
pytest tests/          # Direct pytest
pytest -k test_auth    # Specific pattern
make test-cov          # With coverage report
```

### Code Formatting
```bash
make format            # Black + isort
make format-check      # CI validation (don't modify)
```

### Docker Development
```bash
make docker-build      # Build images
make docker-up         # Start services
make docker-logs       # Follow sentinel logs
docker compose logs ui # UI logs
docker compose down -v # Clean restart (removes volumes)
```

### Session Management
Session upload flow (see `DB_Architecture.instructions.md` §6):
1. Browser → UI: `POST /api/session/upload` (file upload)
2. UI → Sentinel: `POST /api/session/import` (forwards file via HTTP)
3. Sentinel writes to `/app/data/tgsentinel.session` and reinitializes Telethon

**Never** copy session files between volumes or use temp directories.

## Concurrency Model

All Sentinel handlers run in asyncio event loop (see `Concurrency.instructions.md`):

```python
# Handler registry pattern
self.tasks: dict[str, asyncio.Task] = {
    "CHATS-HANDLER": asyncio.create_task(run_chats_handler()),
    "USERS-HANDLER": asyncio.create_task(run_users_handler()),
    "CACHE-REFRESHER": asyncio.create_task(run_cache_refresher()),
}
```

- **I/O-bound**: Redis, Telethon, HTTP → stay in event loop
- **CPU-bound**: Embeddings, semantic scoring → use `ProcessPoolExecutor`
- Communication: `asyncio.Queue` (in-process), Redis Streams (cross-container)

Handler tags in logs: `[CHATS-HANDLER]`, `[USERS-HANDLER]`, `[DIALOGS-HANDLER]`, `[CACHE-REFRESHER]`, `[JOBS-HANDLER]`

## Configuration System

YAML-based config in `config/tgsentinel.yml`:

```python
from tgsentinel.config import load_config

cfg = load_config()  # Returns AppCfg dataclass
cfg.telegram_session  # Path to session file
cfg.api_id, cfg.api_hash  # Telegram credentials
cfg.alerts.mode  # "dm" | "channel" | "both"
cfg.channel_rules  # List[ChannelRule]
```

Per-channel rules (`ChannelRule` dataclass):
- `vip_senders`, `keywords`, `action_keywords`, `decision_keywords`
- `reaction_threshold`, `reply_threshold`, `rate_limit_per_hour`
- `detect_codes`, `detect_documents`, `prioritize_pinned`

## Redis Key Schema

All keys prefixed `tgsentinel:`:

```
tgsentinel:worker_status         # Sentinel auth state (TTL: 3600s)
tgsentinel:user_info             # Cached user identity
tgsentinel:auth_queue            # UI → Sentinel auth requests
tgsentinel:request:get_dialogs:{id}   # Dialog fetch requests
tgsentinel:response:get_dialogs:{id}  # Dialog responses
tgsentinel:jobs:{job_id}:progress     # Job state machine
tgsentinel:relogin:handshake     # Temp handshake during upload
```

**Security**: Never log raw keys, session paths, or credentials (see `AUTH.instructions.md` §4).

## Testing Guidelines

From `TESTS.instructions.md`:

- **Unit tests** (80-90%): Pure logic, no Redis/network. Files in `tests/unit/tgsentinel/` or `tests/unit/ui/`
- **Integration tests**: Real Redis, test HTTP API boundaries. Files in `tests/integration/`
- **Contract tests**: API response schemas, error formats. Files in `tests/contracts/`

**Naming**: `test_<behavior>__<condition>__<expected>()`

**Fixtures**: Centralized in `tests/conftest.py`. Use `@pytest.fixture` for shared test state.

## Logging Standards

Structured JSON logs with required fields (see `Progressbar.instructions.md`):

```python
log.info("Processing message", extra={
    "handler": "CHATS-HANDLER",
    "job_id": job_id,
    "request_id": request_id,
    "chat_id": chat_id,
    "step": "heuristic_filter"
})
```

**Never log**:
- Session file paths (`/app/data/tgsentinel.session`)
- Credentials (`API_ID`, `API_HASH`, tokens)
- Raw handshake keys or phone numbers

## Module Organization

When refactoring large files (see `Split_in_modules.instructions.md`):

1. Handlers → `tgsentinel.handlers.*` (e.g., `tgsentinel.handlers.chats`)
2. Service boundaries: UI modules in `ui/`, Sentinel in `src/tgsentinel/`
3. Preserve handler tags: `[CHATS-HANDLER]` etc.
4. Maintain asyncio entry points: `async def run_*_handler()`

## UI Development

From `UI_UX.instructions.md`:

- Structure: `ui/routes/`, `ui/services/`, `ui/api/`
- **Never** import Sentinel modules or Telethon in UI code
- All Telegram data via HTTP API to sentinel:8080
- UI DB access: `from ui.database import get_ui_db`
- Auth flow: UI submits to Redis → Sentinel processes → UI polls status

## Performance Patterns

From `Coding.instructions.md`:

1. Profile before optimizing (`cProfile`, `line_profiler`)
2. Cache expensive ops: `functools.lru_cache`
3. Use built-ins: `sum`, `min`, `any`, `itertools` (C implementations)
4. Lazy evaluation: generators over lists for large datasets
5. Batch I/O: Redis pipelining, bulk DB inserts

## Common Pitfalls

1. **Session conflicts**: Always verify UI doesn't mount `/app/data` from sentinel volume
2. **Import violations**: `grep -r "from tgsentinel" ui/` should return nothing
3. **Handler crashes**: All handlers need graceful shutdown via `asyncio.Event`
4. **Redis key leaks**: Always set TTLs on temporary keys
5. **Test isolation**: Use dedicated Redis DB or mock for unit tests

## Quick Reference

| Task | Command/Path |
|------|-------------|
| Run sentinel locally | `docker compose up sentinel` |
| Check Redis state | `docker exec -it tgsentinel-redis-1 redis-cli` |
| View handler status | `curl http://localhost:8080/api/status` |
| UI database schema | `ui/database.py` (migration logic) |
| Config validation | `tools/verify_config_ui.py` |
| Generate test session | `tools/generate_session.py` |

## Documentation Index

Core architecture docs in `docs/`:
- `DUAL_DB_ARCHITECTURE.md` — Volume/DB separation details
- `ENGINEERING_GUIDELINES.md` — Full component specs
- `ARCHITECTURE_COMPLIANCE.md` — Validation checklist

Instruction files in `.github/instructions/`:
- `DB_Architecture.instructions.md` — Database ownership rules
- `AUTH.instructions.md` — Session validation workflow
- `Concurrency.instructions.md` — Handler lifecycle patterns
- `TESTS.instructions.md` — Test taxonomy and structure
- `UI_UX.instructions.md` — UI layer conventions

When in doubt, these documents are the authoritative source. Always validate changes against `AUTH.instructions.md` after modifying session/auth code.
