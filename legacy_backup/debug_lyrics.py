import json
from pathlib import Path

def inspect_segments(file_path, target_time, window=30):
    print(f"--- Inspecting: {file_path.name} ---")
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        if isinstance(data, list):
            segments = data
        else:
            segments = data.get("segments", [])
            
        found = False
        for seg in segments:
            start = seg.get('start', 0)
            end = seg.get('end', 0)
            
            if start >= target_time - window and start <= target_time + window:
                print(f"[{start:.2f} - {end:.2f}] {seg.get('text', '')}")
                found = True
                
        if not found:
            print("No segments found in this window.")
            
    except Exception as e:
        print(f"Error: {e}")
    print("\n")

base_dir = Path("/Users/haukur/SubtitleWorkflow")
skeleton = base_dir / "2_VAULT/Data/S6 EP. 1 DOAN (RALEY)_SKELETON_DONE.json"
approved = base_dir / "3_TRANSLATED_DONE/S6 EP. 1 DOAN (RALEY)_APPROVED.json"

target_seconds = 106 # 01:46

if skeleton.exists(): inspect_segments(skeleton, target_seconds)
if approved.exists(): inspect_segments(approved, target_seconds)
