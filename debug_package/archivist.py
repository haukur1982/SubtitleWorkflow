import os
import time
import shutil
from pathlib import Path
from datetime import datetime
from lock_manager import ProcessLock

# --- CONFIGURATION ---
BASE_DIR = Path(os.getcwd())
INBOX = BASE_DIR / "1_INBOX"
READY_DIR = BASE_DIR / "2_READY_FOR_CLOUD"
TRANSLATED_DIR = BASE_DIR / "3_TRANSLATED_DONE"
FINAL_DIR = BASE_DIR / "4_FINAL_OUTPUT"
DELIVERABLES_DIR = BASE_DIR / "5_DELIVERABLES"
ARCHIVE_DIR = BASE_DIR / "6_ARCHIVE"

ARCHIVE_DIR.mkdir(exist_ok=True)

def get_archive_folder(stem):
    """Creates a YYYY-MM folder in the archive."""
    now = datetime.now()
    folder_name = now.strftime("%Y-%m")
    month_dir = ARCHIVE_DIR / folder_name
    month_dir.mkdir(exist_ok=True)
    
    project_dir = month_dir / stem
    project_dir.mkdir(exist_ok=True)
    return project_dir

def find_source_video(stem):
    """Locates the source video in INBOX or its subfolders."""
    # Check processed folders first (where they should be)
    search_paths = [
        INBOX / "processed",
        INBOX / "AUTO_PILOT" / "processed",
        INBOX / "VIP_REVIEW" / "processed",
        INBOX # In case it wasn't moved
    ]
    
    for folder in search_paths:
        if not folder.exists(): continue
        # Look for common extensions
        for ext in ['.mp4', '.mov', '.mkv', '.m4v', '.mpg', '.mp3', '.wav']:
            # Check for "DONE_" prefix and without
            candidates = [
                folder / f"DONE_{stem}{ext}",
                folder / f"{stem}{ext}"
            ]
            for c in candidates:
                if c.exists():
                    return c
    return None

def archive_project(subbed_file):
    stem = subbed_file.name.replace("_SUBBED.mp4", "")
    print(f"📦 Archiving Project: {stem}")
    
    target_dir = get_archive_folder(stem)
    
    # 1. Move Final Video
    try:
        shutil.move(str(subbed_file), str(target_dir / subbed_file.name))
        print(f"   ✅ Moved Final Video")
    except Exception as e:
        print(f"   ⚠️ Failed to move final video: {e}")
        return # Abort if we can't even move the main file

    # 2. Move Source Video
    source = find_source_video(stem)
    if source:
        try:
            shutil.move(str(source), str(target_dir / source.name))
            print(f"   ✅ Moved Source: {source.name}")
        except Exception as e:
            print(f"   ⚠️ Failed to move source: {e}")
    
    # 3. Move SRT
    srt = FINAL_DIR / f"{stem}.srt"
    if srt.exists():
        shutil.move(str(srt), str(target_dir / srt.name))
        print(f"   ✅ Moved SRT")

    # 4. Move JSONs (All stages)
    json_files = [
        READY_DIR / f"{stem}_SKELETON.json",
        TRANSLATED_DIR / f"{stem}_ICELANDIC.json",
        TRANSLATED_DIR / f"{stem}_APPROVED.json",
        FINAL_DIR / f"{stem}_normalized.json"
    ]
    for j in json_files:
        if j.exists():
            shutil.move(str(j), str(target_dir / j.name))
    print(f"   ✅ Moved JSONs")

    # 5. CLEANUP (Delete Temps)
    
    # Overlay MOV (Huge)
    overlay = DELIVERABLES_DIR / f"{stem}_overlay.mov"
    if overlay.exists():
        os.remove(overlay)
        print(f"   🗑️  Deleted Overlay (Saved Space)")
        
    # Temp MP3
    mp3 = READY_DIR / f"{stem}.mp3"
    if mp3.exists():
        os.remove(mp3)
        print(f"   🗑️  Deleted Temp MP3")
        
    print(f"   ✨ Project Archived to: {target_dir}")

def run_archivist():
    print("📚 The Archivist is Active. Scanning for completed work...")
    while True:
        # Scan for completed .mp4 files in DELIVERABLES
        # We look for files that do NOT have a corresponding lock or active write?
        # Simple check: If it ends in _SUBBED.mp4, it's a candidate.
        # But we must be sure it's done writing.
        # publisher.py writes to a temp file? No, it writes directly.
        # But publisher moves it to 5_DELIVERABLES at the very end?
        # Let's check publisher.py... 
        # It writes to OUTBOX (5_DELIVERABLES) directly. 
        # We should wait until the file hasn't been modified for a minute?
        
        for file in DELIVERABLES_DIR.glob("*_SUBBED.mp4"):
            # Check modification time. If > 2 minutes ago, assume safe to archive.
            # This prevents grabbing a file currently being burned.
            mtime = file.stat().st_mtime
            if (time.time() - mtime) > 120:
                archive_project(file)
                
        time.sleep(60)

if __name__ == "__main__":
    with ProcessLock("archivist"):
        run_archivist()
