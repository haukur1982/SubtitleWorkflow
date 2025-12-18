import json
import os
import re
import logging
import shutil
from pathlib import Path
import config
import omega_db

logger = logging.getLogger("OmegaManager.Finalizer")

# --- BROADCAST STANDARDS ---
MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MIN_DURATION = 1.0      # Minimum 1 second on screen
IDEAL_CPS = 17          # Characters per second (Netflix standard)
GAP_SECONDS = 0.1       # Gap between subtitles

def _caps_upper_ratio(text: str) -> tuple[float, int]:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0, 0
    upper = sum(1 for ch in letters if ch.isupper())
    lower = sum(1 for ch in letters if ch.islower())
    total = upper + lower
    if total <= 0:
        return 0.0, 0
    return upper / total, total


def _collect_caps_warnings(events: list[dict]) -> dict:
    """
    Detects suspicious ALL-CAPS / mostly-caps subtitle lines for broadcast QA.
    """
    full_caps = 0
    mostly_caps = 0
    samples: list[str] = []

    for event in events:
        lines = event.get("lines") or []
        text = " ".join([str(line).strip() for line in lines if str(line).strip()]).strip()
        if not text:
            continue

        ratio, letter_count = _caps_upper_ratio(text)
        # Ignore very short strings (likely acronyms).
        if letter_count < 8:
            continue

        if ratio >= 0.95:
            full_caps += 1
            if len(samples) < 3:
                samples.append(text[:160])
        elif ratio >= 0.85:
            mostly_caps += 1
            if len(samples) < 3:
                samples.append(text[:160])

    total = len(events)
    return {
        "full_caps": full_caps,
        "mostly_caps": mostly_caps,
        "total": total,
        "samples": samples,
    }

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def split_into_balanced_lines(text, target_language="is"):
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]
    
    # If text is extremely long (>84), we might need 3 lines, but let's stick to 2 for now and force split
    middle = len(text) // 2
    candidates = []
    
    # Search window: Try to stay within 42 chars for the first line
    # Ideally split around the middle, but MUST NOT exceed MAX_CHARS_PER_LINE for the first line
    
    # Heuristic: Find best split point
    start = max(0, middle - 15)
    end = min(len(text), middle + 15)
    
    for i in range(start, end):
        if text[i] == ' ':
            dist = abs(i - middle)
            score = 15 - dist
            
            # Semantic Bonus
            if i > 0 and text[i-1] in ',.;:?!': score += 20
            remaining = text[i:]
            # Semantic Bonus (Language Aware)
            # Icelandic Conjunctions
            if target_language == "is":
                if remaining.startswith(' og ') or remaining.startswith(' en ') or \
                   remaining.startswith(' sem ') or remaining.startswith(' að ') or \
                   remaining.startswith(' eða ') or remaining.startswith(' því '):
                    score += 15
            # Spanish Conjunctions
            elif target_language in ["es", "spanish"]:
                if remaining.startswith(' y ') or remaining.startswith(' o ') or \
                   remaining.startswith(' que ') or remaining.startswith(' pero ') or \
                   remaining.startswith(' de ') or remaining.startswith(' en '):
                    score += 15
                
            # Penalty for exceeding max length
            if i > MAX_CHARS_PER_LINE: score -= 50
            if (len(text) - i) > MAX_CHARS_PER_LINE: score -= 50
            
            candidates.append((i, score))
            
    if not candidates:
        # Fallback: Hard split at max length or middle
        split_idx = min(MAX_CHARS_PER_LINE, middle)
        # Find nearest space backwards
        fallback = text.rfind(' ', 0, split_idx)
        if fallback != -1:
            return [text[:fallback].strip(), text[fallback:].strip()]
        return [text[:split_idx].strip(), text[split_idx:].strip()]
        
    best_split = max(candidates, key=lambda x: x[1])[0]
    return [text[:best_split].strip(), text[best_split:].strip()]

def abbreviate_bible_refs(text, target_language="is"):
    """
    Abbreviates Icelandic Bible references to save space.
    e.g. "Fyrra Korintubréfi 10" -> "1. Kor. 10"
    """
    if target_language == "is":
        replacements = {
            r"(?i)Fyrra Korintubréfi": "1. Kor.",
            r"(?i)Síðara Korintubréfi": "2. Kor.",
            r"(?i)Fyrra Pétursbréfi": "1. Pét.",
            r"(?i)Síðara Pétursbréfi": "2. Pét.",
            r"(?i)Fyrra Jóhannesarbréfi": "1. Jóh.",
            r"(?i)Síðara Jóhannesarbréfi": "2. Jóh.",
            r"(?i)Þriðja Jóhannesarbréfi": "3. Jóh.",
            r"(?i)Fyrra Tessaloníkubréfi": "1. Tess.",
            r"(?i)Síðara Tessaloníkubréfi": "2. Tess.",
            r"(?i)Fyrra Tímóteusarbréfi": "1. Tím.",
            r"(?i)Síðara Tímóteusarbréfi": "2. Tím.",
            r"(?i)Jóhannesarguðspjall": "Jóh.",
            r"(?i)Lúkasarguðspjall": "Lúk.",
            r"(?i)Markúsarguðspjall": "Mark.",
            r"(?i)Matteusarguðspjall": "Matt.",
            r"(?i)Postulasagan": "Post.",
            r"(?i)Rómverjabréfið": "Róm.",
            r"(?i)Galatabréfið": "Gal.",
            r"(?i)Efesusbréfið": "Ef.",
            r"(?i)Filippíbréfið": "Fil.",
            r"(?i)Kólossebréfið": "Kól.",
            r"(?i)Jakobsbréfið": "Jak.",
            r"(?i)Opinberunarbókin": "Op.",
            r"(?i)Hebreabréfið": "Hebr.",
            r"(?i)Fyrsta Mósebók": "1. Mós.",
            r"(?i)Önnur Mósebók": "2. Mós.",
            r"(?i)Þriðja Mósebók": "3. Mós.",
            r"(?i)Fjórða Mósebók": "4. Mós.",
            r"(?i)Fimmta Mósebók": "5. Mós.",
            r"(?i)Sálmarnir": "Sálm.",
            r"(?i)Orðskviðirnir": "Orðskv.",
            r"(?i)Jesaja": "Jes.",
            r"(?i)Jeremía": "Jer.",
            r"(?i)Esekíel": "Esek.",
            r"(?i)Daníel": "Dan."
        }
    elif target_language in ["es", "spanish"]:
        replacements = {
            r"(?i)Primera de Corintios": "1 Cor.",
            r"(?i)Segunda de Corintios": "2 Cor.",
            r"(?i)Primera de Pedro": "1 Ped.",
            r"(?i)Segunda de Pedro": "2 Ped.",
            r"(?i)Primera de Juan": "1 Jn.",
            r"(?i)Segunda de Juan": "2 Jn.",
            r"(?i)Tercera de Juan": "3 Jn.",
            r"(?i)Apocalipsis": "Apoc.",
            r"(?i)Hechos": "Hch.",
            r"(?i)Romanos": "Rom.",
            r"(?i)Mateo": "Mat.",
            r"(?i)Marcos": "Mar.",
            r"(?i)Lucas": "Luc.",
            r"(?i)Juan": "Jn.",
        }
    else:
        replacements = {}
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    
    return text

def finalize(approved_path: Path, target_language: str = "is"):
    """
    Converts APPROVED JSON -> SRT.
    Applies:
    1. Smart Splitting (42 chars)
    2. Orphan Rescue (og, en, að)
    3. CPS Optimization (Steal time from gaps)
    4. Music Filtering
    5. Bible Ref Abbreviation
    """
    logger.info(f"🎬 Finalizing: {approved_path.name}")
    stem = approved_path.name.replace("_ICELANDIC.json", "").replace("_APPROVED.json", "")
    
    with open(approved_path, "r", encoding="utf-8") as f:
        data_wrapper = json.load(f)
    
    if isinstance(data_wrapper, dict):
        data = data_wrapper.get("segments", [])
    else:
        data = data_wrapper

    processed_events = []
    
    # PASS 1: AGGRESSIVE PRE-PROCESS SPLITTING
    for item in data:
        start = item['start']
        end = item['end']
        text = item['text'].replace("\n", " ").strip()
        text = abbreviate_bible_refs(text, target_language)
        
        if not text or "(MUSIC)" in text or "(SONG)" in text: continue 
            
        queue = [{'start': start, 'end': end, 'text': text}]

        while queue:
            curr = queue.pop(0)
            curr_text = curr['text']
            curr_len = len(curr_text)
            
            if curr_len > (MAX_CHARS_PER_LINE * MAX_LINES): 
                mid = curr_len // 2
                search_start = max(0, mid - 20)
                search_end = min(curr_len, mid + 20)
                split_point = -1
                
                split_point = curr_text.rfind('. ', search_start, search_end)
                if split_point == -1: split_point = curr_text.rfind(', ', search_start, search_end)
                if split_point == -1: split_point = curr_text.rfind(' ', search_start, search_end)
                if split_point == -1: split_point = mid

                part1_text = curr_text[:split_point+1].strip()
                part2_text = curr_text[split_point+1:].strip()
                
                ratio = len(part1_text) / curr_len
                mid_time = curr['start'] + ((curr['end'] - curr['start']) * ratio)
                
                queue.insert(0, {'start': mid_time, 'end': curr['end'], 'text': part2_text})
                queue.insert(0, {'start': curr['start'], 'end': mid_time, 'text': part1_text})
            else:
                processed_events.append(curr)

    # PASS 1.5: ORPHAN RESCUER (BBC/Netflix Standard)
    # Move dangling words (og, en, að, því, er) to the next block
    orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i+1]
        
        words = curr['text'].split()
        if not words: continue
        
        last_word = words[-1].lower().strip(".,:;?!\"")
        if last_word in orphans:
            # Move the word to the next block
            word_to_move = words[-1]
            curr['text'] = " ".join(words[:-1])
            next_item['text'] = f"{word_to_move} {next_item['text']}"
            logger.info(f"   🧹 Rescued orphan '{word_to_move}' from block {i+1}")

    # PASS 2: APPLY TIMING RULES & CPS OPTIMIZER
    final_srt_blocks = []
    normalized_events = []
    srt_counter = 1
    
    for i in range(len(processed_events)):
        current = processed_events[i]
        
        char_count = len(current['text'])
        # CPS Optimizer: Allow extending duration to meet 17 CPS
        required_time = char_count / IDEAL_CPS
        original_duration = current['end'] - current['start']
        
        next_start = processed_events[i+1]['start'] if i < len(processed_events) - 1 else 999999
        max_end_time = next_start - GAP_SECONDS
        
        # Allow stealing up to 0.8s from the gap/next segment if available
        extended_target = current['start'] + max(original_duration, MIN_DURATION, required_time)
        actual_end = min(extended_target, max_end_time)
        
        # If still too fast, log it
        final_duration = actual_end - current['start']
        final_cps = char_count / final_duration if final_duration > 0 else 0
        if final_cps > 20:
             logger.warning(f"   ⚠️ High CPS ({final_cps:.1f}): {current['text'][:20]}...")

        if final_duration < 0.5:
            logger.warning(f"   ⚠️ Subtitle {i+1} squashed to <0.5s: {current['text'][:20]}...")

        lines = split_into_balanced_lines(current['text'], target_language)
        final_text = "\n".join(lines)
        
        final_srt_blocks.append(f"{srt_counter}\n{format_timestamp(current['start'])} --> {format_timestamp(actual_end)}\n{final_text}\n\n")
        normalized_events.append({
            "start": current['start'],
            "end": actual_end,
            "lines": lines
        })
        srt_counter += 1

    # SANITY CHECK
    valid_input_count = sum(1 for item in data if item.get('text', '').strip())
    output_count = len(final_srt_blocks)
    
    if valid_input_count > 0:
        drop_ratio = 1.0 - (output_count / valid_input_count)
        if drop_ratio > 0.2:
             logger.error(f"❌ CRITICAL SANITY CHECK FAILED: Dropped {drop_ratio:.1%}")
             raise Exception("Sanity Check Failed: Too many subtitles dropped")

    # SAVE SRT
    srt_path = config.SRT_DIR / f"{stem}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(final_srt_blocks)
    logger.info(f"✅ Created SRT: {srt_path.name}")

    # SAVE NORMALIZED JSON
    normalized_path = config.SRT_DIR / f"{stem}_normalized.json"
    normalized_payload = {
        "events": normalized_events,
        "video_width": 1920,
        "video_height": 1080,
        "framerate": 23.976
    }
    with open(normalized_path, "w", encoding="utf-8") as f:
        json.dump(normalized_payload, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Created Normalized JSON: {normalized_path.name}")

    # QA: Flag suspicious casing (ALL CAPS) so it can be corrected upstream.
    try:
        caps_qa = _collect_caps_warnings(normalized_events)
        if (caps_qa.get("full_caps") or 0) > 0 or (caps_qa.get("mostly_caps") or 0) > 0:
            logger.warning(
                "   ⚠️ QA Caps: %s full-caps, %s mostly-caps (of %s blocks)",
                caps_qa.get("full_caps"),
                caps_qa.get("mostly_caps"),
                caps_qa.get("total"),
            )
        omega_db.update(stem, meta={"qa_caps": caps_qa})
    except Exception as e:
        logger.warning("   ⚠️ QA Caps check failed: %s", e)

    return srt_path, normalized_path
