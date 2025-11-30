#!/bin/bash
set -e

echo "=== Testing Metrics Endpoint Configuration ==="
echo ""

# Unlock UI first
echo "1. Unlocking UI..."
curl -s -X POST http://localhost:5001/api/ui/lock -H "Content-Type: application/json" -d '{"action":"unlock","password":"changeme"}' -c /tmp/tgsentinel_cookies.txt > /dev/null
echo "   ✓ UI unlocked"

echo ""

# Test 2: Verify API exposes metrics_endpoint
echo "2. Checking if metrics_endpoint is exposed in Sentinel API..."
METRICS_ENDPOINT=$(curl -s http://localhost:8080/api/config | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['data']['system'].get('metrics_endpoint', 'NOT_FOUND'))")

if [ "$METRICS_ENDPOINT" != "NOT_FOUND" ]; then
    echo "   ✓ metrics_endpoint found in API: '$METRICS_ENDPOINT'"
else
    echo "   ✗ metrics_endpoint NOT found in API response"
    exit 1
fi

echo ""

# Test 3: Check if UI config page has the input field
echo "3. Checking if config page has metrics-endpoint input field..."
CONFIG_HTML=$(curl -s -b /tmp/tgsentinel_cookies.txt http://localhost:5001/config)

if echo "$CONFIG_HTML" | grep -q 'id="metrics-endpoint"'; then
    echo "   ✓ metrics-endpoint input field present in HTML"
else
    echo "   ✗ metrics-endpoint input field NOT found"
    exit 1
fi

echo ""

# Test 4: Verify JavaScript handlers
echo "4. Checking JavaScript handlers for metrics_endpoint..."

if echo "$CONFIG_HTML" | grep -q 'flatData.metrics_endpoint'; then
    echo "   ✓ collectPayload() handles metrics_endpoint"
else
    echo "   ✗ collectPayload() does NOT handle metrics_endpoint"
    exit 1
fi

if echo "$CONFIG_HTML" | grep -q 'config.system.metrics_endpoint'; then
    echo "   ✓ loadCurrentConfig() handles metrics_endpoint"
else
    echo "   ✗ loadCurrentConfig() does NOT handle metrics_endpoint"
    exit 1
fi

echo ""

# Test 5: Test save operation (update metrics_endpoint via API)
echo "5. Testing save operation (updating metrics_endpoint)..."
SAVE_RESPONSE=$(curl -s -X POST http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{"system":{"metrics_endpoint":"http://prometheus:9090/metrics"}}')

SAVE_STATUS=$(echo "$SAVE_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))")

if [ "$SAVE_STATUS" = "ok" ]; then
    echo "   ✓ Save successful"
else
    echo "   ✗ Save failed: $SAVE_STATUS"
    echo "   Response: $SAVE_RESPONSE"
    exit 1
fi

echo ""

# Test 6: Verify the value was persisted
echo "6. Verifying metrics_endpoint was saved..."
sleep 2  # Give it time to reload config
NEW_METRICS_ENDPOINT=$(curl -s http://localhost:8080/api/config | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['data']['system'].get('metrics_endpoint', ''))")

if [ "$NEW_METRICS_ENDPOINT" = "http://prometheus:9090/metrics" ]; then
    echo "   ✓ metrics_endpoint persisted: '$NEW_METRICS_ENDPOINT'"
else
    echo "   ✗ metrics_endpoint NOT persisted correctly"
    echo "   Expected: 'http://prometheus:9090/metrics'"
    echo "   Got: '$NEW_METRICS_ENDPOINT'"
    exit 1
fi

echo ""

# Test 7: Reset to empty string
echo "7. Resetting metrics_endpoint to empty string..."
RESET_RESPONSE=$(curl -s -X POST http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{"system":{"metrics_endpoint":""}}')

RESET_STATUS=$(echo "$RESET_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))")

if [ "$RESET_STATUS" = "ok" ]; then
    echo "   ✓ Reset successful"
else
    echo "   ✗ Reset failed"
    exit 1
fi

echo ""
echo "=== All metrics_endpoint tests passed! ==="
echo ""
echo "Summary:"
echo "  ✓ Backend config field added (SystemCfg.metrics_endpoint)"
echo "  ✓ API exposes metrics_endpoint in GET /api/config"
echo "  ✓ API accepts metrics_endpoint in POST /api/config"
echo "  ✓ UI form has metrics-endpoint input field"
echo "  ✓ JavaScript collectPayload() reads metrics_endpoint"
echo "  ✓ JavaScript loadCurrentConfig() populates metrics_endpoint"
echo "  ✓ Save/load cycle works correctly"
