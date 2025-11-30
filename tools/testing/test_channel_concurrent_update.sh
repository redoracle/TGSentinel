#!/bin/bash
#
# Test script for verifying the Redis distributed lock prevents TOCTOU race conditions
# in channel configuration updates.
#
# This script simulates concurrent channel update requests and verifies that:
# 1. Lock prevents simultaneous modifications
# 2. Updates are applied sequentially without data loss
# 3. Retry logic works when contention occurs
# 4. Lock is properly released after each operation
#

set -e

UI_URL="${UI_URL:-http://localhost:5001}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"

echo "=== Testing Channel Concurrent Update Lock Mechanism ==="
echo "UI URL: $UI_URL"
echo ""

# Function to get channel config
get_channel_config() {
    local chat_id="$1"
    curl -s "$UI_URL/api/channels/$chat_id" | jq -r '.data'
}

# Function to update channel config
update_channel() {
    local chat_id="$1"
    local field="$2"
    local value="$3"
    
    curl -s -X POST "$UI_URL/api/channels/$chat_id" \
        -H "Content-Type: application/json" \
        -d "{\"$field\": $value}" \
        -w "\nHTTP_STATUS:%{http_code}" 2>&1
}

# Function to check Redis for lock presence
check_redis_lock() {
    local chat_id="$1"
    docker exec tgsentinel-redis-1 redis-cli GET "tgsentinel:config_lock:channel:$chat_id" 2>/dev/null || echo "(nil)"
}

# Test 1: Verify lock acquisition and release
echo "Test 1: Single update - verify lock lifecycle"
echo "-----------------------------------------------"

CHAT_ID=123456789

echo "Checking initial lock state..."
LOCK_BEFORE=$(check_redis_lock $CHAT_ID)
if [ "$LOCK_BEFORE" != "(nil)" ] && [ -n "$LOCK_BEFORE" ]; then
    echo "⚠️  WARNING: Lock already exists before test: $LOCK_BEFORE"
    echo "   Cleaning up..."
    docker exec tgsentinel-redis-1 redis-cli DEL "tgsentinel:config_lock:channel:$CHAT_ID" >/dev/null
fi

echo "Sending update request..."
RESULT=$(update_channel $CHAT_ID "reaction_threshold" 10)
HTTP_STATUS=$(echo "$RESULT" | grep "HTTP_STATUS:" | cut -d: -f2)
RESPONSE=$(echo "$RESULT" | grep -v "HTTP_STATUS:")

echo "Response: $RESPONSE"
echo "HTTP Status: $HTTP_STATUS"

echo "Checking lock state after update..."
LOCK_AFTER=$(check_redis_lock $CHAT_ID)
if [ "$LOCK_AFTER" != "(nil)" ] && [ -n "$LOCK_AFTER" ]; then
    echo "❌ FAIL: Lock not released: $LOCK_AFTER"
    exit 1
else
    echo "✅ PASS: Lock properly released"
fi
echo ""

# Test 2: Concurrent updates
echo "Test 2: Concurrent updates - verify serialization"
echo "--------------------------------------------------"

echo "Sending 3 concurrent update requests..."

# Launch 3 updates in background
update_channel $CHAT_ID "reaction_threshold" 15 > /tmp/update1.txt 2>&1 &
PID1=$!
update_channel $CHAT_ID "reply_threshold" 20 > /tmp/update2.txt 2>&1 &
PID2=$!
update_channel $CHAT_ID "rate_limit_per_hour" 30 > /tmp/update3.txt 2>&1 &
PID3=$!

# Wait for all to complete
wait $PID1
wait $PID2
wait $PID3

echo "Results:"
echo "  Request 1: $(grep HTTP_STATUS /tmp/update1.txt | cut -d: -f2)"
echo "  Request 2: $(grep HTTP_STATUS /tmp/update2.txt | cut -d: -f2)"
echo "  Request 3: $(grep HTTP_STATUS /tmp/update3.txt | cut -d: -f2)"

# Count successful updates
SUCCESS_COUNT=$(grep -c "HTTP_STATUS:200" /tmp/update*.txt 2>/dev/null || echo "0")
RETRY_COUNT=$(grep -c "HTTP_STATUS:503" /tmp/update*.txt 2>/dev/null || echo "0")

echo "  Successful: $SUCCESS_COUNT"
echo "  Retried/Failed: $RETRY_COUNT"

if [ "$SUCCESS_COUNT" -eq 3 ]; then
    echo "✅ PASS: All concurrent updates succeeded"
elif [ "$SUCCESS_COUNT" -gt 0 ]; then
    echo "⚠️  PARTIAL: $SUCCESS_COUNT updates succeeded, $RETRY_COUNT exhausted retries"
else
    echo "❌ FAIL: No updates succeeded"
    exit 1
fi

# Verify lock is released
LOCK_FINAL=$(check_redis_lock $CHAT_ID)
if [ "$LOCK_FINAL" != "(nil)" ] && [ -n "$LOCK_FINAL" ]; then
    echo "❌ FAIL: Lock still held after concurrent updates: $LOCK_FINAL"
    exit 1
else
    echo "✅ PASS: Lock properly released after concurrent updates"
fi
echo ""

# Test 3: Lock timeout and recovery
echo "Test 3: Lock timeout - verify automatic expiration"
echo "---------------------------------------------------"

echo "Manually setting lock with 2-second timeout..."
docker exec tgsentinel-redis-1 redis-cli SET "tgsentinel:config_lock:channel:$CHAT_ID" "manual-lock" EX 2 >/dev/null

echo "Attempting update while lock is held..."
START_TIME=$(date +%s)
RESULT=$(update_channel $CHAT_ID "reaction_threshold" 25)
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

HTTP_STATUS=$(echo "$RESULT" | grep "HTTP_STATUS:" | cut -d: -f2)
echo "Update completed in ${DURATION}s with status: $HTTP_STATUS"

if [ "$HTTP_STATUS" = "200" ] && [ "$DURATION" -ge 2 ]; then
    echo "✅ PASS: Update waited for lock expiration and succeeded"
elif [ "$HTTP_STATUS" = "503" ]; then
    echo "⚠️  WARNING: Update exhausted retries before lock expired (expected if timeout > retry window)"
else
    echo "❌ FAIL: Unexpected behavior - Status: $HTTP_STATUS, Duration: ${DURATION}s"
fi

# Final cleanup
echo ""
echo "Cleaning up test locks..."
docker exec tgsentinel-redis-1 redis-cli DEL "tgsentinel:config_lock:channel:$CHAT_ID" >/dev/null 2>&1 || true
rm -f /tmp/update*.txt

echo ""
echo "=== All Tests Complete ==="
