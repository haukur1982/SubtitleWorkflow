import json
import os
import time
import textwrap
import shutil
from pathlib import Path
from lock_manager import ProcessLock
import system_health
import omega_db

# --- CONFIGURATION ---
BASE_DIR = Path(os.getcwd())
INBOX = BASE_DIR / "3_EDITOR"
OUTBOX = BASE_DIR / "4_DELIVERY" / "SRT"
OUTBOX.mkdir(parents=True, exist_ok=True)
ERROR_DIR = BASE_DIR / "99_ERRORS"
ERROR_DIR.mkdir(exist_ok=True)

# --- BROADCAST STANDARDS ---
MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MIN_DURATION = 1.0      # Minimum 1 second on screen
IDEAL_CPS = 17          # Characters per second (Netflix standard)
GAP_SECONDS = 0.1       # Gap between subtitles

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def split_into_balanced_lines(text):
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]
    
    middle = len(text) // 2
    
    # Define split candidates with priorities (higher is better)
    # Format: (split_index, priority)
    candidates = []
    
    # Scan for spaces near middle (wider window for linguistic breaks)
    start = max(0, middle - 15)
    end = min(len(text), middle + 15)
    
    for i in range(start, end):
        if text[i] == ' ':
            # Base score: closeness to middle (0 to 15)
            dist = abs(i - middle)
            score = 15 - dist
            
            # Linguistic Bonuses
            # Split after punctuation (e.g. "Hello, world")
            if i > 0 and text[i-1] in ',.;:?!':
                score += 20
            
            # Split before conjunctions (look ahead)
            # Check if the word following this space is a conjunction
            # We look at text[i:] which starts with " "
            remaining = text[i:]
            if remaining.startswith(' og ') or remaining.startswith(' en ') or \
               remaining.startswith(' sem ') or remaining.startswith(' að ') or \
               remaining.startswith(' eða ') or remaining.startswith(' því '):
                score += 15
            
            candidates.append((i, score))
            
    if not candidates:
        # Fallback: Hard split at middle if no spaces found
        return [text[:middle].strip(), text[middle:].strip()]
        
    # Pick best candidate
    best_split = max(candidates, key=lambda x: x[1])[0]
    
    return [text[:best_split].strip(), text[best_split:].strip()]

def json_to_srt(json_file):
    print(f"🎬 Finalizing (Smart Timing): {json_file.name}")
    
    with open(json_file, "r", encoding="utf-8") as f:
        data_wrapper = json.load(f)
    
    # HANDLE WRAPPER (Dict vs List)
    if isinstance(data_wrapper, dict):
        data = data_wrapper.get("segments", [])
    else:
        data = data_wrapper

    stem = json_file.name.replace("_ICELANDIC.json", "").replace("_APPROVED.json", "")
    srt_path = OUTBOX / f"{stem}.srt"
    normalized_path = OUTBOX / f"{stem}_normalized.json"
    
    processed_events = []
    srt_counter = 1

    # PASS 1: AGGRESSIVE PRE-PROCESS SPLITTING (Queue-based)
    # This ensures NO subtitle ever exceeds MAX_CHARS_PER_LINE * MAX_LINES
    for item in data:
        start = item['start']
        end = item['end']
        text = item['text'].replace("\n", " ").strip()
        
        # --- MUSIC SUPPRESSION ---
        if not text:
            continue # Skip empty segments (Singing/Music)
            
        duration = end - start

        # Create a queue to handle this text chunk
        # If it's too long, we split it and add parts back to queue
        queue = [{'start': start, 'end': end, 'text': text}]

        while queue:
            curr = queue.pop(0)
            curr_text = curr['text']
            curr_len = len(curr_text)
            
            # STRICT LIMIT: If > 84 chars (42x2), we MUST split
            if curr_len > (MAX_CHARS_PER_LINE * MAX_LINES): 
                # Find split point in the middle area
                # Search priority: Period -> Comma -> Space
                mid = curr_len // 2
                # Look 20 chars left/right of center
                search_start = max(0, mid - 20)
                search_end = min(curr_len, mid + 20)

                split_point = -1
                
                # 1. Try Period
                split_point = curr_text.rfind('. ', search_start, search_end)
                # 2. Try Comma (if no period)
                if split_point == -1: 
                    split_point = curr_text.rfind(', ', search_start, search_end)
                # 3. Try Space (fallback)
                if split_point == -1: 
                    split_point = curr_text.rfind(' ', search_start, search_end)
                
                # If we still found nothing (rare giant word), force hard split
                if split_point == -1:
                    split_point = mid

                # Do the split
                part1_text = curr_text[:split_point+1].strip()
                part2_text = curr_text[split_point+1:].strip()
                
                # Calculate new timing based on character ratio
                ratio = len(part1_text) / curr_len
                mid_time = curr['start'] + ((curr['end'] - curr['start']) * ratio)
                
                # Insert back into queue to be checked again (Recursive safety)
                # Process part1 first, then part2
                queue.insert(0, {'start': mid_time, 'end': curr['end'], 'text': part2_text})
                queue.insert(0, {'start': curr['start'], 'end': mid_time, 'text': part1_text})
            else:
                # It fits! Add to final list
                processed_events.append(curr)

    # PASS 2: APPLY TIMING RULES (The "Elastic" Fix)
    final_srt_blocks = []
    normalized_events = []
    
    for i in range(len(processed_events)):
        current = processed_events[i]
        
        # 1. Calculate Ideal Duration
        char_count = len(current['text'])
        required_time = char_count / IDEAL_CPS
        original_duration = current['end'] - current['start']
        
        # 2. Extend Duration if needed (and possible)
        # Check when the NEXT subtitle starts
        next_start = processed_events[i+1]['start'] if i < len(processed_events) - 1 else 999999
        max_end_time = next_start - GAP_SECONDS
        
        # New end time is the MAX of (Original, Minimum 1s, Ideal Reading Time)
        # But capped by the start of the next subtitle
        target_end = current['start'] + max(original_duration, MIN_DURATION, required_time)
        actual_end = min(target_end, max_end_time)
        
        # --- SAFETY CHECK: SQUASHED SUBS ---
        if (actual_end - current['start']) < 0.5:
            print(f"   ⚠️ Warning: Subtitle {i+1} squashed to <0.5s due to overlap. Text: {current['text'][:20]}...")
            # We still keep it, but it's good to know.

        
        # 3. Format Text (Wrap)
        lines = split_into_balanced_lines(current['text'])
        final_text = "\n".join(lines)
        
        final_srt_blocks.append(f"{srt_counter}\n{format_timestamp(current['start'])} --> {format_timestamp(actual_end)}\n{final_text}\n\n")
        normalized_events.append({
            "start": current['start'],
            "end": actual_end,
            "lines": lines
        })
        srt_counter += 1

    # --- SANITY CHECK ---
    # Ensure we haven't dropped a massive amount of subtitles.
    # We count valid (non-empty) input segments vs output blocks.
    # Since we split long lines, output should generally be >= input.
    # A drop of > 20% indicates a serious logic failure (like the "silent drop" bug).
    
    valid_input_count = sum(1 for item in data if item.get('text', '').strip())
    output_count = len(final_srt_blocks)
    
    if valid_input_count > 0:
        drop_ratio = 1.0 - (output_count / valid_input_count)
        if drop_ratio > 0.2: # Allow 20% variance (e.g. merges), but warn on big drops
             print(f"   ❌ CRITICAL SANITY CHECK FAILED: Input {valid_input_count} -> Output {output_count} (Dropped {drop_ratio:.1%})")
             print(f"   ❌ Aborting finalization for {stem} to prevent data loss.")
             shutil.move(str(json_file), str(ERROR_DIR / json_file.name))
             return


    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(final_srt_blocks)
    
    print(f"   ✅ Created Broadcast SRT: {srt_path.name}")

    print(f"   ✅ Created Broadcast SRT: {srt_path.name}")

    # Save Normalized JSON to OUTBOX (for Publisher)
    # vault_data = BASE_DIR / "2_VAULT" / "Data"
    normalized_path = OUTBOX / f"{stem}_normalized.json"
    
    normalized_payload = {
        "events": normalized_events,
        "video_width": 1920,
        "video_height": 1080,
        "framerate": 23.976
    }

    with open(normalized_path, "w", encoding="utf-8") as f:
        json.dump(normalized_payload, f, ensure_ascii=False, indent=2)

    print(f"   ✅ Created Normalized JSON: {normalized_path.name}")

    # --- FINAL VERIFICATION ---
    if srt_path.stat().st_size == 0 or normalized_path.stat().st_size == 0:
        print(f"   ❌ CRITICAL ERROR: Generated empty files for {stem}")
        shutil.move(str(json_file), str(ERROR_DIR / json_file.name))
        return

def _run_watcher():
    print(f"👀 Typesetter Active. Waiting for APPROVED JSONs in {INBOX}...")
    while True:
        system_health.update_heartbeat("finalize")
        
        # Check for APPROVED files (Post-QC)
        # The Editor Agent (editor.py) creates these after verifying _ICELANDIC.json
        # We check both INBOX (Auto Mode) and TRANSLATED_DONE (Review Mode)
        
        search_dirs = [INBOX, BASE_DIR / "3_TRANSLATED_DONE"]
        
        for search_dir in search_dirs:
            if not search_dir.exists(): continue
            
            for json_file in search_dir.glob("*_APPROVED.json"):
                stem = json_file.name.replace("_APPROVED.json", "")
                
                # Check if already finalized (SRT exists)
                srt_path = OUTBOX / f"{stem}.srt"
                if srt_path.exists():
                    continue

                print(f"🎬 Finalizing (Smart Timing): {json_file.name}")
                omega_db.update(stem, status="Finalizing Subtitles", progress=90.0)
                
                try:
                    # We need to pass the file path to json_to_srt (which calls process_file logic)
                    # Wait, json_to_srt is the function name in this file? 
                    # Looking at previous view, it calls json_to_srt(file).
                    # Let's check if json_to_srt is defined. Yes, line 101 in previous view.
                    json_to_srt(json_file)
                except Exception as e:
                    print(f"   ❌ Error finalizing {stem}: {e}")
                    # Move to error?
                    shutil.move(str(json_file), str(ERROR_DIR / json_file.name))
                
        time.sleep(2)


# if __name__ == "__main__":
#     with ProcessLock("finalize"):
#         _run_watcher()

if __name__ == "__main__":
    _run_watcher()
