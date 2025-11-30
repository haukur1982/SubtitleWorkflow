from publisher import srt_to_ass
from pathlib import Path

srt_path = Path("/Users/haukur/SubtitleWorkflow/4_FINAL_OUTPUT/DONE_I2248_Gospel_RUVBOX.srt")
ass_path = Path("/Users/haukur/SubtitleWorkflow/test_debug.ass")

print(f"Generating ASS from {srt_path}...")
srt_to_ass(srt_path, ass_path, style_name="RuvBox")
print("Done.")
