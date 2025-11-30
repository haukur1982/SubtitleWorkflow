import json
from pathlib import Path

BASE_DIR = Path("/Users/haukur/SubtitleWorkflow")
VAULT_DATA = BASE_DIR / "2_VAULT" / "Data"
stem = "CBN700ISRAEL112825CC_HDMPEG2"
whisper_json = VAULT_DATA / f"{stem}.json"
skeleton_path = VAULT_DATA / f"{stem}_SKELETON.json"

if whisper_json.exists():
    print(f"Converting {whisper_json}...")
    with open(whisper_json, "r") as f:
        data = json.load(f)
        
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
        "mode": "AUTO",
        "style": "RUV_BOX",
        "segments": segments
    }
    
    with open(skeleton_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        
    print(f"✅ Skeleton Saved: {skeleton_path}")
else:
    print(f"❌ File not found: {whisper_json}")
