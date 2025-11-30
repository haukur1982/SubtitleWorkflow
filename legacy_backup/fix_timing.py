import json
from pathlib import Path

BASE_DIR = Path("/Users/haukur/SubtitleWorkflow")
APPROVED_PATH = BASE_DIR / "3_TRANSLATED_DONE" / "HOP_2913_INT57_APPROVED.json"
SKELETON_PATH = BASE_DIR / "2_READY_FOR_CLOUD" / "processed" / "HOP_2913_INT57_SKELETON.json"

print(f"Fixing {APPROVED_PATH}...")

with open(APPROVED_PATH, 'r') as f:
    approved = json.load(f)

with open(SKELETON_PATH, 'r') as f:
    skeleton = json.load(f)
    if "segments" in skeleton:
        skeleton = skeleton["segments"]

source_map = {item['id']: item for item in skeleton}
merged = []

for seg in approved:
    seg_id = seg['id']
    if seg_id in source_map:
        original = source_map[seg_id]
        merged.append({
            "id": seg_id,
            "start": original['start'],
            "end": original['end'],
            "text": seg['text']
        })
    else:
        print(f"Warning: ID {seg_id} not found in skeleton")
        merged.append(seg)

with open(APPROVED_PATH, 'w') as f:
    json.dump(merged, f, indent=2, ensure_ascii=False)

print("✅ Fixed!")
