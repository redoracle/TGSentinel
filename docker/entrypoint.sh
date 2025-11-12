#!/bin/sh
# Copy session from volume to local filesystem to avoid Docker for Mac locking issues
if [ -f "/app/data/tgsentinel.session" ] && [ ! -f "/tmp/tgsentinel.session" ]; then
    cp /app/data/tgsentinel.session /tmp/tgsentinel.session
    cp /app/data/tgsentinel.session-journal /tmp/tgsentinel.session-journal 2>/dev/null || true
fi

# Update config to use /tmp session
export TG_SESSION_OVERRIDE="/tmp/tgsentinel.session"

# Run the main command
exec "$@"
