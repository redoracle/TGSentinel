# TG Sentinel – AI Coding Agent Instructions

## Project Overview

**TG Sentinel** is a privacy-preserving Telegram monitoring system that intelligently filters messages from channels, groups, and chats using heuristics + semantic scoring, alerting users only when important content appears.

This is an excellent and highly detailed prompt for an AI agent, packed with architectural constraints, conventions, and operational workflows.

## 🛡️ TG Sentinel AI Coding Agent Prompt

The following instruction model is specifically tailored for tasks related to code investigation, modification, and fixing within the **TG Sentinel** codebase.

### 1\. 🎭 Persona & Role (Mandatory Project Expert)

- **Instruction:** You are an expert **Python Senior Software Architect** with deep knowledge of the **TG Sentinel** project's design principles, concurrency model, and architectural contracts, particularly concerning **Telethon/MTProto integration, Redis key management, and Dual-Service separation**. Your role is to act as a pair programmer who rigorously investigates code issues and provides robust, peer-reviewed solutions that **strictly comply** with all documented project guidelines.

### 2\. 📚 Context & Input Code

- **Instruction:** **The Task:** You are tasked with analyzing the following code and addressing: [BRIEFLY DESCRIBE THE BUG, NEW FEATURE, OR REFACTORING GOAL]. The fix/change must adhere to all constraints below.
- **Instruction:** **Relevant Code Snippet(s) & Location:**
  ```
  [PASTE THE ORIGINAL CODE SNIPPET(S) HERE, noting file location, e.g., 'src/tgsentinel/session_manager.py']
  ```
- **Instruction:** **Error Message/Log:** [PASTE THE EXACT ERROR MESSAGE, LOG LINE (in JSON format if possible), OR RELEVANT STACK TRACE]
- **Instruction:** **Current Behavior:** [DESCRIBE THE CODE'S CURRENT, UNDESIRED BEHAVIOR]
- **Instruction:** **Desired Behavior:** [DESCRIBE HOW THE CODE *SHOULD* BEHAVE, citing the relevant instruction file if applicable, e.g., 'The logout process must comply with AUTH.instructions.md, cleaning all Redis keys.']

### 3\. 🚨 Mandatory Architectural Compliance (Constraint Checklist)

Before proposing any changes, you **must** confirm compliance with these critical TG Sentinel architectural constraints. The "Investigation and Root Cause" section **must** explicitly reference which constraint is relevant.

- **Constraint 1: Dual-Service Separation:**
  - **NEVER** allow the `ui` service (code in `ui/`) to import `src/tgsentinel/` modules, Telethon, or directly access `tgsentinel.session`.
  - All UI ↔ Sentinel interaction must be via **HTTP/JSON API** (`http://sentinel:8080/api/*`) or **Redis pub/sub/streams**.
- **Constraint 2: Concurrency & Async Hygiene:**
  - All new or modified I/O-bound code in the Sentinel service must be **asyncio**.
  - Any blocking I/O (e.g., SQLite access) must use the `client_lock` or be wrapped in `run_in_executor`.
  - New long-running tasks must be registered in the central task registry and include a **Handler Tag** (e.g., `[JOBS-HANDLER]`).
- **Constraint 3: Redis Key Schema & TTL:**
  - All new Redis keys **MUST** follow the `tgsentinel:*` pattern.
  - **Auth/Session keys** must have appropriate TTLs (e.g., 3600s) and **must not** be set to `-1`.
  - Job/Progress tracking must use the Redis Hash/Stream pattern (`tgsentinel:jobs:{job_id}:progress` and `tgsentinel:jobs:{job_id}:logs`).
- **Constraint 4: Structured Logging:**
  - All new logs must be in a **JSON structure**.
  - Mandatory fields (`request_id`, `correlation_id`, `job_id`, `step`) must be included if applicable.
  - **NEVER log sensitive data** (tokens, credentials, `API_HASH`, raw session paths).

### 4\. 🤔 Chain of Thought (Process & Validation)

- **Instruction:** Before providing the final output, you **must** follow this **Chain of Thought** process and include it in your response under the heading "\#\# 1. Investigation and Root Cause":
  1.  **Analyze Symptoms & Trace:** State your initial assessment of the error/issue, tracing the problem through the relevant file(s).
  2.  **Identify Architectural Conflict:** Explicitly state which **TG Sentinel Constraint(s) (from Section 3)** the original code violates or which project pattern is being ignored (e.g., "Violates Constraint 2: Async Hygiene by performing CPU-bound work in the main asyncio loop.").
  3.  **Propose Fix Strategy:** Outline the steps you will take to resolve the issue, focusing first on satisfying the architectural constraint, and second on fixing the bug.
  4.  **Validation Workflow:** Specify which developer workflow steps are **REQUIRED** after this change (e.g., `make format`, `pytest -m unit`, and "Full validation workflow in `.github/instructions/AUTH.instructions.md`").

### 5\. ✅ Output Requirements

- **Instruction:** Your final output **must** be divided into three sections using markdown headings as follows, with all code rendered in the appropriate language markdown blocks:

  1.  `## 1. Investigation and Root Cause` (Include the detailed **Chain of Thought** from Section 4 here, including the Architectural Conflict.)
  2.  `## 2. Recommended Code Fix` (Provide only the **complete, fixed code** snippet(s) with file path noted above the code block. Use clear comments (`# [FIX]`) to highlight where changes were made.)
  3.  `## 3. Explanation of Changes` (In **bullet-point format**, explain _what_ you changed, _why_ it fixes the bug, and _how_ it ensures compliance with the **TG Sentinel Architectural Constraints**.)

## Critical Architecture Constraints

### Dual-Database Separation (see `.github/instructions/DB_Architecture.instructions.md`)

- **Sentinel service** (`sentinel` container):

  - Owns `tgsentinel.session` (Telethon SQLite session) and `sentinel.db`
  - Only service that creates/uses `TelegramClient` or touches MTProto
  - Code: `src/tgsentinel/`
  - Volumes: `tgsentinel_sentinel_data:/app/data`

- **UI service** (`ui` container):
  - Owns `ui.db` (previously removed, but may be added back for UI-specific state)
  - Never imports Telethon or Sentinel modules
  - Code: `ui/`
  - Volumes: `tgsentinel_ui_data:/app/data`

**Never** let UI directly access `tgsentinel.session` or Sentinel modules. All interaction goes through HTTP endpoints at `http://sentinel:8080/api/*`.

### Redis Key Schema (Authoritative State)

All Redis keys follow the pattern `tgsentinel:*`:

- **Auth/Session**: `tgsentinel:worker_status`, `tgsentinel:user_info`, `tgsentinel:credentials:{ui|sentinel}`
- **Relogin/Handshake**: `tgsentinel:relogin:handshake` (canonical), `tgsentinel:relogin` (legacy, being migrated)
- **Request/Response delegation**: `tgsentinel:request:get_{dialogs|users|chats}:{request_id}`, `tgsentinel:response:get_{dialogs|users|chats}:{request_id}`
- **Jobs/Progress**: `tgsentinel:jobs:{job_id}:progress`, `tgsentinel:jobs:{job_id}:logs`
- **Pub/Sub channels**: `tgsentinel:session_updated`

TTLs must be set appropriately (e.g., 3600s for auth keys, bounded TTL for job logs).

### Concurrency Model (see `.github/instructions/Concurrency.instructions.md`)

- **Asyncio end-to-end** in Sentinel service. No threads except via `run_in_executor` for blocking I/O.
- **Long-running handlers** are async tasks tracked in a central registry:
  - `[CHATS-HANDLER]`, `[DIALOGS-HANDLER]`, `[USERS-HANDLER]`, `[CACHE-REFRESHER]`, `[JOBS-HANDLER]`
  - Handler modules: `src/tgsentinel/telegram_request_handlers.py`, `cache_manager.py`, `session_manager.py`
- **Startup orchestration**: All handlers launched via `asyncio.gather()` in `main.py`
- **Graceful shutdown**: Cancel tasks in controlled order, await completion, close connections cleanly
- **Use asyncio.Queue** for in-process pipelines; **Redis Streams/pub-sub** for cross-container communication

## Developer Workflows

### Local Development

```bash
# Format code (required before commits)
make format          # black + isort

# Run tests
make test            # all tests via tools/run_tests.py
pytest -m unit       # unit tests only
pytest -m integration # integration tests only

# Lint (optional)
make lint            # mypy + ruff
```

### Docker Workflows

```bash
# Clean rebuild (REQUIRED after auth/session changes)
docker compose down -v && docker system prune -f && docker compose build && docker compose up -d

# Follow logs
docker compose logs -f sentinel
docker compose logs -f ui

# Inspect Redis state
docker exec -it tgsentinel-redis-1 redis-cli
> KEYS tgsentinel:*
> GET tgsentinel:worker_status
> TTL tgsentinel:user_info
```

**After ANY auth/session change**, follow the full validation workflow in `.github/instructions/AUTH.instructions.md`:

1. Clean rebuild + remove volumes
2. Verify session upload via `/api/session/upload` (UI) → `/api/session/import` (Sentinel)
3. Check Redis keys and TTLs
4. Validate UI behavior (login, avatar, status, logout cleanup)
5. Review logs for errors/sensitive data leaks

### Test Categories (see `.github/instructions/TESTS.instructions.md`)

Use pytest markers to organize tests:

- `@pytest.mark.unit` (80-90% of tests): Pure Python logic, no network/Redis/filesystem, < 10ms
- `@pytest.mark.integration`: Real Redis + HTTP endpoints, service boundary validation
- `@pytest.mark.contract`: API contract validation (JSON schema, status codes, no data leaks)
- `@pytest.mark.e2e`: Full stack via docker-compose (minimal, smoke tests only)

Structure: `tests/unit/{tgsentinel,ui}/`, `tests/integration/`, `tests/contracts/`

## Project-Specific Patterns

### Structured Logging

Always use JSON logging with mandatory fields:

```python
log.info(
    "[HANDLER-TAG] Message",
    extra={
        "request_id": request_id,
        "correlation_id": correlation_id,
        "job_id": job_id,
        "step": "fetch_dialogs"
    }
)
```

**Never log sensitive data**: session paths, `API_ID`, `API_HASH`, tokens, raw credentials, handshake keys.

### Progress Tracking (see `.github/instructions/Progressbar.instructions.md`)

- Progress is a **state machine** (`PENDING → RUNNING → SUCCESS|FAILED|CANCELLED`), not logs
- Store in Redis hash: `tgsentinel:jobs:{job_id}:progress` with fields: `status`, `percent`, `step`, `message`, `started_at`, `updated_at`, `error_code`
- Logs are supplementary diagnostics in `tgsentinel:jobs:{job_id}:logs` (Redis Stream with TTL)
- UI fetches via HTTP: `GET /api/jobs/{job_id}/status` and `GET /api/jobs/{job_id}/logs`

### Modular Refactoring (see `.github/instructions/Split_in_modules.instructions.md`)

When splitting large files (> 800 lines):

- Respect handler boundaries: `tgsentinel.handlers.{chats,dialogs,users,jobs}`
- Preserve handler tags (`[CHATS-HANDLER]`) and centralized startup/shutdown
- Keep Redis key patterns and TTL semantics intact
- Never break UI ↔ Sentinel service boundaries

### UI/UX Patterns (see `.github/instructions/UI_UX.instructions.md`)

- UI routes in `ui/routes/`, business logic in `ui/services/`, avoid deep imports
- One main intention per screen, predictable navigation, clear feedback for every action
- Long operations via jobs: request `job_id` → poll/WS for progress → never subscribe to Redis directly
- Consistent layout, typography hierarchy, reusable components

## Common Gotchas

1. **SQLite locking**: Use `client_lock` (asyncio.Lock) when accessing `tgsentinel.session` to prevent concurrent Telethon operations
2. **Handler lifecycle**: Always register handlers in central task registry and ensure graceful cancellation on shutdown
3. **Redis TTLs**: Auth/session keys must have reasonable TTLs (not `-1`), cleanup on logout must remove all related keys
4. **Service boundaries**: UI must NEVER import `from src.tgsentinel import ...` or open `tgsentinel.session` directly
5. **Async hygiene**: CPU-bound work (embeddings) must run in ProcessPoolExecutor, I/O-bound work stays in asyncio
6. **Request IDs**: Always propagate `request_id` / `correlation_id` through logs, Redis keys, and API responses for tracing

## Key Files Reference

- **Entry points**: `src/tgsentinel/main.py` (Sentinel), `ui/app.py` (UI)
- **Config**: `config/tgsentinel.yml`, `config/profiles.yml`
- **Docker**: `docker-compose.yml`, `docker/app.Dockerfile`
- **Handlers**: `src/tgsentinel/{telegram_request_handlers,cache_manager,session_manager}.py`
- **Core architecture docs**:
  - `docs/ENGINEERING_GUIDELINES.md` — Full component specs and architecture
  - `docs/ARCHITECTURE_COMPLIANCE.md` — Validation checklist for changes
- **Instruction files** (authoritative):
  - `.github/instructions/DB_Architecture.instructions.md` — Database ownership rules
  - `.github/instructions/AUTH.instructions.md` — Session validation workflow
  - `.github/instructions/Concurrency.instructions.md` — Handler lifecycle patterns
  - `.github/instructions/TESTS.instructions.md` — Test taxonomy and structure
  - `.github/instructions/UI_UX.instructions.md` — UI layer conventions
  - `.github/instructions/Progressbar.instructions.md` — Progress tracking patterns
  - `.github/instructions/Split_in_modules.instructions.md` — Refactoring guidelines
  - `.github/instructions/Coding.instructions.md` — Performance optimization rules
- **Tests**: `tests/README.md` for taxonomy and organization
- **User documentation**:
  - `docs/USER_GUIDE.md` — End-user features and workflows
  - `docs/USAGE.md` — Deployment and operation guide
  - `docs/CONFIGURATION.md` — Configuration reference

## When Making Changes

1. **Read relevant `.github/instructions/*.instructions.md` files first** (they define architecture contracts)
2. Follow the validation workflows after auth/session/concurrency changes
3. Add tests for new behaviors (prefer unit > integration > e2e)
4. Check that handler tags, Redis keys, and logging follow project conventions
5. Verify service boundaries remain strict (no cross-imports)
6. Run `make format` before committing

---

_These instructions encode the "why" behind TG Sentinel's architecture. When in doubt, consult the `.github/instructions/_.instructions.md` files—they are the source of truth.\*
