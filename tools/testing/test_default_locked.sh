#!/bin/bash
# Test default-locked behavior for fresh sessions (no cookies)

set -e

echo "=== Testing Default-Locked Behavior ==="
echo ""

# Test 1: Fresh session should be locked by default
echo "Test 1: GET / without cookies (fresh session)"
RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" http://127.0.0.1:5001/)
STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | grep -v "HTTP_STATUS")

if [ "$STATUS" == "423" ]; then
    echo "✓ PASS: Returns 423 Locked status"
else
    echo "✗ FAIL: Expected 423, got $STATUS"
fi

if echo "$BODY" | grep -q "UI Locked"; then
    echo "✓ PASS: Displays locked_ui.html page"
else
    echo "✗ FAIL: Does not display lock page"
fi

echo ""

# Test 2: API endpoint status check
echo "Test 2: GET /api/ui/lock/status without cookies"
LOCK_STATUS=$(curl -s -c /tmp/tgsentinel_cookies.txt http://127.0.0.1:5001/api/ui/lock/status)
IS_LOCKED=$(echo "$LOCK_STATUS" | jq -r '.locked')

if [ "$IS_LOCKED" == "true" ]; then
    echo "✓ PASS: API reports locked=true for fresh session"
else
    echo "✗ FAIL: API reports locked=$IS_LOCKED (expected true)"
fi

echo ""

# Test 3: Unlock and verify has_been_unlocked flag
echo "Test 3: POST /api/ui/lock with action=unlock"
UI_LOCK_PASSWORD=${UI_LOCK_PASSWORD:-"changeme"}
UNLOCK_RESPONSE=$(curl -s -b /tmp/tgsentinel_cookies.txt -c /tmp/tgsentinel_cookies.txt \
    -X POST http://127.0.0.1:5001/api/ui/lock \
    -H "Content-Type: application/json" \
    -d "{\"action\": \"unlock\", \"password\": \"$UI_LOCK_PASSWORD\"}")

UNLOCK_STATUS=$(echo "$UNLOCK_RESPONSE" | jq -r '.status')

if [ "$UNLOCK_STATUS" == "ok" ]; then
    echo "✓ PASS: Unlock successful"
else
    echo "✗ FAIL: Unlock failed: $UNLOCK_RESPONSE"
fi

echo ""

# Test 4: After unlock, should remain unlocked
echo "Test 4: GET / with cookie after unlock"
AFTER_UNLOCK=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -b /tmp/tgsentinel_cookies.txt http://127.0.0.1:5001/)
AFTER_STATUS=$(echo "$AFTER_UNLOCK" | grep "HTTP_STATUS" | cut -d: -f2)

if [ "$AFTER_STATUS" == "200" ] || [ "$AFTER_STATUS" == "401" ]; then
    echo "✓ PASS: Returns $AFTER_STATUS (unlocked, not 423)"
else
    echo "✗ FAIL: Expected 200 or 401, got $AFTER_STATUS"
fi

echo ""

# Test 5: Lock status API should report unlocked
echo "Test 5: GET /api/ui/lock/status after unlock"
LOCK_STATUS_AFTER=$(curl -s -b /tmp/tgsentinel_cookies.txt http://127.0.0.1:5001/api/ui/lock/status)
IS_LOCKED_AFTER=$(echo "$LOCK_STATUS_AFTER" | jq -r '.locked')

if [ "$IS_LOCKED_AFTER" == "false" ]; then
    echo "✓ PASS: API reports locked=false after unlock"
else
    echo "✗ FAIL: API reports locked=$IS_LOCKED_AFTER (expected false)"
fi

echo ""

# Test 6: Explicit lock should override
echo "Test 6: POST /api/ui/lock with action=lock (explicit lock)"
LOCK_RESPONSE=$(curl -s -b /tmp/tgsentinel_cookies.txt -c /tmp/tgsentinel_cookies.txt \
    -X POST http://127.0.0.1:5001/api/ui/lock \
    -H "Content-Type: application/json" \
    -d "{\"action\": \"lock\"}")

LOCK_RESULT=$(echo "$LOCK_RESPONSE" | jq -r '.status')

if [ "$LOCK_RESULT" == "ok" ]; then
    echo "✓ PASS: Explicit lock successful"
else
    echo "✗ FAIL: Explicit lock failed: $LOCK_RESPONSE"
fi

echo ""

# Test 7: After explicit lock, should be locked
echo "Test 7: GET / after explicit lock"
AFTER_LOCK=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -b /tmp/tgsentinel_cookies.txt http://127.0.0.1:5001/)
LOCK_CHECK_STATUS=$(echo "$AFTER_LOCK" | grep "HTTP_STATUS" | cut -d: -f2)

if [ "$LOCK_CHECK_STATUS" == "423" ]; then
    echo "✓ PASS: Returns 423 after explicit lock"
else
    echo "✗ FAIL: Expected 423, got $LOCK_CHECK_STATUS"
fi

echo ""

# Cleanup
rm -f /tmp/tgsentinel_cookies.txt

echo "=== Test Complete ==="
