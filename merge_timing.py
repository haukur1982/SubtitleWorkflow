#!/usr/bin/env python3
import json
from pathlib import Path

# Load skeleton (has timing)
skeleton = json.load(open("2_VAULT/Data/CBN700ISRAEL112825CC_HDMPEG2_SKELETON_DONE.json"))
skeleton_segments = skeleton.get("segments", skeleton)

# Load translation (has text, missing timing)
translation = json.load(open("99_ERRORS/CBN700ISRAEL112825CC_HDMPEG2_APPROVED.json"))

# Merge: Add timing to translation
merged = []
trans_map = {item['id']: item['text'] for item in translation}

for seg in skeleton_segments:
    seg_id = seg['id']
    merged.append({
        "id": seg_id,
        "start": seg['start'],
        "end": seg['end'],
        "text": trans_map.get(seg_id, seg.get('text', ''))
    })

# Save to TRANSLATED_DONE for finalize.py to pick up
with open("3_TRANSLATED_DONE/CBN700ISRAEL112825CC_HDMPEG2_APPROVED.json", "w") as f:
    json.dump(merged, f, indent=2, ensure_ascii=False)

print("✅ Merged timing with translation -> Saved to 3_TRANSLATED_DONE")
