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
MERGE_GAP_MAX = 0.35
MERGE_MAX_DURATION = 6.8
MERGE_CPS_TRIGGER = 20.0
MERGE_SHORT_TRIGGER = 0.9

def _is_music_only(text: str) -> bool:
    if not text:
        return True
    cleaned = text.strip()
    cleaned = cleaned.replace("♪", "").strip()
    stripped = cleaned.strip("[]()").strip()
    if not stripped:
        return "♪" in text
    tokens = [t for t in stripped.split() if t]
    if len(tokens) != 1:
        return False
    return tokens[0].lower() in {"music", "song", "singing", "choir", "instrumental"}

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def split_into_balanced_lines(text, target_language="is"):
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]
    
    middle = len(text) // 2
    
    # Define split candidates with priorities (higher is better)
    # Format: (split_index, priority)
    candidates = []
    
    # Scan for spaces near middle (wider window for linguistic breaks)
    start = max(0, middle - 15)
    end = min(len(text), middle + 15)
    
    bad_starters = set()
    if target_language == "is":
        bad_starters = {"og", "en", "sem", "að", "eða", "því"}
    elif target_language in ["es", "spanish"]:
        bad_starters = {"y", "o", "que", "pero", "de", "en"}

    for i in range(start, end):
        if text[i] == ' ':
            # Base score: closeness to middle (0 to 15)
            dist = abs(i - middle)
            score = 15 - dist
            
            # Linguistic Bonuses
            # Split after punctuation (e.g. "Hello, world")
            if i > 0 and text[i-1] in ',.;:?!':
                score += 20
            
            left = text[:i].strip()
            right = text[i:].strip()
            remaining = text[i + 1 :].lstrip()
            next_word = remaining.split(" ", 1)[0].lower().strip(".,:;?!\"'")
            if next_word in bad_starters:
                score -= 20

            imbalance = abs(len(left) - len(right))
            score -= min(imbalance, 40) * 0.6
            if len(left) < 12 or len(right) < 12:
                score -= 15

            if len(left) > MAX_CHARS_PER_LINE or len(right) > MAX_CHARS_PER_LINE:
                continue
            
            candidates.append((i, score))
            
    if not candidates:
        # Fallback: Hard split at max length or middle if no spaces found
        split_idx = min(MAX_CHARS_PER_LINE, middle)
        fallback = text.rfind(' ', 0, split_idx)
        if fallback != -1:
            return [text[:fallback].strip(), text[fallback:].strip()]
        return [text[:split_idx].strip(), text[split_idx:].strip()]
        
    # Pick best candidate
    best_split = max(candidates, key=lambda x: x[1])[0]
    
    return [text[:best_split].strip(), text[best_split:].strip()]

def _merge_high_cps_events(events):
    if not events:
        return events

    merged = []
    i = 0
    max_chars = MAX_CHARS_PER_LINE * MAX_LINES
    while i < len(events):
        curr = events[i]
        if i < len(events) - 1:
            nxt = events[i + 1]
            try:
                gap = float(nxt["start"]) - float(curr["end"])
            except Exception:
                gap = MERGE_GAP_MAX + 1

            if gap <= MERGE_GAP_MAX and gap >= -0.05:
                curr_text = str(curr.get("text") or "").strip()
                next_text = str(nxt.get("text") or "").strip()
                if curr_text and next_text:
                    curr_dur = max(0.01, float(curr["end"]) - float(curr["start"]))
                    next_dur = max(0.01, float(nxt["end"]) - float(nxt["start"]))
                    curr_cps = len(curr_text) / curr_dur
                    next_cps = len(next_text) / next_dur

                    combined_text = f"{curr_text} {next_text}".strip()
                    combined_dur = max(0.01, float(nxt["end"]) - float(curr["start"]))
                    combined_cps = len(combined_text) / combined_dur

                    needs_merge = (
                        curr_cps > MERGE_CPS_TRIGGER
                        or next_cps > MERGE_CPS_TRIGGER
                        or curr_dur < MERGE_SHORT_TRIGGER
                        or next_dur < MERGE_SHORT_TRIGGER
                    )

                    if (
                        needs_merge
                        and combined_dur <= MERGE_MAX_DURATION
                        and len(combined_text) <= max_chars
                        and (combined_cps <= MERGE_CPS_TRIGGER or combined_cps <= max(curr_cps, next_cps) - 0.5)
                    ):
                        merged.append({"start": curr["start"], "end": nxt["end"], "text": combined_text})
                        i += 2
                        continue

        merged.append(curr)
        i += 1

    return merged

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
            continue # Skip empty segments
        if _is_music_only(text):
            continue # Skip pure music/lyrics markers
            
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

    # PASS 1.5: CPS RESCUE MERGE
    for _ in range(2):
        merged_events = _merge_high_cps_events(processed_events)
        if len(merged_events) == len(processed_events):
            break
        processed_events = merged_events

    # PASS 1.6: ORPHAN RESCUER (BBC/Netflix Standard)
    orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i + 1]

        words = curr['text'].split()
        if not words:
            continue

        last_word = words[-1].lower().strip(".,:;?!\"")
        if last_word in orphans:
            word_to_move = words[-1]
            curr['text'] = " ".join(words[:-1])
            next_item['text'] = f"{word_to_move} {next_item['text']}"

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
