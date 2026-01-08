
import sys
import os
import logging

# Setup minimalist logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("VerifyFix")

# Add project root to path
sys.path.append("/Users/haukurhauksson/SubtitleWorkflow")

# Import the modified module
try:
    from workers.transcriber_assemblyai import _mark_opening_music
except ImportError:
    print("❌ Could not import workers.transcriber_assemblyai")
    sys.exit(1)

def run_test():
    print("🧪 Starting Efficient Verification: Music Detector Fix\n")

    # 1. Reconstruct the "Problem Case" from the actual file data
    # This matches the segment that was wrongly deleted
    test_segments = [
        {
            "id": 1,
            "start": 2.4,
            "end": 7.428,
            "text": "And the Son of God, Jesus the Lord, said, I am the way, the truth and the life.",
            # "is_music" starts as False (raw transcription state)
        },
        {
            "id": 2,
            "start": 7.428,
            "end": 9.782,
            "text": "No one comes to the Father but by me.",
        }
    ]

    print(f"🔹 Input Text: \"{test_segments[0]['text']}\"")
    print("   (Contains 'God', 'Jesus', 'Lord' - formerly triggered deletion)\n")

    # 2. Run the Logic
    processed_segments, marked_count = _mark_opening_music(test_segments)
    
    # 3. Analyze Result
    first_seg = processed_segments[0]
    
    if first_seg.get("is_music"):
        print("❌ FAIL: Segment was marked as music!")
        print(f"   Result Text: {first_seg['text']}")
    else:
        print("✅ SUCCESS: Segment was NOT marked as music.")
        print(f"   Result Text: \"{first_seg['text']}\"")
        print("   Reason: Logic now requires short repetitive phrases for keyword trigger, or explicit (MUSIC) tags.")

if __name__ == "__main__":
    run_test()
