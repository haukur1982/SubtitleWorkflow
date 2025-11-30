import os
import shutil
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(os.getcwd())
ARCHIVE_DIR = BASE_DIR / "6_ARCHIVE"
ARCHIVE_DIR.mkdir(exist_ok=True)

DIRS = {
    "INBOX": BASE_DIR / "1_INBOX",
    "READY": BASE_DIR / "2_READY_FOR_CLOUD",
    "TRANSLATED": BASE_DIR / "3_TRANSLATED_DONE",
    "FINAL": BASE_DIR / "4_FINAL_OUTPUT",
    "DELIVERABLES": BASE_DIR / "5_DELIVERABLES"
}

def get_archive_folder(stem):
    now = datetime.now()
    folder_name = now.strftime("%Y-%m")
    month_dir = ARCHIVE_DIR / folder_name
    month_dir.mkdir(exist_ok=True)
    project_dir = month_dir / stem
    project_dir.mkdir(exist_ok=True)
    return project_dir

def cleanup():
    print("🧹 Starting Aggressive Workspace Cleanup...")
    
    # 1. Archive ALL Deliverables
    for mp4 in DIRS["DELIVERABLES"].glob("*.mp4"):
        stem = mp4.stem.replace("_SUBBED", "") # Handle both cases
        print(f"📦 Archiving Deliverable: {stem}")
        target_dir = get_archive_folder(stem)
        shutil.move(str(mp4), str(target_dir / mp4.name))
        
        # Try to find related source/intermediate files for this stem
        # Move Source Video
        for source_dir in [DIRS["INBOX"] / "processed", DIRS["INBOX"] / "AUTO_PILOT" / "processed", DIRS["INBOX"] / "VIP_REVIEW" / "processed"]:
            for ext in ['.mp4', '.mov', '.mkv', '.mp3']:
                f = source_dir / f"DONE_{stem}{ext}"
                if f.exists(): shutil.move(str(f), str(target_dir / f.name))
                f = source_dir / f"{stem}{ext}"
                if f.exists(): shutil.move(str(f), str(target_dir / f.name))

    # 2. Archive Orphaned Final SRTs
    for srt in DIRS["FINAL"].glob("DONE_*.srt"):
        stem = srt.name.replace("DONE_", "").replace(".srt", "")
        print(f"📦 Archiving Orphaned SRT: {stem}")
        target_dir = get_archive_folder(stem)
        shutil.move(str(srt), str(target_dir / srt.name))
        
        # Move related normalized JSON if exists
        norm = DIRS["FINAL"] / f"{stem}_normalized.json"
        if norm.exists(): shutil.move(str(norm), str(target_dir / norm.name))

    # 3. Archive Stuck Cloud Files (Older than 1 hour?)
    # For now, let's just move the specific ones we saw or all of them?
    # User said "clean things up".
    for f in DIRS["READY"].glob("*"):
        if f.is_dir(): continue
        if f.name == "processed": continue
        if f.name.startswith("."): continue # Ignore .DS_Store
        
        print(f"📦 Archiving Stuck Cloud File: {f.name}")
        # We don't know the stem easily if it's weird, but let's try
        stem = f.stem.replace("_SKELETON", "")
        target_dir = get_archive_folder(stem)
        shutil.move(str(f), str(target_dir / f.name))

    print("✨ Aggressive Cleanup Complete.")

if __name__ == "__main__":
    cleanup()
