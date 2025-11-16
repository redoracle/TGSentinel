#!/bin/sh
# TG Sentinel Entrypoint
#
# ARCHITECTURAL NOTE (Dual-DB Architecture):
# - Sentinel container: owns /app/data/tgsentinel.session (exclusive access)
# - UI container: owns /app/data/ui.db (exclusive access)
# - Volumes are separate (tgsentinel_sentinel_data vs tgsentinel_ui_data)
# - No session file copying or sharing between containers
#
# The legacy workaround for "Docker for Mac locking issues" is removed.
# If SQLite locking occurs, the solution is:
# 1. Use WAL mode (Write-Ahead Logging) in Telethon
# 2. Ensure proper connection management
# 3. Never have multiple processes access the same SQLite file

# Run the main command
exec "$@"
