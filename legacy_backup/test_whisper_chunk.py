import subprocess
import sys
from pathlib import Path

# Config
WHISPER_BIN = Path("venv/bin/whisperx")
CHUNK_PATH = Path("I2248_Gospel_chunks/I2248_Gospel_chunk_00.wav")
OUTPUT_DIR = Path("I2248_Gospel_chunks")

print(f"🧪 Testing WhisperX on {CHUNK_PATH}...")

cmd = [
    str(WHISPER_BIN), str(CHUNK_PATH),
    "--model", "medium.en",
    "--output_dir", str(OUTPUT_DIR),
    "--output_format", "json",
    "--device", "cpu",
    "--compute_type", "int8"
]

try:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    print("✅ Success!")
    print(result.stdout)
except subprocess.CalledProcessError as e:
    print("❌ Failure!")
    print("STDOUT:", e.stdout)
    print("STDERR:", e.stderr)
