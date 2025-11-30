#!/bin/bash
set -e

echo "=== Testing Retention UI Configuration Page ==="
echo ""

# Unlock UI first
echo "0. Unlocking UI..."
curl -s -X POST http://localhost:5001/api/ui/lock -H "Content-Type: application/json" -d '{"action":"unlock","password":"changeme"}' -c /tmp/tgsentinel_cookies.txt > /dev/null
echo "   ✓ UI unlocked"

echo ""

# Test 1: Check if UI is accessible
echo "1. Checking if UI is accessible..."
UI_RESPONSE=$(curl -s -b /tmp/tgsentinel_cookies.txt -o /dev/null -w "%{http_code}" http://localhost:5001/)
if [ "$UI_RESPONSE" = "200" ]; then
    echo "   ✓ UI is accessible (HTTP 200)"
else
    echo "   ✗ UI returned HTTP $UI_RESPONSE"
    exit 1
fi

echo ""

# Test 2: Check if config page HTML contains retention input fields
echo "2. Checking if config page has retention input fields..."
CONFIG_HTML=$(curl -s -b /tmp/tgsentinel_cookies.txt http://localhost:5001/config)

if echo "$CONFIG_HTML" | grep -q 'id="max-messages"'; then
    echo "   ✓ max-messages input field present"
else
    echo "   ✗ max-messages input field NOT found"
    exit 1
fi

if echo "$CONFIG_HTML" | grep -q 'id="db-retention-days"'; then
    echo "   ✓ db-retention-days input field present"
else
    echo "   ✗ db-retention-days input field NOT found"
    exit 1
fi

if echo "$CONFIG_HTML" | grep -q 'id="cleanup-enabled"'; then
    echo "   ✓ cleanup-enabled checkbox present"
else
    echo "   ✗ cleanup-enabled checkbox NOT found"
    exit 1
fi

if echo "$CONFIG_HTML" | grep -q 'id="vacuum-on-cleanup"'; then
    echo "   ✓ vacuum-on-cleanup checkbox present"
else
    echo "   ✗ vacuum-on-cleanup checkbox NOT found"
    exit 1
fi

echo ""

# Test 3: Check if JavaScript has database config handling
echo "3. Checking if JavaScript handles database config..."
if echo "$CONFIG_HTML" | grep -q 'config.system.database.max_messages'; then
    echo "   ✓ JavaScript loads max_messages"
else
    echo "   ✗ JavaScript does NOT load max_messages"
    exit 1
fi

if echo "$CONFIG_HTML" | grep -q 'config.system.database.retention_days'; then
    echo "   ✓ JavaScript loads retention_days"
else
    echo "   ✗ JavaScript does NOT load retention_days"
    exit 1
fi

if echo "$CONFIG_HTML" | grep -q 'payload.system.database'; then
    echo "   ✓ JavaScript saves system.database object"
else
    echo "   ✗ JavaScript does NOT save system.database"
    exit 1
fi

echo ""

# Test 4: Check if UI can reach Sentinel API
echo "4. Checking if UI can reach Sentinel API..."
# This tests from the UI container's perspective
UI_TO_SENTINEL=$(docker exec tgsentinel-ui-1 curl -s -o /dev/null -w "%{http_code}" http://sentinel:8080/api/config)
if [ "$UI_TO_SENTINEL" = "200" ]; then
    echo "   ✓ UI can reach Sentinel API (HTTP 200)"
else
    echo "   ✗ UI to Sentinel returned HTTP $UI_TO_SENTINEL"
    exit 1
fi

echo ""
echo "=== All UI configuration tests passed! ==="
echo ""
echo "Next steps:"
echo "  1. Open http://localhost:5001/config in a browser"
echo "  2. Navigate to 'System Settings' section"
echo "  3. Verify retention fields are visible with correct default values:"
echo "     - Max Messages: 200"
echo "     - Retention Days: 30"
echo "     - Cleanup Enabled: checked"
echo "     - VACUUM on Cleanup: checked"
echo "  4. Try changing max_messages to 300 and save"
echo "  5. Reload page and verify the change persisted"
