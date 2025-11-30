# Manual Burn Trigger
from publisher import burn_subtitles
from pathlib import Path

# TARGET FILES
srt_path = Path("/Users/haukur/SubtitleWorkflow/4_FINAL_OUTPUT/DONE_HOP_2913_INT57.srt")
video_path = Path("/Users/haukur/SubtitleWorkflow/1_INBOX/CLASSIC_LOOK/2HOP_2913_INT57.mp4")

print(f"Starting manual burn for {srt_path}...")
burn_subtitles(srt_path, video_path=video_path, style="RUV_BOX")
print("Burn process initiated.")
