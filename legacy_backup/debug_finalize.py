from finalize import json_to_srt
from pathlib import Path
import os

# Define paths
BASE_DIR = Path(os.getcwd())
INBOX = BASE_DIR / "3_TRANSLATED_DONE"
json_file = INBOX / "I2248_Gospel_RUVBOX_APPROVED.json"

print(f"Testing json_to_srt on {json_file}")
if json_file.exists():
    json_to_srt(json_file)
    print("Done.")
else:
    print("File not found.")
