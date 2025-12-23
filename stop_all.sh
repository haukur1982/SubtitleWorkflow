#!/bin/bash

echo "🛑 Stopping all OmegaTV processes..."

# Kill Watchdog
WATCHDOG_PID_FILE="/tmp/omega_watchdog.pid"
if [ -f "$WATCHDOG_PID_FILE" ]; then
    pid=$(cat "$WATCHDOG_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing watchdog PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$WATCHDOG_PID_FILE"
fi

# Kill Caffeinate (sleep prevention)
CAFFEINATE_PID_FILE="/tmp/omega_caffeinate.pid"
if [ -f "$CAFFEINATE_PID_FILE" ]; then
    pid=$(cat "$CAFFEINATE_PID_FILE" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "   Killing caffeinate PID: $pid"
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$CAFFEINATE_PID_FILE"
fi

# Kill Dashboard (and all Python processes running dashboard.py)
pids=$(ps aux | grep "[d]ashboard.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing dashboard.py PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

# Kill Omega Manager
pids=$(ps aux | grep "[o]mega_manager.py" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing omega_manager.py PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

# Kill FFmpeg (be careful not to kill system ffmpeg if used elsewhere, but for this user it's likely safe)
pids=$(ps aux | grep "[f]fmpeg" | awk '{print $2}')
if [ -n "$pids" ]; then
    echo "   Killing ffmpeg PIDs: $pids"
    kill -9 $pids 2>/dev/null
fi

echo "✅ All processes stopped."
