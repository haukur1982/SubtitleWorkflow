import json
import shutil
from pathlib import Path
import sys
import os

# Add current dir to path to import local modules
sys.path.append(os.getcwd())

import omega_db
import cloud_brain

BASE_DIR = Path("/Users/haukur/SubtitleWorkflow")
ARCHIVE_SKELETON = BASE_DIR / "6_ARCHIVE/2025-11/HOP_2913_INT57/HOP_2913_INT57_SKELETON.json"
TEST_SKELETON = BASE_DIR / "2_READY_FOR_CLOUD/HOP_2913_INT57_TEST_SKELETON.json"
AUDIO_SRC = BASE_DIR / "2_READY_FOR_CLOUD/HOP_2913_INT57.mp3"
AUDIO_DST = BASE_DIR / "2_READY_FOR_CLOUD/HOP_2913_INT57_TEST.mp3"

# Set Credentials explicitly
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(BASE_DIR / "service_account.json")

# 1. Create Test Skeleton
if not ARCHIVE_SKELETON.exists():
    print("❌ Archive skeleton not found")
    sys.exit(1)

with open(ARCHIVE_SKELETON, "r") as f:
    data = json.load(f)

test_segments = []
for seg in data["segments"]:
    if 60 <= seg["id"] <= 100:
        test_segments.append(seg)

payload = {
    "needs_human_review": False,
    "segments": test_segments
}

with open(TEST_SKELETON, "w") as f:
    json.dump(payload, f, indent=2)
print(f"✅ Created test skeleton with {len(test_segments)} segments.")

# 2. Copy Audio
if not AUDIO_SRC.exists():
    AUDIO_SRC = BASE_DIR / "6_ARCHIVE/2025-11/HOP_2913_INT57/HOP_2913_INT57.mp3"

if AUDIO_SRC.exists():
    if not AUDIO_DST.exists():
        shutil.copy(AUDIO_SRC, AUDIO_DST)
        print(f"✅ Copied audio to {AUDIO_DST}")
    else:
        print(f"ℹ️ Audio already exists at {AUDIO_DST}")
else:
    print("❌ Audio source not found!")
    sys.exit(1)

# 3. Init Job in DB
omega_db.update("HOP_2913_INT57_TEST", stage="CLOUD_READY", status="Ready for Test", progress=0)

# 4. Run Translation
print("🚀 Starting Translation...")
try:
    cloud_brain.process_translation(TEST_SKELETON)
    print("✅ Translation function returned.")
except Exception as e:
    print(f"❌ Translation failed: {e}")
