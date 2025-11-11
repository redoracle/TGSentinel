# TG Sentinel Test Suite

This directory contains comprehensive unit and integration tests for the TG Sentinel application.

## Test Structure

```text
tests/
├── conftest.py              # Shared fixtures and test configuration
├── test_heuristics.py       # Tests for heuristic evaluation logic
├── test_config.py           # Tests for configuration loading
├── test_store.py            # Tests for database operations
├── test_semantic.py         # Tests for semantic scoring
├── test_client.py           # Tests for Telegram client integration
├── test_notifier.py         # Tests for notification system
├── test_digest.py           # Tests for digest generation
└── test_metrics.py          # Tests for metrics collection
```

## Running Tests

### Run all tests

```bash
pytest
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

## Fixtures

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

## Mocking Strategy

- **Telegram Client**: Mocked using AsyncMock to avoid network calls
- **Redis**: Mocked to avoid external dependencies
- **Database**: Uses in-memory SQLite for speed
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

## Best Practices

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
