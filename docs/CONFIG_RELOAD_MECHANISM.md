# Configuration Reload Mechanism

## Overview

Automatic configuration reloading system that allows channels to be added via the UI without requiring container restarts.

## Architecture

### UI Container (`tgsentinel-ui-1`)

When configuration changes are made through API endpoints:

1. **Config File Update**: YAML file written atomically using `tempfile` + `shutil.move`
2. **In-Memory Reload**: `reload_config()` called to refresh Flask app's global config object
3. **Signal Creation**: Creates `/app/data/.reload_config` marker file to notify worker

### Sentinel Container (`tgsentinel-sentinel-1`)

Worker process monitors for reload signals:

1. **Periodic Check**: Every 5 seconds, checks for `/app/data/.reload_config` marker file
2. **Reload Sequence** (if marker exists):
   - Loads fresh config from YAML file
   - Rebuilds channel rules dictionary
   - Reloads semantic interests
   - Deletes marker file
   - Logs reload event with new channel count
3. **Error Handling**: Removes marker even on failure to prevent infinite retry loop

## Implementation Details

### UI Side (`ui/app.py`)

```python
def reload_config():
    """Reload configuration from disk without reinitializing DB/Redis"""
    global config
    from tgsentinel.config import load_config
    config = load_config()
```

Integrated into:

- `api_config_channels_add()`: After adding channels via "+ ADD" button
- `api_config_save()`: After any config form submission

### Worker Side (`src/tgsentinel/worker.py`)

```python
reload_marker = Path("/app/data/.reload_config")
last_cfg_check = 0
cfg_check_interval = 5  # seconds

# In main loop:
if reload_marker.exists():
    new_cfg = load_config()
    cfg = new_cfg
    rules = load_rules(cfg)
    load_interests(cfg.interests)
    reload_marker.unlink()
    log.info("Configuration reloaded successfully with %d channels", len(cfg.channels))
```

## Shared Volume

Docker Compose mounts `/app/data` as shared volume:

```yaml
volumes:
  - ./data:/app/data
```

Both containers can:

- Read/write YAML config files
- Create/detect marker files
- Access shared Telegram session files

## Testing

### Manual Test

1. Open UI at `http://localhost:5001`
2. Click "+ ADD" button in Channels Management
3. Add new channel (e.g., "Test Channel", ID: -1002222333444)
4. Check sentinel logs: `docker logs tgsentinel-sentinel-1 --tail 30`
5. Verify reload message appears:
   ```
   [INFO] Config reload requested, reloading configuration...
   [INFO] Configuration reloaded successfully with 7 channels
   [INFO]   • Test Channel (id: -1002222333444)
   ```

### Automated Test

Run test suite:

```bash
python -m pytest tests/ -v
```

All 195 tests pass including:

- 27 tests for newly implemented endpoints
- 168 existing tests (no regressions)

## Benefits

1. **Zero Downtime**: No need to restart containers when adding channels
2. **Immediate Effect**: New channels monitored within 5 seconds
3. **Shared State**: Both UI and worker stay synchronized
4. **Error Resilient**: Failed reloads don't break the system
5. **Developer Friendly**: Easy to debug via marker file presence

## Limitations

1. **Polling Interval**: 5-second delay before worker picks up changes
2. **File-Based Signaling**: Requires shared volume between containers
3. **Single Instance**: Not designed for multi-worker deployments (yet)

## Future Improvements

1. Use Redis pub/sub for instant signaling (no 5-second delay)
2. Support distributed worker pools with shared config state
3. Add configuration versioning for rollback capability
4. Implement partial reloads (e.g., only channel rules, not entire config)

## Telegram Chat ID Format

Telegram uses negative IDs to distinguish entity types:

- **Positive IDs**: Users, bots, channels (e.g., `355791041`)
- **Negative IDs**: Groups (e.g., `-100123456789`)
  - `-100xxx`: Supergroups and channels (most common)
  - `-xxx`: Legacy small groups

This is Telegram's native format, not a TGSentinel convention.
