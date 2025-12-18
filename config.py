import os
import shutil
import time
import logging
from pathlib import Path

# --- BASE PATHS ---
BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "1_INBOX"
VAULT_DIR = BASE_DIR / "2_VAULT"
VAULT_DATA = VAULT_DIR / "Data"
VAULT_VIDEOS = VAULT_DIR / "Videos"
PROXIES_DIR = VAULT_DIR / "Proxies"
EDITOR_DIR = BASE_DIR / "3_EDITOR"
TRANSLATED_DONE_DIR = BASE_DIR / "3_TRANSLATED_DONE"
DELIVERY_DIR = BASE_DIR / "4_DELIVERY"
SRT_DIR = DELIVERY_DIR / "SRT"
VIDEO_DIR = DELIVERY_DIR / "VIDEO"
ERROR_DIR = BASE_DIR / "99_ERRORS"

logger = logging.getLogger("OmegaConfig")

def _safe_mkdir(path: Path) -> None:
    """
    Create directories when possible.

    This must not crash if the external SSD is temporarily unmounted and the
    repo paths are dangling symlinks.
    """
    try:
        if path.exists() and path.is_dir():
            return
        # Never try to mkdir over a symlink (including a dangling one).
        if path.is_symlink():
            return
        # If any parent is a dangling symlink, we can't create children safely.
        for parent in path.parents:
            if parent.is_symlink() and not parent.exists():
                return
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Could not ensure directory %s: %s", path, e)


# Ensure directories exist (when storage is ready)
for d in [INBOX_DIR, VAULT_DATA, VAULT_VIDEOS, PROXIES_DIR, EDITOR_DIR, TRANSLATED_DONE_DIR, SRT_DIR, VIDEO_DIR, ERROR_DIR]:
    _safe_mkdir(d)

# --- BINARIES ---
def get_binary(name, default):
    path = shutil.which(name)
    return path if path else default

FFMPEG_BIN = get_binary("ffmpeg", "ffmpeg")
FFPROBE_BIN = get_binary("ffprobe", "ffprobe")
WHISPER_BIN = get_binary("whisperx", "whisperx")

# --- STORAGE READINESS ---
_WRITE_PROBE_CACHE = {}
_WRITE_PROBE_TTL_SECONDS = 30.0

def critical_paths_ready(require_write: bool = False) -> bool:
    """
    Returns True if the minimum required filesystem paths are accessible.

    This is intentionally dynamic (it may become True after an external drive is mounted).
    """
    required = [INBOX_DIR, VAULT_DIR, DELIVERY_DIR]

    def _check_dir(p: Path) -> bool:
        try:
            target = p.resolve() if p.is_symlink() else p
        except Exception:
            target = p

        if not target.exists():
            return False
        if not target.is_dir():
            return False

        if require_write:
            now = time.monotonic()
            cache_key = str(target)
            cached = _WRITE_PROBE_CACHE.get(cache_key)
            if cached and (now - cached[0]) < _WRITE_PROBE_TTL_SECONDS:
                return bool(cached[1])

            writable = os.access(str(target), os.W_OK | os.X_OK)
            if writable:
                probe_path = target / f".omega_write_test.{os.getpid()}.{int(now * 1e9)}"
                try:
                    with open(probe_path, "wb") as f:
                        f.write(b"1")
                    writable = True
                except Exception:
                    writable = False
                finally:
                    try:
                        probe_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            _WRITE_PROBE_CACHE[cache_key] = (now, writable)
            return bool(writable)

        return True

    return all(_check_dir(p) for p in required)

# --- SETTINGS ---
# Whisper
WHISPER_MODEL = "large-v3"
WHISPER_COMPUTE = "int8"
WHISPER_DEVICE = "cpu"

# Gemini Models
# ⚠️ CRITICAL: NEVER USE GEMINI 1.5. IT IS BANNED.
MODEL_TRANSLATOR = "gemini-3-pro-preview"  # The "Brain" for Translation
MODEL_EDITOR = "gemini-3-pro-preview"      # The "Brain" for Review
GEMINI_LOCATION = "global"                 # Required for Preview models

# --- CLOUD ARTIFACTS (GCS) ---
# Store per-job JSON artifacts (skeleton/termbook/translation/approved/checkpoints) in GCS.
# This enables a cloud-first translation/editor pipeline while keeping heavy video work local.
OMEGA_JOBS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
OMEGA_JOBS_PREFIX = os.environ.get("OMEGA_JOBS_PREFIX", "jobs")

# Optional: when set, the local manager will trigger a Cloud Run Job execution
# automatically after uploading job artifacts to GCS (no manual worker run).
OMEGA_CLOUD_RUN_JOB = os.environ.get("OMEGA_CLOUD_RUN_JOB", "").strip()
OMEGA_CLOUD_RUN_REGION = os.environ.get("OMEGA_CLOUD_RUN_REGION", "us-central1").strip() or "us-central1"
OMEGA_CLOUD_PROJECT = os.environ.get("OMEGA_CLOUD_PROJECT", "").strip()

# Style Map
STYLE_MAP = {
    "Joyce Meyer": "RUV_BOX",
    "Praise": "OMEGA_MODERN", 
    "News": "RUV_BOX",
    "CBN": "RUV_BOX",
    "700": "RUV_BOX",
    "DEFAULT": "OMEGA_MODERN"
}

# Burn Method Map
# Maps internal style names to burn methods ("RuvBox" = Direct ASS, "Apple" = Overlay)
BURN_METHOD_MAP = {
    "Classic": "RuvBox",   # DIRECT: Use ASS Burn for Classic
    "RuvBox": "RuvBox",    # DIRECT: Use ASS Burn for RuvBox
    "Modern": "Default",
    # Folder / legacy aliases
    "Modern_Look": "Default",
    "OMEGA_MODERN": "Default",
    "Apple": "Apple",
    "Apple_TV": "Apple",
    "AppleTV_IS": "Apple",
    "DEFAULT": "RuvBox"
}

# --- PUBLISH (BROADCAST) ---
# Defaults target broadcast-friendly 1080p H.264 output.
# Override via env if needed (e.g. OMEGA_VIDEO_BITRATE=18M).
PUBLISH_X264_PRESET = os.environ.get("OMEGA_X264_PRESET", "medium")
PUBLISH_VIDEO_BITRATE = os.environ.get("OMEGA_VIDEO_BITRATE", "15M")
PUBLISH_VIDEO_MAXRATE = os.environ.get("OMEGA_VIDEO_MAXRATE", "18M")
PUBLISH_VIDEO_BUFSIZE = os.environ.get("OMEGA_VIDEO_BUFSIZE", "30M")
PUBLISH_AUDIO_CODEC = os.environ.get("OMEGA_AUDIO_CODEC", "copy")
