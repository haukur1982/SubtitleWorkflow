import os
import shutil
from pathlib import Path
import omega_db

BASE_DIR = Path("/Users/haukur/SubtitleWorkflow")
INBOX = BASE_DIR / "2_READY_FOR_CLOUD"
ARCHIVE = BASE_DIR / "6_ARCHIVE/2025-11/HOP_2913_INT57"
TEST_FILES = [
    INBOX / "HOP_2913_INT57_TEST_SKELETON.json",
    INBOX / "HOP_2913_INT57_TEST.mp3",
    BASE_DIR / "3_TRANSLATED_PENDING_REVIEW/HOP_2913_INT57_TEST_TRANSLATED.json"
]

# 1. Cleanup
print("🧹 Cleaning up test files...")
for f in TEST_FILES:
    if f.exists():
        f.unlink()
        print(f"   Deleted {f.name}")

# 2. Trigger Full Run
stem = "HOP_2913_INT57"
print(f"🚀 Triggering full re-run for {stem}...")

# Reset DB
omega_db.update(stem, stage="CLOUD_READY", status="Reset for Fix", progress=0)

# Move Skeleton to Inbox
skeleton_src = ARCHIVE / f"{stem}_SKELETON.json"
skeleton_dst = INBOX / f"{stem}_SKELETON.json"

if skeleton_src.exists():
    shutil.copy(skeleton_src, skeleton_dst) # Copy instead of move to keep archive safe
    print(f"   ✅ Copied Skeleton to Inbox: {skeleton_dst.name}")
else:
    print(f"   ❌ Skeleton not found in Archive: {skeleton_src}")

# Ensure Audio is in Inbox
audio_src = ARCHIVE / f"{stem}.mp3"
audio_dst = INBOX / f"{stem}.mp3"
if not audio_dst.exists():
    if audio_src.exists():
        shutil.copy(audio_src, audio_dst)
        print(f"   ✅ Restored Audio to Inbox")
    else:
        print(f"   ⚠️ Audio not found in Archive or Inbox!")

print("✅ Ready for Cloud Brain.")
