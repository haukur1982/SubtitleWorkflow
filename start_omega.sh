#!/bin/bash

# Omega Manager Launcher
# Ensures the process starts in the background without being suspended by SIGTTOU.

# Ensure clean slate
if [ "${OMEGA_SKIP_STOP:-0}" != "1" ]; then
  ./stop_all.sh
fi

LOG_FILE="logs/manager.log"
LOCK_FILE="/tmp/omega_manager.lock"
WATCHDOG_PID_FILE="/tmp/omega_watchdog.pid"
CAFFEINATE_PID_FILE="/tmp/omega_caffeinate.pid"

echo "🚀 Starting OmegaTV System..."

# Optional: load secrets from a local file (kept out of git).
if [ -f ".omega_secrets" ]; then
  # shellcheck disable=SC1091
  source ".omega_secrets"
fi

# Cloud Run Job defaults (override in shell if needed)
export OMEGA_CLOUD_RUN_JOB="${OMEGA_CLOUD_RUN_JOB:-omega-cloud-worker}"
export OMEGA_CLOUD_RUN_REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
export OMEGA_CLOUD_PROJECT="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
# Cloud-first translation/editor pipeline (disable by setting OMEGA_CLOUD_PIPELINE=0)
export OMEGA_CLOUD_PIPELINE="${OMEGA_CLOUD_PIPELINE:-1}"
export OMEGA_JOBS_BUCKET="${OMEGA_JOBS_BUCKET:-omega-jobs-subtitle-project}"
export OMEGA_JOBS_PREFIX="${OMEGA_JOBS_PREFIX:-jobs}"
# Enable the 3rd-pass polish step for all jobs by default ("review" or "all").
export OMEGA_CLOUD_POLISH_MODE="${OMEGA_CLOUD_POLISH_MODE:-all}"
# Detect and suppress choir/worship lyrics before translation.
export OMEGA_CLOUD_MUSIC_DETECT="${OMEGA_CLOUD_MUSIC_DETECT:-1}"
# Remote review portal (Cloud Run URL) + email settings
export OMEGA_REVIEW_PORTAL_URL="${OMEGA_REVIEW_PORTAL_URL:-}"
export OMEGA_REVIEWER_EMAIL="${OMEGA_REVIEWER_EMAIL:-hawk1982@me.com}"
export OMEGA_SMTP_HOST="${OMEGA_SMTP_HOST:-smtp.gmail.com}"
export OMEGA_SMTP_PORT="${OMEGA_SMTP_PORT:-587}"
export OMEGA_SMTP_USER="${OMEGA_SMTP_USER:-haukur1982@gmail.com}"
export OMEGA_SMTP_PASS="${OMEGA_SMTP_PASS:-}"
export OMEGA_SMTP_FROM="${OMEGA_SMTP_FROM:-haukur1982@gmail.com}"
# Allow PyTorch to load trusted VAD checkpoints used by whisperx/pyannote.
export TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="${TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD:-1}"

# Pick Python (prefer venv if present)
BASE_DIR="$(pwd)"
if [ -n "${OMEGA_VENV_PY:-}" ] && [ -x "${OMEGA_VENV_PY:-}" ]; then
  OMEGA_PYTHON="${OMEGA_VENV_PY}"
elif [ -x "$BASE_DIR/.venv/bin/python3" ]; then
  OMEGA_PYTHON="$BASE_DIR/.venv/bin/python3"
else
  OMEGA_PYTHON="python3"
fi
export OMEGA_PYTHON

# Ensure user-installed Python CLI tools (like whisperx) are on PATH
PY_USER_BIN="$($OMEGA_PYTHON -m site --user-base 2>/dev/null)/bin"
if [ -d "$PY_USER_BIN" ]; then
  export PATH="$PY_USER_BIN:$PATH"
fi

# Prefer existing WhisperX CLI if available (keeps local ASR stable)
if [ -x "$HOME/Library/Python/3.9/bin/whisperx" ]; then
  export OMEGA_WHISPER_BIN="$HOME/Library/Python/3.9/bin/whisperx"
elif [ -x "$BASE_DIR/.venv/bin/whisperx" ]; then
  export OMEGA_WHISPER_BIN="$BASE_DIR/.venv/bin/whisperx"
fi

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
nohup "$OMEGA_PYTHON" dashboard.py > logs/dashboard.log 2>&1 &
DASH_PID=$!
echo "   Dashboard PID: $DASH_PID"
echo "   Dashboard: http://127.0.0.1:8080"

# 2.5 Check external storage readiness (symlink targets writable)
echo "💾 Checking SSD mount / symlink writability..."
if "$OMEGA_PYTHON" - <<'PY'
import sys
import config
sys.exit(0 if config.critical_paths_ready(require_write=True) else 1)
PY
then
  echo "   ✅ Storage ready"
else
  echo "   ❌ Storage not ready (mount /Volumes/Extreme SSD). Manager will wait."
fi

# 3. Start Manager in background
nohup "$OMEGA_PYTHON" omega_manager.py > /dev/null 2>&1 &

# 3. Capture PID
NEW_PID=$!
echo "✅ Manager started with PID: $NEW_PID"

# 3.5 Keep Mac awake while manager runs (prevents sleep stalls)
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -dimsu -w "$NEW_PID" >/dev/null 2>&1 &
  echo $! > "$CAFFEINATE_PID_FILE"
  echo "☕ Caffeinate active (PID: $(cat "$CAFFEINATE_PID_FILE"))"
fi

# 3.6 Start watchdog (auto-restart manager/dashboard if they die)
if [ -f "$WATCHDOG_PID_FILE" ]; then
  OLD_PID=$(cat "$WATCHDOG_PID_FILE" 2>/dev/null)
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "🐶 Watchdog already running (PID: $OLD_PID)"
  else
    rm -f "$WATCHDOG_PID_FILE"
  fi
fi
if [ ! -f "$WATCHDOG_PID_FILE" ]; then
  nohup "$OMEGA_PYTHON" process_watchdog.py > logs/watchdog.log 2>&1 &
  echo $! > "$WATCHDOG_PID_FILE"
  echo "🐶 Watchdog started (PID: $(cat "$WATCHDOG_PID_FILE"))"
fi

# 4. Tail the log file so user sees immediate feedback
if [ "${OMEGA_NO_TAIL:-0}" = "1" ]; then
  exit 0
fi
echo "📜 Tailing logs (Ctrl+C to exit tail, Manager will keep running)..."
echo "----------------------------------------------------------------"
tail -f "$LOG_FILE"
