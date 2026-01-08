import subprocess
from pathlib import Path
import os
from publisher import srt_to_ass

# Paths
BASE_DIR = Path(os.getcwd())
VIDEO_PATH = BASE_DIR / "1_INBOX/CLASSIC_LOOK/processed/DONE_I2248_Gospel.mp4"
SRT_PATH = BASE_DIR / "4_FINAL_OUTPUT/DONE_I2248_Gospel_RUVBOX.srt"
OUTPUT_CLIP = BASE_DIR / "repro_stacking_clip.mp4"
ASS_PATH = BASE_DIR / "repro.ass"

# 1. Generate ASS
print("Generating ASS...")
srt_to_ass(SRT_PATH, ASS_PATH, style_name="RuvBox")

# 2. Burn Clip (15:20 to 15:40)
# Note: We cut the video AND the subtitles to match.
# But simpler: just burn the whole ASS onto the cut video? 
# No, timing will be wrong if we cut video first.
# We must use -ss on input and burn.

cmd = [
    "ffmpeg", "-y",
    "-ss", "00:15:20",
    "-t", "20",
    "-i", str(VIDEO_PATH),
    "-vf", f"ass='{ASS_PATH}'",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "18",
    "-c:a", "copy",
    str(OUTPUT_CLIP)
]

print(f"Running FFmpeg: {' '.join(cmd)}")
subprocess.run(cmd, check=True)
print("Done.")
