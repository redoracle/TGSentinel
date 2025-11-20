# Infrastructure Tests

This directory contains tests that **require running infrastructure** (Redis, Docker, HTTP services) and will fail if the application is not running.

## Test Organization

### `/redis/` - Redis-Dependent Tests

Tests that require a **real, running Redis instance** (not mocked):

- `test_dashboard_data.py` - Dashboard data pipeline (Redis → API → Frontend)
  - Connects to `redis.Redis(host="localhost", port=6379, db=15)`
  - Tests full data flow with real Redis operations

These tests will fail with `redis.exceptions.ConnectionError` if Redis is not running on localhost:6379.

### `/services/` - HTTP/Service-Dependent Tests

**Currently empty.** All tests that were here have been moved to `tests/integration/` because they use mocked dependencies.

### `/docker/` - Docker/Subprocess-Dependent Tests

**Currently empty.** All tests that were here have been moved to `tests/integration/` because subprocess calls are mocked.

## Running Infrastructure Tests

### Prerequisites

Ensure the following are running:

```bash
# Start Redis
docker compose up -d redis

# Start Sentinel and UI services
docker compose up -d sentinel ui
```

### Run All Infrastructure Tests

```bash
pytest tests/infrastructure/
```

### Run Specific Category

```bash
# Redis tests only (currently the only infrastructure tests)
pytest tests/infrastructure/redis/
```

## Key Differences from Unit/Integration Tests

| Category           | Location                | Dependencies                        | Run Without App |
| ------------------ | ----------------------- | ----------------------------------- | --------------- |
| **Unit**           | `tests/unit/`           | None (pure logic)                   | ✅ Yes          |
| **Integration**    | `tests/integration/`    | Mocked dependencies                 | ✅ Yes          |
| **Infrastructure** | `tests/infrastructure/` | Real services (Redis, HTTP, Docker) | ❌ No           |
| **Contracts**      | `tests/contracts/`      | Mocked HTTP responses               | ✅ Yes          |

## When Tests Fail

### Redis Connection Errors

```
redis.exceptions.ConnectionError: Error 61 connecting to localhost:6379. Connection refused.
```

**Solution**: Start Redis with `docker compose up -d redis`

### Service Unavailable (503)

**Note**: If you see 503 errors in tests, they are likely in `tests/integration/` (which use mocks) not in `tests/infrastructure/`. Check that you're running the correct test category.

## Best Practices

1. **Run unit tests first**: Always run `pytest tests/unit/` before infrastructure tests
2. **Check service health**: Verify services are healthy before running tests
3. **Clean state**: Reset Redis and databases between test runs if needed
4. **CI/CD**: In CI, these tests should run in a dedicated stage after services are up
5. **Local development**: Use `make test` which handles service dependencies automatically

## Test Markers

Infrastructure tests use pytest markers to indicate their dependencies:

- `@pytest.mark.integration` - Requires running services
- `@pytest.mark.e2e` - Full stack end-to-end tests
- `@pytest.mark.slow` - Tests that take longer to execute

## Future Improvements

- [ ] Add docker-compose test fixture to automatically start/stop services
- [ ] Implement health check waiting in test setup
- [ ] Add retry logic for flaky network operations
- [ ] Create separate CI job for infrastructure tests
- [ ] Document required environment variables for each test category
