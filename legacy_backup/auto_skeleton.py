import os
import shutil
import subprocess
import json
import time
import sys
import signal
import multiprocessing as mp
import torch
import math
from pathlib import Path
import omega_db
from lock_manager import ProcessLock
import system_health
from datetime import datetime

# --- SIGNAL HANDLER ---
def signal_handler(signum, frame):
    print(f"🛑 Received signal {signum}. Cleaning up resources...")
    if mp.get_start_method(allow_none=True) == 'fork':
        try:
            mp.semaphore_tracker._semaphore_tracker._semaphores.clear()
        except:
            pass
    sys.exit(130 + signum)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# --- MPS CHECK ---
# if torch.backends.mps.is_available():
#     print("✅ MPS enabled for transcription.")
# else:
#     print("⚠️ MPS not available. Using CPU.")
print("⚠️ MPS disabled for stability. Using CPU.")

# --- CONFIGURATION ---
BASE_DIR = Path(os.getcwd())
INBOX_ROOT = BASE_DIR / "1_INBOX"
VAULT_VIDEO = BASE_DIR / "2_VAULT" / "Videos"
VAULT_AUDIO = BASE_DIR / "2_VAULT" / "Audio"
VAULT_DATA = BASE_DIR / "2_VAULT" / "Data"
ERROR_DIR = BASE_DIR / "99_ERRORS"

# Ensure directories exist
for d in [VAULT_VIDEO, VAULT_AUDIO, VAULT_DATA, ERROR_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- WATCH FOLDERS ---
# Map folder paths to (Mode, Style)
WATCH_MAP = {
    INBOX_ROOT / "01_AUTO_PILOT" / "Classic_Look": ("AUTO", "RUV_BOX"),
    INBOX_ROOT / "01_AUTO_PILOT" / "Modern_Look": ("AUTO", "MODERN"),
    INBOX_ROOT / "02_HUMAN_REVIEW" / "Classic_Look": ("REVIEW", "RUV_BOX"),
    INBOX_ROOT / "02_HUMAN_REVIEW" / "Modern_Look": ("REVIEW", "MODERN"),
}

EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".mov", ".mkv", ".mpg", ".mpeg", ".moc"}
WHISPER_BIN = BASE_DIR / "venv" / "bin" / "whisperx"
DEMUCS_BIN = BASE_DIR / "venv" / "bin" / "demucs"

def get_ffmpeg_binary():
    candidates = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
    for path in candidates:
        if shutil.which(path): return path
    return "ffmpeg"

FFMPEG_BIN = get_ffmpeg_binary()

def get_duration(file_path):
    """Returns duration in seconds using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return float(res.stdout.strip()) if res.stdout else 0
    except:
        return 0

def process_file(file_path, mode, style):
    """
    1. Move video to Vault
    2. Extract Audio to Vault
    3. Transcribe -> Skeleton JSON to Vault
    4. Update DB
    """
    stem = file_path.stem
    print(f"🚀 Processing: {file_path.name} | Mode: {mode} | Style: {style}")
    
    # 1. Move to Vault
    vault_video_path = VAULT_VIDEO / file_path.name
    
    # CRITICAL FIX: Only move if source is NOT the destination
    if file_path.resolve() != vault_video_path.resolve():
        if vault_video_path.exists():
            # Overwrite to ensure latest version
            os.remove(vault_video_path)
        shutil.move(str(file_path), str(vault_video_path))
        print(f"   📦 Moved to Vault: {vault_video_path}")
    else:
        print(f"   📦 File already in Vault: {vault_video_path}")
    
    # Init Job in DB
    meta = {
        "original_filename": file_path.name,
        "mode": mode,
        "style": style,
        "ingest_time": datetime.now().isoformat()
    }
    omega_db.update(stem, stage="INGEST", status="Processing Audio", progress=10.0, meta=meta)

    # 2. Extract Audio
    audio_path = VAULT_AUDIO / f"{stem}.wav"
    
    # Check if audio needs extraction
    if not audio_path.exists():
        print(f"   🔊 Extracting Audio...")
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(vault_video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    
    # 3. Transcribe (WhisperX)
    print(f"   📝 Transcribing...")
    omega_db.update(stem, status="Transcribing", progress=20.0)
    
    output_dir = VAULT_DATA
    cmd = [
        str(WHISPER_BIN),
        str(audio_path),
        "--model", "large-v3",
        "--language", "en",
        "--output_dir", str(output_dir),
        "--output_format", "json",
        "--compute_type", "int8", # Optimized for memory
        "--batch_size", "4"       # Reduce memory pressure
    ]
    
    # Force CPU for stability
    device = "cpu"
    cmd.extend(["--device", "cpu"])

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"   ⚠️ WhisperX Failed on {device}: {e}")
        if device == "mps":
             print(f"   🔄 Retrying with CPU...")
             cmd = [c for c in cmd if c != "mps"] + ["cpu"] # Replace mps with cpu
             # Actually, the cmd construction above appended --device mps. 
             # We need to rebuild it cleanly.
             cmd = [
                str(WHISPER_BIN),
                str(audio_path),
                "--model", "large-v3",
                "--language", "en",
                "--output_dir", str(output_dir),
                "--output_format", "json",
                "--compute_type", "int8",
                "--batch_size", "4",
                "--device", "cpu"
            ]
             subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
             raise e

    # Rename output to _SKELETON.json
    whisper_json = output_dir / f"{stem}.json"
    skeleton_path = VAULT_DATA / f"{stem}_SKELETON.json"
    
    if whisper_json.exists():
        # Load and clean
        with open(whisper_json, "r") as f:
            data = json.load(f)
            
        # Basic cleaning (similar to before)
        segments = []
        for seg in data.get("segments", []):
            segments.append({
                "id": seg.get("id"),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "text": seg.get("text", "").strip()
            })
            
        payload = {
            "file": stem,
            "mode": mode,
            "style": style,
            "segments": segments
        }
        
        with open(skeleton_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            
        whisper_json.unlink() # Clean up raw
        print(f"   ✅ Skeleton Saved: {skeleton_path.name}")
        omega_db.update(stem, stage="TRANSLATING", status="Ready for Cloud", progress=30.0)
    else:
        raise Exception("WhisperX did not produce JSON output")

def _run_watcher():
    print("👀 Watchdog Active. Monitoring 1_INBOX subfolders...")
    
    # --- RESUME CAPABILITY ---
    # Check for videos in Vault that don't have a Skeleton JSON
    print(f"🔎 Checking Vault for unfinished jobs in: {VAULT_VIDEO}")
    if not VAULT_VIDEO.exists():
        print(f"   ❌ Vault directory does not exist!")
    for video_file in VAULT_VIDEO.iterdir():
        print(f"   Found in Vault: {video_file.name}")
        if video_file.name.startswith("."): continue
        if video_file.suffix.lower() in EXTENSIONS:
            stem = video_file.stem
            skeleton = VAULT_DATA / f"{stem}_SKELETON.json"
            done_skeleton = VAULT_DATA / f"{stem}_SKELETON_DONE.json"
            
            if not skeleton.exists() and not done_skeleton.exists():
                print(f"   🔄 Resuming unfinished job: {video_file.name}")
                # We need to know Mode/Style. We can check DB or default to AUTO/RUV_BOX
                # Or we can store metadata in a sidecar file?
                # For now, let's check DB.
                job = omega_db.get_job(stem)
                if job:
                    meta = job.get("meta", {})
                    mode = meta.get("mode", "AUTO")
                    style = meta.get("style", "RUV_BOX")
                else:
                    mode = "AUTO"
                    style = "RUV_BOX"
                
                try:
                    process_file(video_file, mode, style)
                except Exception as e:
                    print(f"   ❌ Error resuming {video_file.name}: {e}")

    while True:
        system_health.update_heartbeat("auto_skeleton")
        
        # Scan all watch folders
        for folder, (mode, style) in WATCH_MAP.items():
            if not folder.exists(): continue
            
            # Look for video files
            for file_path in folder.iterdir():
                if file_path.name.startswith("."): continue
                if file_path.suffix.lower() in EXTENSIONS:
                    try:
                        # Wait for file stability
                        initial_size = file_path.stat().st_size
                        time.sleep(1)
                        if file_path.stat().st_size != initial_size:
                            continue
                            
                        process_file(file_path, mode, style)
                    except Exception as e:
                        print(f"   ❌ Error processing {file_path.name}: {e}")
                        # Only move if it still exists in INBOX (meaning move to Vault failed)
                        if file_path.exists():
                            shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
                        omega_db.update(file_path.stem, status=f"Error: {e}", progress=0)
        
        time.sleep(2)

# if __name__ == "__main__":
#     with ProcessLock("auto_skeleton"):
#         _run_watcher()

if __name__ == "__main__":
    with ProcessLock("auto_skeleton"):
        _run_watcher()
