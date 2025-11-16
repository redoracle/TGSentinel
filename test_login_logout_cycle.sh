#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Array to store user info for comparison
declare -a TEST_USERS
declare -a TEST_USER_IDS
declare -a TEST_PHONES

echo "=========================================="
echo "Testing Login/Logout Cycle"
echo "=========================================="
echo ""

# Session files to test
SESSIONS=("my_dutch_fresh.session" "my_dutch.session" "my_italian.session")

test_session() {
    local session_file=$1
    local test_num=$2
    
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}TEST ${test_num}: ${session_file}${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    # Step 1: Upload session
    echo ""
    echo "Step 1: Uploading session..."
    upload_response=$(curl -s -F "session_file=@${session_file}" http://localhost:5001/api/session/upload)
    upload_status=$(echo "$upload_response" | jq -r '.status')
    
    if [ "$upload_status" = "ok" ]; then
        echo -e "${GREEN}✓ Session uploaded successfully${NC}"
    else
        echo -e "${RED}✗ Session upload failed${NC}"
        echo "$upload_response" | jq .
        return 1
    fi
    
    # Step 2: Wait for authorization
    echo ""
    echo "Step 2: Waiting 20s for Sentinel to authorize..."
    sleep 20
    
    # Step 3: Check Redis worker_status
    echo ""
    echo "Step 3: Checking Redis worker_status..."
    worker_status=$(docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:worker_status)
    authorized=$(echo "$worker_status" | jq -r '.authorized')
    
    if [ "$authorized" = "true" ]; then
        echo -e "${GREEN}✓ Worker authorized${NC}"
        echo "   Status: $(echo "$worker_status" | jq -r '.status')"
    else
        echo -e "${RED}✗ Worker not authorized${NC}"
        echo "$worker_status" | jq .
        return 1
    fi
    
    # Step 4: Check Redis user_info (detailed)
    echo ""
    echo "Step 4: Checking Redis user_info..."
    user_info=$(docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:user_info)
    
    if [ -n "$user_info" ] && [ "$user_info" != "(nil)" ]; then
        echo -e "${GREEN}✓ User info found in Redis${NC}"
        username=$(echo "$user_info" | jq -r '.username // "N/A"')
        user_id=$(echo "$user_info" | jq -r '.user_id // "N/A"')
        phone=$(echo "$user_info" | jq -r '.phone // "N/A"')
        first_name=$(echo "$user_info" | jq -r '.first_name // "N/A"')
        last_name=$(echo "$user_info" | jq -r '.last_name // "N/A"')
        avatar_url=$(echo "$user_info" | jq -r '.avatar // "N/A"')
        
        echo "   ┌─ User Information ─────────────────────"
        echo "   │ Username:   @${username}"
        echo "   │ User ID:    ${user_id}"
        echo "   │ First Name: ${first_name}"
        echo "   │ Last Name:  ${last_name}"
        echo "   │ Phone:      ${phone}"
        echo "   │ Avatar URL: ${avatar_url}"
        echo "   └────────────────────────────────────────"
        
        # Store user info for comparison
        TEST_USERS+=("$username")
        TEST_USER_IDS+=("$user_id")
        TEST_PHONES+=("$phone")
        
        # Verify avatar exists in Redis
        if [[ "$avatar_url" == "/api/avatar/user/"* ]]; then
            avatar_key="tgsentinel:user_avatar:${user_id}"
            avatar_exists=$(docker exec tgsentinel-redis-1 redis-cli EXISTS "$avatar_key")
            if [ "$avatar_exists" = "1" ]; then
                # Get avatar size
                avatar_size=$(docker exec tgsentinel-redis-1 redis-cli STRLEN "$avatar_key")
                echo "   │ Avatar Key: ${avatar_key}"
                echo "   │ Avatar Size: ${avatar_size} bytes (base64)"
                echo -e "   └─ ${GREEN}✓ Avatar cached in Redis${NC}"
            else
                echo -e "   └─ ${RED}✗ Avatar not found in Redis${NC}"
                return 1
            fi
        else
            echo "   └─ Using default avatar (no custom avatar)"
        fi
    else
        echo -e "${RED}✗ User info not found in Redis${NC}"
        return 1
    fi
    
    # Step 5: Check session files exist
    echo ""
    echo "Step 5: Verifying session files in Sentinel container..."
    session_files=$(docker exec tgsentinel-sentinel-1 sh -c 'ls -lah /app/data/ | grep session | wc -l' | tr -d ' ')
    
    if [ "$session_files" -gt 0 ]; then
        echo -e "${GREEN}✓ Session files present (${session_files} files)${NC}"
        docker exec tgsentinel-sentinel-1 sh -c 'ls -lh /app/data/ | grep session'
    else
        echo -e "${RED}✗ No session files found${NC}"
        return 1
    fi
    
    # Step 6: Check UI loads correctly
    echo ""
    echo "Step 6: Checking UI authentication state..."
    ui_title=$(curl -s http://localhost:5001/ | grep -o '<title>[^<]*</title>' | sed 's/<[^>]*>//g')
    
    if [[ "$ui_title" == *"Dashboard"* ]]; then
        echo -e "${GREEN}✓ UI shows Dashboard (authenticated)${NC}"
        echo "   Title: ${ui_title}"
    else
        echo -e "${RED}✗ UI shows: ${ui_title}${NC}"
        return 1
    fi
    
    # Step 7: Logout
    echo ""
    echo "Step 7: Logging out..."
    logout_response=$(curl -s -X POST http://localhost:5001/api/session/logout)
    logout_status=$(echo "$logout_response" | jq -r '.status')
    
    if [ "$logout_status" = "ok" ]; then
        echo -e "${GREEN}✓ Logout API call successful${NC}"
    else
        echo -e "${RED}✗ Logout failed${NC}"
        echo "$logout_response" | jq .
        return 1
    fi
    
    # Step 8: Wait for logout to process
    echo ""
    echo "Step 8: Waiting 5s for logout to complete..."
    sleep 5
    
    # Step 9: Check Redis worker_status after logout
    echo ""
    echo "Step 9: Verifying Redis worker_status after logout..."
    worker_status_after=$(docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:worker_status)
    authorized_after=$(echo "$worker_status_after" | jq -r '.authorized')
    status_after=$(echo "$worker_status_after" | jq -r '.status')
    
    if [ "$authorized_after" = "false" ] && [ "$status_after" = "logged_out" ]; then
        echo -e "${GREEN}✓ Worker status correctly set to logged_out${NC}"
    else
        echo -e "${RED}✗ Worker status not updated correctly${NC}"
        echo "$worker_status_after" | jq .
        return 1
    fi
    
    # Step 10: Check Redis user_info cleared
    echo ""
    echo "Step 10: Verifying Redis user_info cleared..."
    user_info_after=$(docker exec tgsentinel-redis-1 redis-cli GET tgsentinel:user_info)
    
    if [ "$user_info_after" = "(nil)" ] || [ -z "$user_info_after" ]; then
        echo -e "${GREEN}✓ User info cleared from Redis${NC}"
        
        # Verify all user-related keys are cleared
        echo ""
        echo "   Checking all user-related Redis keys..."
        user_avatar_count=$(docker exec tgsentinel-redis-1 redis-cli --scan --pattern "tgsentinel:user_avatar:*" | wc -l | tr -d ' ')
        chat_avatar_count=$(docker exec tgsentinel-redis-1 redis-cli --scan --pattern "tgsentinel:chat_avatar:*" | wc -l | tr -d ' ')
        cached_channels=$(docker exec tgsentinel-redis-1 redis-cli EXISTS tgsentinel:cached_channels)
        cached_users=$(docker exec tgsentinel-redis-1 redis-cli EXISTS tgsentinel:cached_users)
        
        echo "   ┌─ Redis Cleanup Verification ───────────"
        echo "   │ User avatars:    ${user_avatar_count} keys"
        echo "   │ Chat avatars:    ${chat_avatar_count} keys"
        echo "   │ Cached channels: $([ "$cached_channels" = "1" ] && echo "EXISTS" || echo "CLEARED")"
        echo "   │ Cached users:    $([ "$cached_users" = "1" ] && echo "EXISTS" || echo "CLEARED")"
        echo "   └────────────────────────────────────────"
        
        if [ "$user_avatar_count" = "0" ] && [ "$chat_avatar_count" = "0" ] && \
           [ "$cached_channels" = "0" ] && [ "$cached_users" = "0" ]; then
            echo -e "   ${GREEN}✓ All user-related cache keys cleared${NC}"
        else
            echo -e "   ${YELLOW}⚠ Some cache keys still present (expected if logout during cache operation)${NC}"
        fi
    else
        echo -e "${RED}✗ User info still present in Redis${NC}"
        echo "$user_info_after"
        return 1
    fi
    
    # Step 11: Check session files deleted
    echo ""
    echo "Step 11: Verifying session files deleted..."
    session_files_after=$(docker exec tgsentinel-sentinel-1 sh -c 'ls -lah /app/data/ 2>/dev/null | grep session | wc -l' | tr -d ' ')
    
    if [ "$session_files_after" = "0" ]; then
        echo -e "${GREEN}✓ All session files deleted${NC}"
    else
        echo -e "${RED}✗ Session files still present (${session_files_after} files)${NC}"
        docker exec tgsentinel-sentinel-1 sh -c 'ls -lh /app/data/ | grep session'
        return 1
    fi
    
    # Step 12: Check UI redirects to login
    echo ""
    echo "Step 12: Verifying UI shows login page..."
    ui_title_after=$(curl -s http://localhost:5001/ | grep -o '<title>[^<]*</title>' | sed 's/<[^>]*>//g')
    
    if [[ "$ui_title_after" == *"Login Required"* ]]; then
        echo -e "${GREEN}✓ UI shows Login Required page${NC}"
        echo "   Title: ${ui_title_after}"
    else
        echo -e "${RED}✗ UI shows: ${ui_title_after}${NC}"
        return 1
    fi
    
    # Step 13: Wait to ensure no file recreation
    echo ""
    echo "Step 13: Waiting 65s to verify no session file recreation..."
    sleep 65
    
    session_files_final=$(docker exec tgsentinel-sentinel-1 sh -c 'ls -lah /app/data/ 2>/dev/null | grep session | wc -l' | tr -d ' ')
    
    if [ "$session_files_final" = "0" ]; then
        echo -e "${GREEN}✓ Session files remain deleted (no recreation)${NC}"
    else
        echo -e "${RED}✗ Session files were recreated (${session_files_final} files)${NC}"
        return 1
    fi
    
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ TEST ${test_num} PASSED: ${session_file}${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    return 0
}

# Run tests for each session
test_count=1
passed=0
failed=0

for session in "${SESSIONS[@]}"; do
    if test_session "$session" "$test_count"; then
        ((passed++))
    else
        ((failed++))
        echo -e "${RED}Test ${test_count} failed, stopping...${NC}"
        break
    fi
    test_count=$((test_count + 1))
done

echo ""
echo "=========================================="
echo "FINAL RESULTS"
echo "=========================================="
echo -e "Passed: ${GREEN}${passed}${NC}"
echo -e "Failed: ${RED}${failed}${NC}"
echo "=========================================="

# Display user switching comparison
if [ ${#TEST_USERS[@]} -gt 0 ]; then
    echo ""
    echo "=========================================="
    echo "USER SWITCHING COMPARISON"
    echo "=========================================="
    for i in "${!TEST_USERS[@]}"; do
        test_num=$((i + 1))
        echo -e "${CYAN}Test ${test_num}: ${SESSIONS[$i]}${NC}"
        echo "  • Username: @${TEST_USERS[$i]}"
        echo "  • User ID:  ${TEST_USER_IDS[$i]}"
        echo "  • Phone:    ${TEST_PHONES[$i]}"
        echo ""
    done
    
    # Check if we tested different users
    unique_users=$(printf '%s\n' "${TEST_USER_IDS[@]}" | sort -u | wc -l | tr -d ' ')
    total_tests=${#TEST_USER_IDS[@]}
    
    if [ "$unique_users" -gt 1 ]; then
        echo -e "${GREEN}✓ Successfully tested ${unique_users} different users across ${total_tests} sessions${NC}"
        echo -e "${GREEN}✓ User switching works correctly without container restart${NC}"
    elif [ "$unique_users" -eq 1 ] && [ "$total_tests" -gt 1 ]; then
        echo -e "${BLUE}ℹ Tested same user (${TEST_USERS[0]}) with ${total_tests} different session files${NC}"
    fi
    echo "=========================================="
fi

if [ $failed -eq 0 ]; then
    echo -e "${GREEN}✓ ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}✗ SOME TESTS FAILED${NC}"
    exit 1
fi
