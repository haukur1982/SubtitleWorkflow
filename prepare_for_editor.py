#!/usr/bin/env python3
import json
from pathlib import Path

# Load skeleton (English source with timing)
skeleton = json.load(open("2_VAULT/Data/CBN700ISRAEL112825CC_HDMPEG2_SKELETON_DONE.json"))
source_segments = skeleton.get("segments", skeleton)

# Load translation (Icelandic text only)
translation_raw = json.load(open("99_ERRORS/CBN700ISRAEL112825CC_HDMPEG2_APPROVED.json"))
trans_map = {item['id']: item['text'] for item in translation_raw}

# Create source_data with timing
source_data = []
translated_data = []

for seg in source_segments:
    seg_id = seg['id']
    
    source_data.append({
        "id": seg_id,
        "start": seg['start'],
        "end": seg['end'],
        "text": seg.get('text', '')
    })
    
    translated_data.append({
        "id": seg_id,
        "text": trans_map.get(seg_id, '')
    })

# Create the format chief_editor expects
editor_input = {
    "source_data": source_data,
    "translated_data": translated_data
}

# Save as ICELANDIC for the editor to pick up
with open("3_EDITOR/CBN700ISRAEL112825CC_HDMPEG2_ICELANDIC.json", "w") as f:
    json.dump(editor_input, f, indent=2, ensure_ascii=False)

print("✅ Created ICELANDIC.json with source_data + translated_data for Chief Editor")
