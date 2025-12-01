import os
import shutil
from pathlib import Path

# --- BASE PATHS ---
BASE_DIR = Path(os.getcwd())
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

# Ensure directories exist
for d in [INBOX_DIR, VAULT_DATA, VAULT_VIDEOS, PROXIES_DIR, EDITOR_DIR, TRANSLATED_DONE_DIR, SRT_DIR, VIDEO_DIR, ERROR_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- BINARIES ---
def get_binary(name, default):
    path = shutil.which(name)
    return path if path else default

FFMPEG_BIN = get_binary("ffmpeg", "ffmpeg")
WHISPER_BIN = get_binary("whisperx", "whisperx")

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
    "OMEGA_MODERN": "Default",
    "Apple": "Apple",
    "DEFAULT": "RuvBox"
}
