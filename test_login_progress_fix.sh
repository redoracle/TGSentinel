#!/bin/bash

# Test script to verify login progress fix
echo "=== Testing Login Progress Fix ==="
echo ""

echo "1. Check initial state (should be empty):"
docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:login_progress
echo ""

echo "2. Simulate login start by submitting auth request:"
docker exec tgsentinel-redis-1 redis-cli --eval - tgsentinel:auth_queue <<'EOF'
return redis.call('RPUSH', KEYS[1], '{"action":"start","request_id":"test123","phone":"+31625561396"}')
EOF
echo ""

echo "3. Wait 2 seconds for auth manager to process..."
sleep 2
echo ""

echo "4. Check if stale progress was cleared (should still be empty after start):"
docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:login_progress
echo ""

echo "5. Manually set login progress to simulate sentinel updating it:"
docker exec tgsentinel-redis-1 redis-cli SETEX tgsentinel:login_progress 300 '{"stage":"authenticating","percent":50,"message":"Verifying code...","timestamp":"2025-11-17T20:00:00Z"}'
echo ""

echo "6. Verify progress exists:"
docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:login_progress
echo ""

echo "7. Check TTL (should be around 300 seconds):"
docker exec tgsentinel-redis-1 redis-cli TTL tgsentinel:login_progress
echo ""

echo "8. Simulate completion (set to 100%):"
docker exec tgsentinel-redis-1 redis-cli SETEX tgsentinel:login_progress 300 '{"stage":"completed","percent":100,"message":"Login complete!","timestamp":"2025-11-17T20:00:00Z"}'
echo ""

echo "9. Verify completion state:"
docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:login_progress | python3 -m json.tool
echo ""

echo "10. Simulate new login start (should clear the old progress):"
docker exec tgsentinel-redis-1 redis-cli --eval - tgsentinel:auth_queue <<'EOF'
return redis.call('RPUSH', KEYS[1], '{"action":"start","request_id":"test456","phone":"+31625561396"}')
EOF
echo ""

echo "11. Wait 2 seconds for auth manager to clear stale progress..."
sleep 2
echo ""

echo "12. Check if progress was cleared by auth manager:"
PROGRESS=$(docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:login_progress)
if [ -z "$PROGRESS" ]; then
    echo "✅ SUCCESS: Login progress was cleared at auth start!"
else
    echo "❌ FAILED: Login progress still exists:"
    echo "$PROGRESS" | python3 -m json.tool
fi
echo ""

echo "=== Test Complete ==="
