import sys
import os
from pathlib import Path
import config
from workers import finalizer, publisher

def run():
    # 1. Finalize
    approved_json = Path("3_TRANSLATED_DONE/S6 EP. 1 DOAN (RALEY)_APPROVED.json")
    if not approved_json.exists():
        print(f"❌ File not found: {approved_json}")
        return

    print(f"🎬 Finalizing: {approved_json.name}")
    srt_path, normalized_json_path = finalizer.finalize(approved_json)
    print(f"✅ Created SRT: {srt_path}")

    # 2. Burn
    video_path = Path("2_VAULT/Videos/S6 EP. 1 DOAN (RALEY).mp4")
    if not video_path.exists():
        print(f"❌ Video not found: {video_path}")
        return

    print(f"🔥 Burning: {video_path.name}")
    output_video = publisher.burn(srt_path, forced_style="RUV_BOX")
    print(f"✅ Burn Complete: {output_video}")

if __name__ == "__main__":
    run()
