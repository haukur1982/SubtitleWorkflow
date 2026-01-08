import os
import shutil
import time
import logging
import site
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
for d in [
    INBOX_DIR,
    VAULT_DATA,
    VAULT_VIDEOS,
    PROXIES_DIR,
    EDITOR_DIR,
    TRANSLATED_DONE_DIR,
    SRT_DIR,
    VIDEO_DIR,
    ERROR_DIR,
    INBOX_DIR / "03_REMOTE_REVIEW" / "Classic",
    INBOX_DIR / "03_REMOTE_REVIEW" / "Modern_Look",
    INBOX_DIR / "03_REMOTE_REVIEW" / "Apple_TV",
]:
    _safe_mkdir(d)

# --- BINARIES ---
def get_binary(name, default):
    path = shutil.which(name)
    if path:
        return path
    try:
        user_bin = Path(site.getuserbase()) / "bin" / name
        if user_bin.exists():
            return str(user_bin)
    except Exception:
        pass
    return default

FFMPEG_BIN = get_binary("ffmpeg", "ffmpeg")
FFPROBE_BIN = get_binary("ffprobe", "ffprobe")

# WhisperX lives in Python 3.9's user bin (not the venv's Python 3.11)
# Priority: OMEGA_WHISPER_BIN env var > explicit Python 3.9 path > generic lookup
_WHISPERX_PY39 = Path.home() / "Library" / "Python" / "3.9" / "bin" / "whisperx"
WHISPER_BIN = (
    os.environ.get("OMEGA_WHISPER_BIN", "").strip()
    or str(BASE_DIR / "scripts" / "whisperx_wrapper.py")
)

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

def disk_space_available(min_gb: float = 50.0) -> tuple[bool, float]:
    """
    Check if sufficient disk space is available for batch processing.
    
    Returns (is_sufficient, available_gb).
    Checks the DELIVERY_DIR path (usually where output goes).
    """
    try:
        target = DELIVERY_DIR.resolve() if DELIVERY_DIR.is_symlink() else DELIVERY_DIR
        stat = shutil.disk_usage(str(target))
        available_gb = stat.free / (1024**3)
        return (available_gb >= min_gb, available_gb)
    except Exception as e:
        logger.warning("Could not check disk space: %s", e)
        return (False, 0.0)

# --- SETTINGS ---
# Whisper
WHISPER_MODEL = os.environ.get("OMEGA_WHISPER_MODEL", "large-v3").strip() or "large-v3"
# float32 is safest on macOS; override via OMEGA_WHISPER_COMPUTE if needed.
WHISPER_COMPUTE = os.environ.get("OMEGA_WHISPER_COMPUTE", "float32").strip() or "float32"
WHISPER_DEVICE = os.environ.get("OMEGA_WHISPER_DEVICE", "cpu").strip() or "cpu"

# Gemini Models
# ⚠️ CRITICAL: NEVER USE GEMINI 1.5. IT IS BANNED.
MODEL_TRANSLATOR = "gemini-3-pro-preview"  # The "Brain" for Translation
MODEL_EDITOR = "gemini-3-pro-preview"      # The "Brain" for Review
MODEL_POLISH = os.environ.get("OMEGA_MODEL_POLISH", MODEL_TRANSLATOR).strip() or MODEL_TRANSLATOR
MODEL_ASSISTANT = "gemini-3-flash-preview"   # Officially verified Gemini 3 Flash string
GEMINI_LOCATION = "global"                 # Required for Preview models

# --- CLOUD ARTIFACTS (GCS) ---
# Store per-job JSON artifacts (skeleton/termbook/translation/approved/checkpoints) in GCS.
# This enables a cloud-first translation/editor pipeline while keeping heavy video work local.
OMEGA_JOBS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
OMEGA_JOBS_PREFIX = os.environ.get("OMEGA_JOBS_PREFIX", "jobs")
OMEGA_CLOUD_POLISH_MODE = os.environ.get("OMEGA_CLOUD_POLISH_MODE", "review").strip().lower()
OMEGA_CLOUD_MUSIC_DETECT = os.environ.get("OMEGA_CLOUD_MUSIC_DETECT", "1").strip().lower() in {"1", "true", "yes", "on"}

# Optional: when set, the local manager will trigger a Cloud Run Job execution
# automatically after uploading job artifacts to GCS (no manual worker run).
OMEGA_CLOUD_RUN_JOB = os.environ.get("OMEGA_CLOUD_RUN_JOB", "").strip()
OMEGA_CLOUD_RUN_REGION = os.environ.get("OMEGA_CLOUD_RUN_REGION", "us-central1").strip() or "us-central1"
OMEGA_CLOUD_PROJECT = os.environ.get("OMEGA_CLOUD_PROJECT", "sermon-translator-system").strip() or "sermon-translator-system"

# --- ASSEMBLYAI TRANSCRIPTION ---
# API key for AssemblyAI (get from https://www.assemblyai.com)
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
# Transcriber backend: "assemblyai" (fast, cloud) or "whisperx" (local, slower)
OMEGA_TRANSCRIBER = os.environ.get("OMEGA_TRANSCRIBER", "assemblyai").strip().lower()
# Additional words to boost for transcription accuracy (comma-separated)
ASSEMBLYAI_WORD_BOOST = os.environ.get("ASSEMBLYAI_WORD_BOOST", "").strip()
# Word boost weight: "low", "default", or "high"
ASSEMBLYAI_BOOST_WEIGHT = os.environ.get("ASSEMBLYAI_BOOST_WEIGHT", "high").strip()
# Enable speaker diarization (multi-speaker detection) - default True
ASSEMBLYAI_SPEAKER_LABELS = os.environ.get("ASSEMBLYAI_SPEAKER_LABELS", "1").strip().lower() in {"1", "true", "yes", "on"}

# --- ANTHROPIC (CLAUDE) ---
# API key for Claude Phase 3 Polish Editor (get from https://console.anthropic.com)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Enable Claude for Phase 3 Polish (requires ANTHROPIC_API_KEY)
OMEGA_CLAUDE_POLISH = os.environ.get("OMEGA_CLAUDE_POLISH", "1").strip().lower() in {"1", "true", "yes", "on"}
# Claude model to use for polish
OMEGA_CLAUDE_MODEL = os.environ.get("OMEGA_CLAUDE_MODEL", "claude-opus-4-5-20251101").strip()

# --- DEMUCS VOCAL EXTRACTION ---
# Enable Demucs to remove background music before transcription (requires M2 Mac)
# This prevents AssemblyAI from transcribing background lyrics
OMEGA_DEMUCS_ENABLED = os.environ.get("OMEGA_DEMUCS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
# Demucs model: "htdemucs" (fast, ~8-12 min/hour) or "htdemucs_ft" (slower, best quality)
OMEGA_DEMUCS_MODEL = os.environ.get("OMEGA_DEMUCS_MODEL", "htdemucs").strip()
# Device: "mps" (Apple Silicon GPU), "cpu", or "cuda" (Nvidia)
OMEGA_DEMUCS_DEVICE = os.environ.get("OMEGA_DEMUCS_DEVICE", "mps").strip()

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

# --- DELIVERY PROFILES (MEDIA ENCODING) ---
# Select the appropriate profile when burning subtitles based on client requirements.
# Use dashboard dropdown or set DEFAULT_DELIVERY_PROFILE for automatic selection.

DELIVERY_PROFILES = {
    "broadcast_hevc": {
        "name": "Broadcast HEVC (Fast)",
        "encoder": "hevc_videotoolbox",
        "bitrate": "12M",
        "maxrate": "15M",
        "bufsize": "24M",
        "extra_args": ["-tag:v", "hvc1"],  # QuickTime/Apple compatibility
        "description": "Fast hardware encoding (9x), modern compatibility",
        "speed": "9x",
        "compatibility": "Modern (2017+)"
    },
    "broadcast_h264": {
        "name": "Broadcast H.264 (Universal)",
        "encoder": "libx264",
        "preset": "slow",
        "crf": "18",
        "maxrate": "18M",
        "bufsize": "36M",
        "extra_args": ["-profile:v", "high", "-level", "4.1"],
        "description": "Software encoding, plays on everything",
        "speed": "1x",
        "compatibility": "Universal"
    },
    "web": {
        "name": "Web Optimized",
        "encoder": "hevc_videotoolbox",
        "bitrate": "6M",
        "maxrate": "8M",
        "bufsize": "12M",
        "extra_args": ["-tag:v", "hvc1"],
        "description": "Smaller files for streaming/upload",
        "speed": "9x",
        "compatibility": "Modern"
    },
    "archive": {
        "name": "Archive (Master)",
        "encoder": "libx264",
        "preset": "veryslow",
        "crf": "16",
        "maxrate": "25M",
        "bufsize": "50M",
        "extra_args": ["-profile:v", "high"],
        "description": "Highest quality, for archival",
        "speed": "0.3x",
        "compatibility": "Universal"
    },
    "universal": {
        "name": "Universal (Safe Default)",
        "encoder": "libx264",
        "preset": "medium",
        "crf": "20",
        "maxrate": "12M",
        "bufsize": "24M",
        "extra_args": ["-profile:v", "main", "-level", "4.0"],
        "description": "Balanced speed/quality, maximum compatibility",
        "speed": "2x",
        "compatibility": "Maximum"
    }
}

# Default profile when no specific profile is selected
DEFAULT_DELIVERY_PROFILE = os.environ.get("OMEGA_DELIVERY_PROFILE", "broadcast_hevc").strip()

# --- CLIENT PATTERNS ---
# Auto-detect client from filename. Keys are lowercase patterns to match, values are display names.
# Matched in order, first match wins. Add your clients here.
CLIENT_PATTERNS = {
    "intouch": "In Touch",
    "in_touch": "In Touch",
    "timessquare": "Times Square Church",
    "times_square": "Times Square Church",
    "tsc": "Times Square Church",
    "charles_stanley": "Charles Stanley",
    "charlesstanley": "Charles Stanley",
    "benny_hinn": "Benny Hinn",
    "bennyhinn": "Benny Hinn",
    "joyce_meyer": "Joyce Meyer",
    "joycemeyer": "Joyce Meyer",
    "i2": "Omega TV",  # Internal production codes
    "gospel": "Gospel",
    # Add more patterns as needed
}

# --- CLIENT DEFAULTS ---
# Default turnaround time (in days) for each client
# Delivery template tokens:
#   {client} - Client name
#   {title} - Extracted from original filename
#   {date_YYYY_MM_DD} - 2024_12_28
#   {date_MM-DD-YY} - 12-28-24
#   {date_DD_month_YYYY} - 28_december_2024
CLIENT_DEFAULTS = {
    "In Touch": {
        "due_date_days": 7,
        "delivery_template": "InTouch_{title}_{date_YYYY_MM_DD}",
        "delivery_method": "folder",  # 'folder', 'email', 'ftp'
        "delivery_target": "4_DELIVERY/InTouch"
    },
    "Times Square Church": {
        "due_date_days": 5,
        "delivery_template": "TSC_{title}_{date_MM-DD-YY}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/TSC"
    },
    "Charles Stanley": {
        "due_date_days": 7,
        "delivery_template": "CharlesStanley_{date_YYYY_MM_DD}_{title}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/CharlesStanley"
    },
    "Benny Hinn": {
        "due_date_days": 10,
        "delivery_template": "BennyHinn_{title}_{date_MM-DD-YY}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/BennyHinn"
    },
    "Joyce Meyer": {
        "due_date_days": 7,
        "delivery_template": "JoyceMeyer_{date_YYYY_MM_DD}_{title}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/JoyceMeyer"
    },
    "Omega TV": {
        "due_date_days": 3,
        "delivery_template": "OmegaTV_{title}_{date_DD_month_YYYY}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/OmegaTV"
    },
    "Gospel": {
        "due_date_days": 7,
        "delivery_template": "Gospel_{date_YYYY_MM_DD}_{title}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/Gospel"
    },
    "unknown": {
        "due_date_days": 7,
        "delivery_template": "{title}_{date_YYYY_MM_DD}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY"
    },
}

# --- LEGACY PUBLISH SETTINGS (for backwards compatibility) ---
PUBLISH_X264_PRESET = os.environ.get("OMEGA_X264_PRESET", "medium")
PUBLISH_VIDEO_BITRATE = os.environ.get("OMEGA_VIDEO_BITRATE", "15M")
PUBLISH_VIDEO_MAXRATE = os.environ.get("OMEGA_VIDEO_MAXRATE", "18M")
PUBLISH_VIDEO_BUFSIZE = os.environ.get("OMEGA_VIDEO_BUFSIZE", "30M")

