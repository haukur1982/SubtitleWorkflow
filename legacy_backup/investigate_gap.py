import os
import subprocess
import json
from pathlib import Path

BASE_DIR = Path("/Users/haukur/SubtitleWorkflow")
WHISPER_BIN = BASE_DIR / "venv" / "bin" / "whisperx"
INPUT_FILE = BASE_DIR / "2_READY_FOR_CLOUD" / "HOP_2913_INT57.mp3"
ARCHIVE_FILE = BASE_DIR / "6_ARCHIVE/2025-11/HOP_2913_INT57/HOP_2913_INT57.mp3"

if not INPUT_FILE.exists():
    if ARCHIVE_FILE.exists():
        INPUT_FILE = ARCHIVE_FILE
    else:
        print("❌ Input file not found!")
        exit(1)

print(f"Using input file: {INPUT_FILE}")

OUTPUT_CLIP = BASE_DIR / "debug_gap_clip.wav"
START_TIME = 1200 # 20:00
DURATION = 70     # 1m 10s

# Extract clip
cmd = [
    "ffmpeg", "-y", "-i", str(INPUT_FILE),
    "-ss", str(START_TIME), "-t", str(DURATION),
    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
    str(OUTPUT_CLIP)
]
subprocess.run(cmd, check=True)
print(f"✅ Extracted clip to {OUTPUT_CLIP}")

# Isolate Vocals with Demucs
DEMUCS_BIN = BASE_DIR / "venv" / "bin" / "demucs"
DEMUCS_OUT = BASE_DIR / "debug_demucs"
print("🎸 Running Demucs...")
demucs_cmd = [
    str(DEMUCS_BIN),
    "-n", "htdemucs",
    "--two-stems=vocals",
    "-o", str(DEMUCS_OUT),
    str(OUTPUT_CLIP)
]
env = os.environ.copy()
env["TQDM_DISABLE"] = "1"
env["PYTHONUNBUFFERED"] = "1"

subprocess.run(demucs_cmd, check=True, env=env)

# Path to isolated vocals
VOCALS_FILE = DEMUCS_OUT / "htdemucs" / "debug_gap_clip" / "vocals.wav"
if not VOCALS_FILE.exists():
    print("❌ Vocals file not found, using original")
    VOCALS_FILE = OUTPUT_CLIP
else:
    print(f"✅ Vocals isolated: {VOCALS_FILE}")

# Transcribe
print("🎙️ Running WhisperX on Vocals...")
whisper_cmd = [
    str(WHISPER_BIN), str(VOCALS_FILE),
    "--output_dir", str(BASE_DIR),
    "--output_format", "json",
    "--model", "medium.en",
    "--device", "cpu", # Use CPU for safety/simplicity in debug
    "--compute_type", "int8",
    "--initial_prompt", "Transcribe only spoken dialogue."
]

try:
    subprocess.run(whisper_cmd, check=True)
    json_path = BASE_DIR / "debug_gap_clip.json"
    if json_path.exists():
        with open(json_path, "r") as f:
            data = json.load(f)
            print(json.dumps(data, indent=2))
    else:
        print("❌ JSON output not found")
except Exception as e:
    print(f"❌ Transcription failed: {e}")
