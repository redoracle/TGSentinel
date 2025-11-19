# TG Sentinel Test Suite

This directory contains comprehensive unit, integration, contract, and end-to-end tests for the TG Sentinel application, following the guidelines in `.github/instructions/TESTS.instructions.md`.

## Test Organization & Architecture

Tests are organized according to the **Test Taxonomy** defined in TESTS.instructions.md:

### Test Categories

1. **Unit Tests (80-90% of total)** - `@pytest.mark.unit`

   - Pure Python logic, no network, no Redis, no filesystem
   - Fast execution (< 10ms per test ideal)
   - Focus on domain logic, helpers, and small services
   - Examples:
     - `test_heuristics.py` - Heuristic evaluation logic
     - `test_config.py` - Configuration loading
     - `test_store.py` - Database operations (in-memory)
     - `test_metrics.py` - Metrics collection
     - `test_semantic.py` - Semantic scoring

2. **Integration Tests** - `@pytest.mark.integration`

   - Test Sentinel and UI services with real boundaries
   - Use real Redis (dedicated test DB or ephemeral container)
   - Exercise realistic flows: Redis keys, handler loops, API endpoints
   - Examples:
     - `test_client.py` - Telegram client integration
     - `test_worker.py` - Worker process integration
     - `test_dashboard_data.py` - Dashboard data pipeline
     - `test_ui_channels.py` - UI channel management
     - `test_ui_login_endpoints.py` - Login/auth flows

3. **Contract / API Tests** - `@pytest.mark.contract`

   - Test API contracts consumed by UI and external clients
   - Assert HTTP status codes, JSON schema, error formats
   - Ensure no sensitive data leaks (session paths, tokens, credentials)
   - Examples:
     - `test_ui_endpoints.py` - UI endpoint contracts
     - `test_ui_analytics_layout.py` - Analytics API contracts
     - `test_ui_missing_endpoints.py` - Newly implemented endpoints

4. **End-to-End Tests** - `@pytest.mark.e2e`
   - Small, curated set for full stack validation
   - Bring up UI + Sentinel + Redis via docker-compose
   - Examples:
     - `test_console_e2e.py` - Console page full stack tests

### Directory Structure (Recommended for Future)

While tests are currently in the root `tests/` directory, the recommended structure for future organization is:

```text
tests/
├── unit/
│   ├── tgsentinel/     # Unit tests for src/tgsentinel/
│   └── ui/             # Unit tests for ui/
├── integration/        # Integration tests
├── contracts/          # Contract/API tests
└── e2e/               # End-to-end smoke tests
```

## Running Tests

### Run all tests

```bash
pytest
```

### Run by category (using markers)

```bash
# Unit tests only (fast, no network/Redis)
pytest -m unit

# Integration tests (requires Redis)
pytest -m integration

# Contract/API tests
pytest -m contract

# End-to-end tests
pytest -m e2e

# Exclude slow tests
pytest -m "not slow"
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

## Performance

The full test suite should run in under 5 seconds on most systems, thanks to:

- In-memory database usage
- Mocked external services
- No network I/O
- Minimal file system access
