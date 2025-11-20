# Quick Test Reference

## Test Categories

| Category           | Location                | Dependencies  | Pass Without App? |
| ------------------ | ----------------------- | ------------- | ----------------- |
| **Unit**           | `tests/unit/`           | None          | ✅ Yes            |
| **Integration**    | `tests/integration/`    | Mocked        | ✅ Yes            |
| **Infrastructure** | `tests/infrastructure/` | Real services | ❌ No             |
| **Contracts**      | `tests/contracts/`      | Mocked        | ✅ Yes            |

## Quick Commands

```bash
# Run tests without requiring running app
pytest tests/unit/ tests/integration/ tests/contracts/

# Run only infrastructure tests (requires services)
pytest tests/infrastructure/

# Skip infrastructure tests entirely
pytest --ignore=tests/infrastructure/

# Run specific infrastructure category
pytest tests/infrastructure/redis/      # Redis tests
pytest tests/infrastructure/services/   # Service tests
pytest tests/infrastructure/docker/     # Docker tests
```

## Test Counts

- **15 unit tests** - Pure logic (no external dependencies)
- **16 integration tests** - Mocked dependencies (Redis, HTTP, etc.)
- **3 contract tests** - API contracts with mocked responses
- **1 infrastructure test** - Requires real Redis (test_dashboard_data.py)
- **Total: 35 test files** (excl. conftest.py)

## Current Test Results

### Logic Tests (Unit) - Can Fix Without Starting App

- `tests/unit/test_config.py` - 5 failures (Redis host default)
- `tests/unit/tgsentinel/test_digest.py` - 1 failure (SQL query)
- `tests/unit/tgsentinel/test_metrics.py` - 3 failures (KeyError)

### Infrastructure Tests - Need Running Services

- `tests/infrastructure/redis/test_dashboard_data.py` - 7 tests requiring real Redis
  - Connects to `redis.Redis(host="localhost", port=6379, db=15)`
  - Tests full data pipeline: Redis → API → Frontend

## To Fix Logic Tests

```bash
# These can be fixed without starting any services
pytest tests/unit/test_config.py -v
pytest tests/unit/tgsentinel/test_digest.py -v
pytest tests/unit/tgsentinel/test_metrics.py -v
```

## To Run Infrastructure Tests

```bash
# 1. Start services
docker compose up -d redis sentinel ui

# 2. Run tests
pytest tests/infrastructure/ -v

# 3. Check specific categories
pytest tests/infrastructure/redis/ -v       # Verify Redis connection
pytest tests/infrastructure/services/ -v    # Verify HTTP APIs
```
