#!/bin/bash
set -e

echo "=== Testing Prometheus Metrics Endpoint ==="
echo ""

# Test 1: Check if /metrics endpoint exists
echo "1. Checking if /metrics endpoint is accessible..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/metrics)

if [ "$HTTP_CODE" = "200" ]; then
    echo "   ✓ /metrics endpoint accessible (HTTP $HTTP_CODE)"
else
    echo "   ✗ /metrics endpoint returned HTTP $HTTP_CODE (expected 200)"
    exit 1
fi

echo ""

# Test 2: Verify Prometheus format
echo "2. Verifying Prometheus text format..."
METRICS_RESPONSE=$(curl -s http://localhost:8080/metrics)

if echo "$METRICS_RESPONSE" | grep -q "^# HELP"; then
    echo "   ✓ Response contains Prometheus HELP comments"
else
    echo "   ✗ Response missing Prometheus HELP comments"
    exit 1
fi

if echo "$METRICS_RESPONSE" | grep -q "^# TYPE"; then
    echo "   ✓ Response contains Prometheus TYPE comments"
else
    echo "   ✗ Response missing Prometheus TYPE comments"
    exit 1
fi

echo ""

# Test 3: Check for TG Sentinel metrics
echo "3. Checking for TG Sentinel specific metrics..."

EXPECTED_METRICS=(
    "tgsentinel_messages_ingested_total"
    "tgsentinel_messages_processed_total"
    "tgsentinel_alerts_generated_total"
    "tgsentinel_alerts_sent_total"
    "tgsentinel_db_messages_current"
    "tgsentinel_worker_authorized"
    "tgsentinel_worker_connected"
    "tgsentinel_redis_stream_depth"
)

MISSING_METRICS=()
for metric in "${EXPECTED_METRICS[@]}"; do
    if echo "$METRICS_RESPONSE" | grep -q "$metric"; then
        echo "   ✓ Found metric: $metric"
    else
        echo "   ✗ Missing metric: $metric"
        MISSING_METRICS+=("$metric")
    fi
done

if [ ${#MISSING_METRICS[@]} -gt 0 ]; then
    echo ""
    echo "   ERROR: ${#MISSING_METRICS[@]} metrics missing"
    exit 1
fi

echo ""

# Test 4: Verify metric values are numeric
echo "4. Verifying metric values are valid numbers..."
if echo "$METRICS_RESPONSE" | grep -E "^tgsentinel_.*[0-9]+(\.[0-9]+)?$" > /dev/null; then
    echo "   ✓ Metrics have numeric values"
else
    echo "   ✗ Some metrics may have invalid values"
    exit 1
fi

echo ""

# Test 5: Check Content-Type header
echo "5. Checking Content-Type header..."
CONTENT_TYPE=$(curl -s -I http://localhost:8080/metrics | grep -i "content-type" | cut -d: -f2 | tr -d ' \r')

if echo "$CONTENT_TYPE" | grep -q "text/plain"; then
    echo "   ✓ Content-Type is correct: $CONTENT_TYPE"
else
    echo "   ⚠ Content-Type: $CONTENT_TYPE (expected text/plain)"
fi

echo ""
echo "=== All Prometheus Metrics Tests Passed! ==="
echo ""
echo "Sample metrics output:"
echo "$METRICS_RESPONSE" | head -20
echo "..."
