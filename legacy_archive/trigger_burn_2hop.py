from publisher import burn_subtitles
from pathlib import Path

# TARGET FILES
srt_path = Path("/Users/haukur/SubtitleWorkflow/4_FINAL_OUTPUT/DONE_2HOP_2913_INT57_RUVBOX.srt")
video_path = Path("/Users/haukur/SubtitleWorkflow/1_INBOX/CLASSIC_LOOK/processed/DONE_2HOP_2913_INT57.mp4")

print(f"🔥 Starting manual burn for {srt_path.name}...")
print(f"🎥 Video source: {video_path.name}")

if not srt_path.exists():
    print("❌ SRT not found!")
    exit(1)
if not video_path.exists():
    print("❌ Video not found!")
    exit(1)

burn_subtitles(srt_path)
print("✅ Burn process initiated.")
