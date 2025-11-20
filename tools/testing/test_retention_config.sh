#!/bin/bash
set -e

echo "=== Testing Message Retention Configuration ==="
echo ""

# Test 1: Verify API exposes retention config
echo "1. Checking if retention config is exposed in API..."
DB_CONFIG=$(curl -s http://localhost:8080/api/config | python3 -c "import sys, json; data = json.load(sys.stdin); print(json.dumps(data['data']['system']['database']))")

if echo "$DB_CONFIG" | grep -q '"max_messages": 200'; then
    echo "   ✓ max_messages: 200"
else
    echo "   ✗ max_messages NOT found or incorrect"
    echo "   Got: $DB_CONFIG"
    exit 1
fi

if echo "$DB_CONFIG" | grep -q '"retention_days": 30'; then
    echo "   ✓ retention_days: 30"
else
    echo "   ✗ retention_days NOT found or incorrect"
    exit 1
fi

if echo "$DB_CONFIG" | grep -q '"cleanup_enabled": true'; then
    echo "   ✓ cleanup_enabled: true"
else
    echo "   ✗ cleanup_enabled NOT found or incorrect"
    exit 1
fi

if echo "$DB_CONFIG" | grep -q '"cleanup_interval_hours": 24'; then
    echo "   ✓ cleanup_interval_hours: 24"
else
    echo "   ✗ cleanup_interval_hours NOT found or incorrect"
    exit 1
fi

if echo "$DB_CONFIG" | grep -q '"vacuum_on_cleanup": true'; then
    echo "   ✓ vacuum_on_cleanup: true"
else
    echo "   ✗ vacuum_on_cleanup NOT found or incorrect"
    exit 1
fi

if echo "$DB_CONFIG" | grep -q '"vacuum_hour": 3'; then
    echo "   ✓ vacuum_hour: 3"
else
    echo "   ✗ vacuum_hour NOT found or incorrect"
    exit 1
fi

echo ""

# Test 2: Check cleanup worker is running
echo "2. Checking if cleanup worker started successfully..."
if docker compose logs sentinel | grep -q "DATABASE-CLEANUP.*Starting cleanup"; then
    echo "   ✓ Cleanup worker started"
else
    echo "   ✗ Cleanup worker did NOT start"
    exit 1
fi

# Test 3: Verify cleanup ran (even if 0 messages deleted)
if docker compose logs sentinel | grep -q "DATABASE-CLEANUP.*Deleted.*messages"; then
    echo "   ✓ Cleanup executed"
else
    echo "   ✗ Cleanup did NOT execute"
    exit 1
fi

echo ""

# Test 4: Check current message count
echo "3. Checking current message count in database..."
MSG_COUNT=$(docker exec tgsentinel-sentinel-1 python3 -c "
from sqlalchemy import create_engine, text
engine = create_engine('sqlite:////app/data/sentinel.db')
with engine.connect() as conn:
    result = conn.execute(text('SELECT COUNT(*) FROM messages'))
    print(result.scalar())
")
echo "   Current messages in DB: $MSG_COUNT"

if [ "$MSG_COUNT" -lt 200 ]; then
    echo "   ✓ Message count is below max_messages limit"
else
    echo "   ⚠ Message count is at or above max_messages limit"
fi

echo ""
echo "=== All retention configuration tests passed! ==="
echo ""
echo "Default retention settings:"
echo "  - max_messages: 200"
echo "  - retention_days: 30"
echo "  - cleanup_enabled: true"
echo "  - cleanup_interval_hours: 24"
echo "  - vacuum_on_cleanup: true"
echo "  - vacuum_hour: 3"
