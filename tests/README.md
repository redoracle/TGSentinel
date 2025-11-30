# TG Sentinel Test Suite

This directory contains comprehensive unit, integration, contract, and end-to-end tests for the TG Sentinel application, following the guidelines in `.github/instructions/TESTS.instructions.md`.

## Test Organization & Architecture

Tests are organized according to the **Test Taxonomy** defined in TESTS.instructions.md:

### Directory Structure

```
tests/
├── unit/                           # Unit tests (80-90% of total)
│   ├── tgsentinel/                # Sentinel service unit tests
│   │   ├── test_heuristics.py
│   │   ├── test_semantic.py
│   │   ├── test_store.py
│   │   ├── test_notifier.py
│   │   ├── test_metrics.py
│   │   ├── test_digest.py
│   │   ├── test_profiles.py
│   │   ├── test_digest_formatting.py
│   │   ├── test_profile_validation.py
│   │   └── test_migration.py
│   ├── ui/                        # UI service unit tests
│   │   ├── test_dashboard_controls.py
│   │   └── test_global_profile_api.py
│   ├── test_config.py             # Cross-service config tests
│   ├── test_config_priority.py
│   └── test_ui_config_extras.py
├── integration/                    # Integration tests (mocked dependencies)
│   ├── test_profile_binding_workflow.py
│   └── test_profiles_e2e.py
├── infrastructure/                 # Tests requiring running services (real, not mocked)
│   ├── README.md                  # Infrastructure test documentation
│   └── redis/                     # Redis-dependent tests
│       └── test_dashboard_data.py # Only test requiring real Redis
├── contracts/                      # API contract tests
├── failing/                        # Tests that need fixing
│   ├── README.md                  # Details on failure reasons
│   ├── test_app_integration.py    # Config structure issues
│   ├── test_client.py             # AppCfg redis parameter
│   ├── test_worker.py             # AppCfg redis parameter
│   ├── test_ui_*.py               # Missing endpoints/dependencies
│   ├── test_developer_webhooks.py # Missing endpoints
│   ├── test_identity_caching.py   # Config structure
│   └── test_telegram_users_api.py # Config structure
├── conftest.py                     # Shared fixtures
└── README.md                       # This file
```

### Test Categories

1. **Unit Tests (80-90% of total)** - `@pytest.mark.unit`

   - Pure Python logic, no network, no Redis, no filesystem
   - Fast execution (< 10ms per test ideal)
   - Focus on domain logic, helpers, and small services
   - **Location**: `unit/tgsentinel/` or `unit/ui/`
   - **Passing examples**:
     - `unit/tgsentinel/test_heuristics.py` - Heuristic evaluation logic
     - `unit/test_config.py` - Configuration loading
     - `unit/tgsentinel/test_store.py` - Database operations (in-memory)
     - `unit/tgsentinel/test_metrics.py` - Metrics collection
     - `unit/tgsentinel/test_semantic.py` - Semantic scoring
     - `unit/tgsentinel/test_profiles.py` - Profile resolution
     - `unit/ui/test_dashboard_controls.py` - UI control logic

2. **Integration Tests** - `@pytest.mark.integration`

   - Test Sentinel and UI services with **mocked dependencies**
   - No real Redis, Docker, or HTTP connections required
   - Exercise realistic flows using mocks and in-memory state
   - **Location**: `integration/`
   - **Passing examples**:
     - `integration/test_profile_binding_workflow.py` - Profile system integration
     - `integration/test_profiles_e2e.py` - End-to-end profile flows

3. **Infrastructure Tests** - Require Running Services

   - Tests that **require real infrastructure** (not mocked)
   - Will fail if services are not running
   - **Location**: `infrastructure/`
   - **Currently contains**: Only 1 test file that needs real Redis
     - `infrastructure/redis/test_dashboard_data.py` - Full data pipeline test
       - Connects to real Redis instance (localhost:6379 db=15)
       - Tests Redis → API → Frontend data flow
   - **See**: `infrastructure/README.md` for details on running these tests

4. **Contract / API Tests** - `@pytest.mark.contract`

   - Test API contracts consumed by UI and external clients
   - Assert HTTP status codes, JSON schema, error formats
   - Ensure no sensitive data leaks (session paths, tokens, credentials)
   - **Location**: `contracts/`

### Test Organization Summary

The test suite is organized by test type and service dependency:

```text
tests/
├── unit/               # Pure logic tests (80-90% of total)
│   ├── tgsentinel/    # Sentinel service unit tests
│   └── ui/            # UI service unit tests
├── integration/        # Integration tests with mocked dependencies
├── infrastructure/     # Tests requiring running services (Redis, HTTP, Docker)
│   ├── redis/         # Redis-dependent tests
│   ├── services/      # HTTP/Service-dependent tests
│   └── docker/        # Docker/subprocess tests
├── contracts/          # API contract tests
└── failing/           # Tests being fixed (legacy)
```

## Running Tests

### Run all tests (including infrastructure tests)

```bash
pytest
```

**Note**: This will fail for infrastructure tests if services are not running.

### Run by test type

```bash
# Unit tests only (fast, no dependencies)
pytest tests/unit/

# Integration tests (mocked dependencies)
pytest tests/integration/

# Infrastructure tests (requires running services)
pytest tests/infrastructure/

# Contract/API tests
pytest tests/contracts/
```

### Run by category (using markers)

```bash
# Unit tests only
pytest -m unit

# Integration tests
pytest -m integration

# Contract/API tests
pytest -m contract

# End-to-end tests
pytest -m e2e

# Exclude slow tests
pytest -m "not slow"
```

### Run specific infrastructure test categories

```bash
# Redis-dependent tests only
pytest tests/infrastructure/redis/

# Service-dependent tests only
pytest tests/infrastructure/services/

# Docker-dependent tests only
pytest tests/infrastructure/docker/
```

### Run specific test file

```bash
pytest tests/test_heuristics.py
```

### Run specific test class

```bash
pytest tests/test_heuristics.py::TestRunHeuristics
```

### Run specific test

```bash
pytest tests/test_heuristics.py::TestRunHeuristics::test_mentioned_triggers_importance
```

### Run with verbose output

```bash
pytest -v
```

### Run with coverage

```bash
pytest --cov=tgsentinel --cov-report=html
```

## Test Design Principles

Following TESTS.instructions.md, all tests must adhere to:

### 1. Dependency Injection

- No global Redis/HTTP clients inside business logic
- Inject dependencies via function parameters, constructors, or framework DI

### 2. Thin I/O, Thick Core

- API/route handlers: parse → call domain service → format output
- Business rules in plain Python services (no direct I/O)

### 3. No Hidden Globals/Singletons

- Configuration via env or explicit settings objects
- Override-able in tests

### 4. Prefer Pure Functions

- Deterministic input → deterministic output
- Avoid side effects → fewer fixtures, minimal mocking

## Fixtures

Centralized fixtures are in `tests/conftest.py`:

- `in_memory_db` - In-memory SQLite database for testing
- `temp_config_file` - Temporary YAML config file
- `test_env_vars` - Clean environment variables for tests
- `client` - Flask test client
- `mock_init` - Mock initialization for UI tests

## Test Coverage

The test suite covers:

- **Heuristics Module**: All scoring logic, keyword matching, VIP detection, reaction/reply thresholds
- **Config Module**: Configuration loading, environment variable handling, defaults
- **Store Module**: Database initialization, message upsert, alert marking
- **Semantic Module**: Embedding model loading, text scoring, cosine similarity
- **Client Module**: Telegram client creation, message ingestion, event handling
- **Notifier Module**: DM and channel notifications, message formatting
- **Digest Module**: Digest generation, time filtering, score ordering
- **Metrics Module**: Counter increments, label handling, metric dumping
- **UI Endpoints**: All UI routes and API endpoints
- **Worker Module**: Message processing loop, handler coordination

## Architecture Compliance

Tests enforce architectural boundaries defined in:

- `DB_Architecture.instructions.md` - Dual-database separation (UI vs Sentinel)
- `AUTH.instructions.md` - Session handling and authentication flows
- `Concurrency.instructions.md` - Handler lifecycle and async patterns
- `UI_UX.instructions.md` - UI layer conventions

### Key Rules

1. **UI tests must NOT import Sentinel modules** (Telethon, session_manager, etc.)
2. **Sentinel tests must NOT import UI modules**
3. **Integration tests must use Redis patterns** correctly (key naming, TTLs, handshakes)
4. **No sensitive data in responses** (session paths, tokens, credentials)
5. **Structured logging** with handler tags and required fields

## Best Practices

### Mocking Strategy

- **Mock what you don't own**: External APIs, SMTP, storage
- **Don't mock Redis** in integration tests - use real Redis or in-memory fake
- **Focus on behavior**, not implementation details
- Use `pytest-mocker` or `unittest.mock`

## Performance & Reliability

- **Unit tests**: < 10ms ideal, no network, no sleeps
- **Integration tests**: Reserved for critical flows
- **Coverage target**: 80-90% on core modules
- **Flakiness control**: Deterministic seeds, frozen time for time-sensitive logic

## CI Integration

Tests run in CI with multiple stages:

1. **Stage 1**: `pytest -m unit` (fast feedback)
2. **Stage 2**: `pytest -m "integration or contract"` (with Redis)
3. **Stage 3**: `pytest -m e2e` (optional, full stack)

## Adding New Tests

When adding new tests:

1. Choose the appropriate category (unit/integration/contract/e2e)
2. Add the corresponding `@pytest.mark.<category>` marker
3. Follow naming convention: `test_<behavior>__<condition>__<expected>()`
4. Respect architectural boundaries (UI vs Sentinel)
5. Use existing fixtures from `conftest.py` when possible
6. Ensure tests are independent (no execution order dependencies)

## Example Test Structure

```python
import pytest

@pytest.mark.unit
class TestMyFeature:
    """Test my feature functionality."""

    def test_feature_basic_case(self):
        """Test basic functionality."""
        result = my_function("input")
        assert result == "expected"

    def test_feature_edge_case__empty_input__returns_default(self):
        """Test edge case with descriptive name."""
        result = my_function("")
        assert result == "default"
```

## Related Documentation

- `.github/instructions/TESTS.instructions.md` - Comprehensive test guidelines
- `.github/instructions/DB_Architecture.instructions.md` - Database separation
- `.github/instructions/AUTH.instructions.md` - Authentication test requirements
- `.github/instructions/Concurrency.instructions.md` - Handler testing patterns

### Available Fixtures (from conftest.py)

- `temp_dir`: Temporary directory for test files
- `temp_config_file`: Pre-configured test YAML config
- `test_env_vars`: Mock environment variables
- `mock_redis`: Mock Redis client
- `mock_telegram_client`: Mock Telegram client
- `in_memory_db`: In-memory SQLite database
- `sample_message_payload`: Sample message data
- `sample_telegram_message`: Mock Telegram message object
- `sample_telegram_event`: Mock Telegram event object

## Test Markers

Tests are marked with the following markers:

- `@pytest.mark.unit`: Unit tests (fast, isolated)
- `@pytest.mark.integration`: Integration tests (slower, multiple components)
- `@pytest.mark.slow`: Slow-running tests
- `@pytest.mark.asyncio`: Async tests

## Writing New Tests

### Example Unit Test

```python
def test_my_function():
    \"\"\"Test description.\"\"\"
    result = my_function(input_value)
    assert result == expected_value
```

### Example Async Test

```python
@pytest.mark.asyncio
async def test_async_function(mock_telegram_client):
    \"\"\"Test async function.\"\"\"
    await async_function(mock_telegram_client)
    mock_telegram_client.send_message.assert_called_once()
```

### Example Test with Fixtures

```python
def test_with_database(in_memory_db):
    \"\"\"Test database operation.\"\"\"
    upsert_message(in_memory_db, -100123, 1, "hash", 1.0)
    # Verify the operation
```

## Legacy Mocking Patterns

Note: These patterns are from before the test reorganization. New tests should follow the guidelines above.

- **Telegram Client**: Mocked using AsyncMock to avoid network calls
- **Redis**: Mocked in unit tests, real Redis in integration tests
- **Database**: Uses in-memory SQLite for unit tests
- **Embeddings**: Mocked to avoid downloading models
- **Time**: Use datetime mocking for time-dependent tests

## Continuous Integration

These tests are designed to run in CI/CD pipelines without external dependencies:

- No Redis server required (mocked)
- No Telegram API required (mocked)
- No embedding models required (optional, mocked)
- All tests use in-memory database

## Troubleshooting

### Import Errors

If you see import errors, ensure the Python path includes the src directory:

```bash
export PYTHONPATH="${PYTHONPATH}:/path/to/TGSentinel/src"
```

Or install the package in development mode:

```bash
pip install -e .
```

### Async Test Failures

Ensure `pytest-asyncio` is installed:

```bash
pip install pytest-asyncio
```

### Database Lock Errors

If you encounter SQLite lock errors, ensure tests properly close database connections.

## Testing Best Practices Summary

1. **Keep tests isolated**: Each test should be independent
2. **Use descriptive names**: Test names should describe what they test
3. **Test edge cases**: Include tests for empty inputs, None values, errors
4. **Mock external dependencies**: Don't make real network calls or file I/O
5. **Use fixtures**: Share common setup code via fixtures
6. **Assert explicitly**: Use clear, specific assertions
7. **Document tests**: Include docstrings explaining what is tested

## Current Test Status

**Last Run**: 2025-11-20  
**Total Tests**: 450  
**Passing**: 292 (65%)  
**Failing**: 109 (24%)  
**Errors**: 49 (11%)

### Failing Tests - Common Issues

All failing tests have been moved to `failing/` directory. See `failing/README.md` for details.

**Primary failure categories**:

1. **Config structure changes** (60+ tests):

   - `AppCfg.__init__() got an unexpected keyword argument 'redis'`
   - Tests need fixtures updated to match new RedisConfig dataclass structure

2. **Missing dependencies** (30+ tests):

   - `flask_limiter` not installed - blueprint import failures
   - Need to make optional or install

3. **Missing endpoints** (15+ tests):

   - Developer panel endpoints returning 404
   - Webhook management endpoints not implemented
   - Profile import endpoint returning 503

4. **Sentinel connectivity** (10+ tests):
   - Tests expecting real Sentinel service at `http://sentinel:8080`
   - Need proper mocking or mark as integration/e2e tests

## Running Tests (Updated)

### All Passing Tests

```bash
# Run all passing tests (excludes failing/ directory)
make test

# Or directly with pytest
pytest --ignore=failing/

# With coverage
pytest --cov=src/tgsentinel --cov=ui --ignore=failing/
```

### By Category

```bash
# Unit tests only
pytest -m unit --ignore=failing/

# Integration tests only
pytest -m integration --ignore=failing/

# Contract tests only
pytest -m contract --ignore=failing/

# E2E tests only
pytest -m e2e --ignore=failing/
```

### Specific Test Files

```bash
# Sentinel unit tests
pytest unit/tgsentinel/test_heuristics.py
pytest unit/tgsentinel/test_semantic.py

# UI unit tests
pytest unit/ui/test_dashboard_controls.py

# Integration tests
pytest integration/test_profiles_e2e.py
```

### Failing Tests (for debugging)

```bash
# Run specific failing test
pytest failing/test_worker.py -v

# Run all failing tests (expect failures)
pytest failing/ -v

# See detailed failure reasons
cat failing/README.md
```

## Fixing Failing Tests

### Priority Order

1. **Config structure** (affects most tests):

   - Update `_make_cfg()` helpers in test files
   - Change `redis={}` to `redis=RedisConfig(...)`
   - Update all AppCfg instantiations

2. **Missing dependencies**:

   - Install `flask-limiter` or make optional in blueprints
   - Update requirements.txt

3. **Missing endpoints**:

   - Implement endpoints or mark tests as `@pytest.mark.skip(reason="Not implemented")`
   - Move to appropriate category (may be integration, not unit)

4. **Connectivity issues**:
   - Add proper mocking for Sentinel API calls
   - Or move to integration/ with docker-compose setup

### Example Fix (Config Structure)

**Before** (failing):

```python
def _make_cfg():
    return AppCfg(
        telegram_session="sess",
        api_id=123,
        api_hash="hash",
        redis={"host": "localhost", "port": 6379},  # ❌ Wrong
        # ...
    )
```

**After** (passing):

```python
from tgsentinel.config import RedisConfig

def _make_cfg():
    return AppCfg(
        telegram_session="sess",
        api_id=123,
        api_hash="hash",
        redis=RedisConfig(host="localhost", port=6379),  # ✅ Correct
        # ...
    )
```

## CI/CD Integration

### GitHub Actions Workflow

```yaml
- name: Run Tests
  run: |
    # Only run passing tests in CI
    pytest --ignore=failing/ -v

- name: Check Test Coverage
  run: |
    pytest --cov=src/tgsentinel --cov=ui --ignore=failing/ --cov-report=xml
```

### Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

# Run unit tests (fast)
pytest -m unit --ignore=failing/ -x

if [ $? -ne 0 ]; then
    echo "Unit tests failed. Commit aborted."
    exit 1
fi
```

## Test Maintenance

### Monthly Tasks

1. Review `failing/` directory
2. Fix high-priority failing tests
3. Update fixtures for new config changes
4. Add tests for new features
5. Remove obsolete tests

### When Refactoring

1. Run affected tests first
2. Update tests alongside code
3. Maintain test/code ratio (aim for 1:1 or higher)
4. Don't skip failing tests without documenting why

## Performance

The full test suite should run in under 10 seconds on most systems (passing tests only), thanks to:

- In-memory database usage
- Mocked external services
- No network I/O
- Minimal file system access

**Note**: Current full suite (including failing tests) runs in ~10 seconds. With all tests fixed, target is < 5 seconds.

---

**Test Suite Health**: 65% passing (target: 95%+)
