#!/bin/bash

echo "🛑 Stopping all OmegaTV processes..."

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
