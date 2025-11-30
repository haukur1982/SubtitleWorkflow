from workers import finalizer
from pathlib import Path
import logging

# Configure logging to see the new messages
logging.basicConfig(level=logging.INFO)

base_dir = Path("/Users/haukur/SubtitleWorkflow")
approved_json = base_dir / "3_TRANSLATED_DONE/S6 EP. 1 DOAN (RALEY)_APPROVED.json"

if approved_json.exists():
    print(f"🚀 Running Finalizer on: {approved_json.name}")
    finalizer.finalize(approved_json)
    print("✅ Done.")
else:
    print("❌ File not found.")
