#!/bin/bash

# Omega Manager Launcher
# Ensures the process starts in the background without being suspended by SIGTTOU.

# Ensure clean slate
./stop_all.sh

LOG_FILE="logs/manager.log"
LOCK_FILE="/tmp/omega_manager.lock"

echo "🚀 Starting OmegaTV System..."

# 1. Check if already running
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE")
    if ps -p $PID > /dev/null; then
        echo "❌ Manager is already running (PID: $PID)"
        exit 1
    else
        echo "⚠️ Found stale lock file. Cleaning up..."
        rm "$LOCK_FILE"
    fi
fi

# 2. Start Dashboard
echo "📊 Starting Dashboard..."
nohup python3 dashboard.py > logs/dashboard.log 2>&1 &
DASH_PID=$!
echo "   Dashboard PID: $DASH_PID"

# 3. Start Manager in background
nohup python3 omega_manager.py > /dev/null 2>&1 &

# 3. Capture PID
NEW_PID=$!
echo "✅ Manager started with PID: $NEW_PID"

# 4. Tail the log file so user sees immediate feedback
echo "📜 Tailing logs (Ctrl+C to exit tail, Manager will keep running)..."
echo "----------------------------------------------------------------"
tail -f "$LOG_FILE"
