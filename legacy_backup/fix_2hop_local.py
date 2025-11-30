import json
import shutil
from pathlib import Path
import omega_db

BASE_DIR = Path("/Users/haukur/SubtitleWorkflow")
APPROVED_JSON = BASE_DIR / "3_TRANSLATED_DONE/2HOP_2913_INT57_RUVBOX_APPROVED.json"
FINAL_OUTPUT_DIR = BASE_DIR / "4_FINAL_OUTPUT"
BURN_QUEUE = BASE_DIR / "5_BURN_QUEUE"

if not APPROVED_JSON.exists():
    print(f"❌ File not found: {APPROVED_JSON}")
    exit(1)

print(f"🔧 Fixing {APPROVED_JSON.name}...")

with open(APPROVED_JSON, "r") as f:
    data = json.load(f)

# 1. Remove Choir Lyrics (IDs 67 - 140 approx, based on previous checks)
# I will use the timestamps to be safe: 600s to 1250s is roughly the song
# Or just look for specific IDs if I'm sure.
# From previous view_file, IDs 67 to 140 cover the song.
# ID 67 start: 619.927
# ID 140 start: 1248.947 (Gloria)
# ID 141 start: 1257.085 ("Some of you are facing...") -> Dialogue resumes

# 1. Remove Choir Lyrics & Intro
# User requests:
# - Start at ID 5 ("Þetta er dagurinn..."). Remove 1-4.
# - Remove "Guð og menn..." (ID 63) and following.
# - Previously removed 67-140.
# - Also removing IDs 30-36 ("Gloria") as it is clearly choir.

ids_to_remove = []
ids_to_remove.extend(range(1, 5))    # Intro
ids_to_remove.extend(range(30, 37))  # Gloria Choir
ids_to_remove.extend(range(63, 141)) # Hark the Herald Choir (Extended range)

count = 0
for seg in data:
    if seg["id"] in ids_to_remove:
        if seg["text"] != "": # Only count if we actually remove something
             seg["text"] = ""
             count += 1

print(f"✅ Cleared text for {count} choir segments.")

# 2. Save back to file
with open(APPROVED_JSON, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"💾 Saved fixes to {APPROVED_JSON}")

# 3. Trigger Burn
# To trigger burn, we need to ensure it's in the right state for `finalize.py` or `publisher.py`
# Usually `finalize.py` takes from 3_TRANSLATED_DONE and moves to 4_FINAL_OUTPUT
# Let's manually move it to 4_FINAL_OUTPUT as a normalized JSON to skip steps?
# Or just update DB to "APPROVED" and let `finalize.py` pick it up?

stem = "2HOP_2913_INT57_RUVBOX"
omega_db.update(stem, stage="TRANSLATED", status="Approved", progress=100)

# Run finalize.py to generate SRT/ASS
print("🚀 Running finalize.py...")
import subprocess
subprocess.run([str(BASE_DIR / "venv/bin/python"), str(BASE_DIR / "finalize.py")], check=True)

print("✅ Finalize complete. Check 5_BURN_QUEUE.")
