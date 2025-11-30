# API Key Revocation Refactor

## Problem

The original implementation used a global Redis set with a single TTL:

```python
redis_client.sadd("tgsentinel:revoked_api_keys", key_hash)
redis_client.expire("tgsentinel:revoked_api_keys", 30 * 24 * 3600)
```

**Issue**: Calling `redis_client.expire()` on the set resets the TTL for **ALL** revoked keys, not just the newly added one. This means:

- Older revoked keys get their expiration extended every time a new key is revoked
- Keys that should have expired after 30 days remain revoked indefinitely if new keys keep getting added
- No per-key expiration tracking

## Solution

Changed to per-hash keys with individual TTLs:

### 1. Revocation (creates individual keys)

```python
# Create individual Redis key for this revoked hash with 30-day TTL
revoked_key = f"tgsentinel:revoked_api_key:{key_hash}"
ttl_seconds = 30 * 24 * 3600  # 30 days

# Set the key with value "1" and TTL
redis_client.setex(revoked_key, ttl_seconds, "1")

# Optionally maintain a set for listing (without relying on its TTL)
redis_client.sadd("tgsentinel:revoked_api_keys", key_hash)
```

### 2. Validation (checks individual key existence)

```python
def is_api_key_revoked(api_key: str) -> bool:
    """Check if an API key has been revoked."""
    if not redis_client:
        return False

    try:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        revoked_key = f"tgsentinel:revoked_api_key:{key_hash}"
        # Check if the per-hash revocation key exists
        return bool(redis_client.exists(revoked_key))
    except Exception as exc:
        logger.error(f"Failed to check API key revocation status: {exc}")
        return False
```

## Benefits

1. **Individual Expiration**: Each revoked key expires exactly 30 days after revocation
2. **Scalability**: No global TTL reset affecting all keys
3. **Simplicity**: Single `redis_client.exists()` call to check revocation
4. **Optional Listing**: Set maintained for querying all revoked keys (if needed)
5. **Future-Proof**: Validation function ready for authentication middleware

## Redis Key Schema

### Per-Hash Keys (Primary)

- **Pattern**: `tgsentinel:revoked_api_key:{key_hash}`
- **Value**: `"1"`
- **TTL**: 2592000 seconds (30 days)
- **Purpose**: Individual revocation with automatic expiration

### Set (Optional, for Listing)

- **Key**: `tgsentinel:revoked_api_keys`
- **Type**: SET
- **Members**: Key hashes (without prefix)
- **TTL**: None (or managed separately)
- **Purpose**: Query all revoked keys if needed

## Migration Notes

- Old revoked keys in the set will remain but won't affect validation
- New revocations use per-hash keys immediately
- No data migration required (old keys will naturally expire)

## Testing

```bash
# Revoke a key
curl -X POST http://localhost:5001/api/api-keys/revoke \
  -H "Content-Type: application/json" \
  -d '{"api_key": "test_key_123"}'

# Check Redis (inside container)
docker exec tgsentinel-redis-1 redis-cli
> KEYS tgsentinel:revoked_api_key:*
> TTL tgsentinel:revoked_api_key:{hash}
> EXISTS tgsentinel:revoked_api_key:{hash}
```

## Files Changed

- `ui/api/developer_routes.py`:
  - Added `is_api_key_revoked()` function (lines 815-833)
  - Updated `api_key_revoke()` to use per-hash keys with `setex()` (lines 836-886)
  - Removed global set TTL approach
- `docs/DEVELOPER_PANEL_AUDIT_REPORT.md`:
  - Updated documentation to reflect new implementation (lines 209-223)

## Next Steps

To fully integrate API key authentication:

1. Create authentication middleware/decorator
2. Add `is_api_key_revoked()` check to middleware
3. Protect endpoints with API key requirement
4. Add API key validation to request headers (e.g., `X-API-Key`)

Example middleware:

```python
from functools import wraps

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return jsonify({"error": "API key required"}), 401

        if is_api_key_revoked(api_key):
            return jsonify({"error": "API key has been revoked"}), 403

        # Additional validation (check against stored hash)
        # ...

        return f(*args, **kwargs)
    return decorated_function
```
