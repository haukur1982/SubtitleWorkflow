import os
import time
import subprocess
import json
import shutil
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(os.getcwd())
INBOX = BASE_DIR / "1_INBOX"
OUTBOX = BASE_DIR / "2_READY_FOR_CLOUD"
ERROR_DIR = BASE_DIR / "99_ERRORS"
ERROR_DIR.mkdir(exist_ok=True)

# Create Subfolders for Humans
VIP_DIR = INBOX / "VIP_REVIEW"
AUTO_DIR = INBOX / "AUTO_PILOT"
VIP_DIR.mkdir(parents=True, exist_ok=True)
AUTO_DIR.mkdir(parents=True, exist_ok=True)

EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".mov", ".mkv", ".mpg"}  # Accept MPEG program streams too
WHISPER_BIN = BASE_DIR / "venv" / "bin" / "whisperx"
from lock_manager import ProcessLock
import system_health

import omega_db

def get_duration(file_path):
    """Returns duration in seconds using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return float(res.stdout.strip()) if res.stdout else 0
    except:
        return 0

def process_file(file_path, needs_review):
    print(f"⚡️ Detected ({'VIP' if needs_review else 'AUTO'}): {file_path.name}")
    stem = file_path.stem
    
    # Initialize Job in DB
    omega_db.update(stem, stage="INBOX", status="Detected", progress=0.0)
    
    # Check file size
    file_size_mb = file_path.stat().st_size / (1024 * 1024)
    print(f"   File size: {file_size_mb:.1f} MB")
    
    mp3_path = OUTBOX / f"{stem}.mp3"
    
    # 1. Extract Audio (IMPROVED QUALITY to 128k)
    if not mp3_path.exists():
        print(f"   Extracting audio (128k)... This may take a while for large files.")
        omega_db.update(stem, stage="AUDIO", status="Extracting Audio", progress=10.0)
        
        try:
            # Get duration first for progress calculation
            total_duration = get_duration(file_path)
            if total_duration == 0: total_duration = 1 # Prevent div by zero
            
            process = subprocess.Popen([
                "ffmpeg", "-i", str(file_path), 
                "-vn", "-ar", "16000", "-ac", "1", "-b:a", "128k", 
                str(mp3_path), "-y", "-progress", "pipe:1"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Parse FFmpeg progress
            while True:
                line = process.stdout.readline()
                if not line: break
                if "out_time_us=" in line:
                    try:
                        us = int(line.split("=")[1])
                        current_sec = us / 1000000
                        prog = min(30.0, 10.0 + (current_sec / total_duration) * 20.0) # 10-30%
                        omega_db.update(stem, progress=prog)
                    except: pass
            
            process.wait()
            if process.returncode != 0:
                print(f"   ❌ FFmpeg failed")
                omega_db.update(stem, status="Error: Audio Extraction Failed", progress=0)
                shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
                return
                
            print(f"   ✅ Audio extracted successfully")
            
        except Exception as e:
            print(f"   ❌ Error during audio extraction: {e}")
            omega_db.update(stem, status=f"Error: {str(e)[:50]}", progress=0)
            shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
            return

    # --- VERIFICATION STEP (ALWAYS RUN) ---
    if mp3_path.exists():
        total_duration = get_duration(file_path)
        if total_duration == 0: total_duration = 1
        
        audio_duration = get_duration(mp3_path)
        diff = abs(total_duration - audio_duration)
        if diff > 5.0: # Allow 5s tolerance
            print(f"   ❌ VERIFICATION FAILED: Audio is {audio_duration}s, Video is {total_duration}s")
            omega_db.update(stem, status="Error: Audio Verification Failed", progress=0)
            os.remove(mp3_path) # Delete bad file
            shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
            return
        print(f"   ✅ Verified: Audio matches video length ({audio_duration:.1f}s)")
        omega_db.update(stem, progress=30.0)
    
    # 2. Run WhisperX
    print(f"   Running WhisperX transcription...")
    omega_db.update(stem, stage="TRANSCRIPTION", status="Transcribing (WhisperX)", progress=35.0)
    
    try:
        # We can't easily get real-time progress from WhisperX CLI without modifying it,
        # so we'll simulate it or just set a "working" state.
        # Ideally, we'd use the python API for granular updates, but CLI is safer for memory.
        subprocess.run([
            str(WHISPER_BIN), str(mp3_path),
            "--model", "medium.en",
            "--output_dir", str(BASE_DIR),
            "--output_format", "json",
            "--compute_type", "int8",
            "--device", "cpu"
        ], stdout=subprocess.DEVNULL, timeout=7200)
        
        print(f"   ✅ Transcription complete")
        omega_db.update(stem, progress=60.0)
        
    except subprocess.TimeoutExpired:
        print(f"   ❌ Timeout: Transcription took >2 hours.")
        omega_db.update(stem, status="Error: Transcription Timeout", progress=0)
        shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
        return
    except Exception as e:
        print(f"   ❌ Error during transcription: {e}")
        omega_db.update(stem, status=f"Error: {str(e)[:50]}", progress=0)
        shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
        return

    # 3. Clean, Format & TAG
    raw_json_path = BASE_DIR / f"{stem}.json"
    
    if raw_json_path.exists():
        with open(raw_json_path, "r") as f:
            data = json.load(f)
        
        clean_data = []
        for i, segment in enumerate(data["segments"]):
            clean_data.append({
                "id": i + 1,
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"].strip()
            })
        
        # --- THE VIP TAG ---
        # We wrap the list in a dict to tell the Brain this needs a human
        payload = {
            "needs_human_review": needs_review,
            "segments": clean_data
        }
        
        final_json_path = OUTBOX / f"{stem}_SKELETON.json"
        with open(final_json_path, "w") as f:
            json.dump(payload, f, indent=2)
            
        os.remove(raw_json_path)
        
        # Move original video to processed (only once)
        processed_dir = file_path.parent / "processed"
        processed_dir.mkdir(exist_ok=True)
        if file_path.exists():
            shutil.move(str(file_path), str(processed_dir / f"DONE_{file_path.name}"))
        
        print(f"   ✅ Skeleton JSON created: {final_json_path.name}")
        omega_db.update(stem, stage="CLOUD_READY", status="Ready for Translation", progress=60.0)

# --- THE WATCHER ---
if __name__ == "__main__":
    with ProcessLock("auto_skeleton"):
        print(f"👀 Ear Active. Monitoring VIP & AUTO folders...")
        while True:
            system_health.update_heartbeat("auto_skeleton")
            # Scan VIP Folder
            for file_path in VIP_DIR.iterdir():
                if file_path.suffix.lower() in EXTENSIONS and not file_path.name.startswith("DONE_"):
                    if file_path.is_file(): 
                        if system_health.check_disk_space(min_gb=10):
                            process_file(file_path, needs_review=True)
                        else:
                            print("   ⚠️ Low Disk Space. Skipping VIP file.")
                    
            # Scan AUTO Folder
            for file_path in AUTO_DIR.iterdir():
                if file_path.suffix.lower() in EXTENSIONS and not file_path.name.startswith("DONE_"):
                    if file_path.is_file(): 
                        if system_health.check_disk_space(min_gb=10):
                            process_file(file_path, needs_review=False)
                        else:
                            print("   ⚠️ Low Disk Space. Skipping AUTO file.")
                    
            time.sleep(2)
