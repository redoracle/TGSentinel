#!/usr/bin/env python3
"""
Check current Telegram rate limit status.

Usage:
    python tools/check_rate_limit.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import redis
except ImportError:
    print("ERROR: redis package not installed. Run: pip install redis")
    sys.exit(1)


def format_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def main():
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))

    print(f"Connecting to Redis at {redis_host}:{redis_port}...")

    try:
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        r.ping()
        print("✓ Connected to Redis\n")
    except Exception as e:
        print(f"✗ Failed to connect to Redis: {e}")
        sys.exit(1)

    # Check worker status
    print("=" * 60)
    print("WORKER STATUS")
    print("=" * 60)

    try:
        worker_status_raw = r.get("tgsentinel:worker_status")
        if worker_status_raw:
            worker_status = json.loads(str(worker_status_raw))
            print(f"Status:     {worker_status.get('status', 'unknown')}")
            print(f"Authorized: {worker_status.get('authorized', False)}")
            print(f"Timestamp:  {worker_status.get('ts', 'N/A')}")

            if worker_status.get("status") == "rate_limited":
                print("\n⚠️  RATE LIMITED ⚠️")
                print(
                    f"Action:      {worker_status.get('rate_limit_action', 'unknown')}"
                )
                wait_seconds = worker_status.get("rate_limit_wait", 0)
                print(
                    f"Wait Time:   {format_duration(wait_seconds)} ({wait_seconds} seconds)"
                )

                wait_until = worker_status.get("rate_limit_until")
                if wait_until:
                    try:
                        until_dt = datetime.fromisoformat(
                            wait_until.replace("Z", "+00:00")
                        )
                        now = datetime.now(timezone.utc)
                        remaining = (until_dt - now).total_seconds()
                        if remaining > 0:
                            print(f"Time Left:   {format_duration(int(remaining))}")
                            print(
                                f"Expires At:  {until_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                            )
                        else:
                            print("✓ Rate limit has expired")
                    except Exception as e:
                        print(f"Error parsing expiration time: {e}")
        else:
            print("No worker status found in Redis")
    except Exception as e:
        print(f"Error reading worker status: {e}")

    # Check for specific rate limit keys
    print("\n" + "=" * 60)
    print("ACTIVE RATE LIMITS")
    print("=" * 60)

    found_limits = False
    for key in r.scan_iter("tgsentinel:rate_limit:*"):
        found_limits = True
        ttl_result = r.ttl(key)
        ttl = ttl_result if ttl_result is not None and ttl_result != -1 else 0
        if isinstance(ttl, int) and ttl > 0:
            action = key.replace("tgsentinel:rate_limit:", "")
            print(f"\n{action}")
            print(f"  Time remaining: {format_duration(ttl)} ({ttl} seconds)")
            print(
                f"  Expires in:     {datetime.now(timezone.utc).timestamp() + ttl:.0f} (unix timestamp)"
            )

    if not found_limits:
        print("✓ No active rate limits")

    # Check auth queue
    print("\n" + "=" * 60)
    print("AUTH QUEUE STATUS")
    print("=" * 60)

    try:
        queue_length_result = r.llen("tgsentinel:auth_queue")
        if queue_length_result is None:
            queue_length = 0
        else:
            # Ensure we have an int result from synchronous Redis client
            if isinstance(queue_length_result, int):
                queue_length = queue_length_result
            else:
                # Fallback for unexpected types
                queue_length = 0
        print(f"Pending auth requests: {queue_length}")

        if queue_length > 0:
            print("\n⚠️  Warning: There are pending auth requests in the queue")
            print("   These will be processed when the sentinel starts")
    except Exception as e:
        print(f"Error reading auth queue: {e}")

    print("\n" + "=" * 60)
    print("\nTo clear rate limits (emergency use only):")
    print(
        "  docker exec tgsentinel-redis-1 redis-cli DEL tgsentinel:rate_limit:send_code"
    )
    print(
        "  docker exec tgsentinel-redis-1 redis-cli DEL tgsentinel:rate_limit:resend_code"
    )
    print("\nTo clear auth queue:")
    print("  docker exec tgsentinel-redis-1 redis-cli DEL tgsentinel:auth_queue")
    print()


if __name__ == "__main__":
    main()
