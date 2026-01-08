import json
import os
import re
import logging
import shutil
from pathlib import Path
import config
import omega_db
from subtitle_standards import (
    MAX_CHARS_PER_LINE,
    MAX_LINES,
    MIN_DURATION,
    GAP_SECONDS,
    get_cps_for_language,
)

logger = logging.getLogger("OmegaManager.Finalizer")

# --- BROADCAST STANDARDS ---
MERGE_GAP_MAX = 0.35
MERGE_MAX_DURATION = 6.8
MERGE_CPS_TRIGGER = 20.0
MERGE_SHORT_TRIGGER = 0.9

def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _timing_mode() -> str:
    mode = str(os.environ.get("OMEGA_TIMING_MODE", "balanced") or "balanced").strip().lower()
    if mode not in {"balanced", "strict"}:
        return "balanced"
    return mode


def _strict_timing_limits() -> tuple[float, float]:
    max_extend = _safe_float_env("OMEGA_TIMING_STRICT_MAX_EXTEND", 0.0)
    fragment_shift = _safe_float_env("OMEGA_TIMING_STRICT_FRAGMENT_SHIFT", 0.0)
    return max_extend, fragment_shift

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


def _collect_srt_qc(events: list[dict]) -> dict:
    orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    total = len(events)
    high_cps_17 = 0
    high_cps_20 = 0
    short_duration = 0
    long_duration = 0
    long_lines = 0
    dangling = 0
    max_cps = 0.0
    max_duration = 0.0

    for event in events:
        lines = event.get("lines") or []
        cleaned_lines = [str(line).strip() for line in lines if str(line).strip()]
        if not cleaned_lines:
            continue

        text = " ".join(cleaned_lines).strip()
        duration = max(0.0, float(event.get("end", 0.0)) - float(event.get("start", 0.0)))
        cps = (len(text) / duration) if duration > 0 else 0.0
        max_cps = max(max_cps, cps)
        max_duration = max(max_duration, duration)

        if cps > IDEAL_CPS:
            high_cps_17 += 1
        if cps > 20:
            high_cps_20 += 1
        if duration < MIN_DURATION:
            short_duration += 1
        if duration > 7.0:
            long_duration += 1

        for line in cleaned_lines:
            if len(line) > MAX_CHARS_PER_LINE:
                long_lines += 1

        last_word = cleaned_lines[-1].split()[-1].lower().strip(".,:;?!\"'")
        if last_word in orphans:
            dangling += 1

    return {
        "total": total,
        "high_cps_17": high_cps_17,
        "high_cps_20": high_cps_20,
        "short_duration": short_duration,
        "long_duration": long_duration,
        "long_lines": long_lines,
        "dangling": dangling,
        "max_cps": round(max_cps, 2),
        "max_duration": round(max_duration, 2),
    }


def _collect_timing_qc(events: list[dict]) -> dict:
    total = len(events)
    with_words = 0
    missing_words = 0
    overlaps = 0
    max_overlap = 0.0
    min_gap = None
    max_gap = 0.0
    short_duration = 0
    zero_duration = 0

    start_delta_sum = 0.0
    end_delta_sum = 0.0
    start_delta_min = None
    start_delta_max = None
    end_delta_min = None
    end_delta_max = None
    start_early = 0
    start_late = 0
    end_cutoff = 0
    end_tail = 0

    start_early_threshold = -0.35
    start_late_threshold = 0.25
    end_cutoff_threshold = -0.15
    end_tail_threshold = 0.40

    prev_end = None

    for event in events:
        start = float(event.get("start", 0.0))
        end = float(event.get("end", 0.0))
        duration = end - start

        if duration <= 0:
            zero_duration += 1
        if duration < MIN_DURATION:
            short_duration += 1

        if prev_end is not None:
            gap = start - prev_end
            min_gap = gap if min_gap is None else min(min_gap, gap)
            max_gap = max(max_gap, gap)
            if gap < 0:
                overlaps += 1
                max_overlap = max(max_overlap, abs(gap))
        prev_end = end

        words = event.get("words")
        if isinstance(words, list) and words:
            word_start = words[0].get("start")
            word_end = words[-1].get("end")
            if word_start is None or word_end is None:
                missing_words += 1
                continue

            word_start = float(word_start)
            word_end = float(word_end)
            with_words += 1

            start_delta = start - word_start
            end_delta = end - word_end

            start_delta_sum += start_delta
            end_delta_sum += end_delta

            start_delta_min = start_delta if start_delta_min is None else min(start_delta_min, start_delta)
            start_delta_max = start_delta if start_delta_max is None else max(start_delta_max, start_delta)
            end_delta_min = end_delta if end_delta_min is None else min(end_delta_min, end_delta)
            end_delta_max = end_delta if end_delta_max is None else max(end_delta_max, end_delta)

            if start_delta < start_early_threshold:
                start_early += 1
            if start_delta > start_late_threshold:
                start_late += 1
            if end_delta < end_cutoff_threshold:
                end_cutoff += 1
            if end_delta > end_tail_threshold:
                end_tail += 1
        else:
            missing_words += 1

    start_delta_avg = start_delta_sum / with_words if with_words else 0.0
    end_delta_avg = end_delta_sum / with_words if with_words else 0.0

    return {
        "total": total,
        "with_words": with_words,
        "missing_words": missing_words,
        "zero_duration": zero_duration,
        "short_duration": short_duration,
        "overlaps": overlaps,
        "max_overlap": round(max_overlap, 3),
        "min_gap": round(min_gap, 3) if min_gap is not None else None,
        "max_gap": round(max_gap, 3),
        "start_delta_avg": round(start_delta_avg, 3),
        "start_delta_min": round(start_delta_min, 3) if start_delta_min is not None else None,
        "start_delta_max": round(start_delta_max, 3) if start_delta_max is not None else None,
        "end_delta_avg": round(end_delta_avg, 3),
        "end_delta_min": round(end_delta_min, 3) if end_delta_min is not None else None,
        "end_delta_max": round(end_delta_max, 3) if end_delta_max is not None else None,
        "start_early": start_early,
        "start_late": start_late,
        "end_cutoff": end_cutoff,
        "end_tail": end_tail,
        "thresholds": {
            "start_early": start_early_threshold,
            "start_late": start_late_threshold,
            "end_cutoff": end_cutoff_threshold,
            "end_tail": end_tail_threshold,
        },
    }


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


def _find_word_boundary_time(words: list[dict], char_position: int, use_end: bool = True) -> float:
    """
    Find the timing at a character position using word-level boundaries.
    
    Args:
        words: List of word dicts with 'text', 'start', 'end' keys
        char_position: Character offset in the concatenated text
        use_end: If True, return the end time of the boundary word; else start time
        
    Returns:
        The word boundary time in seconds, or None if words data unavailable.
    """
    if not words:
        return None
    
    chars_seen = 0
    for i, word in enumerate(words):
        word_text = word.get("text", "")
        word_len = len(word_text)
        
        # Check if the split point falls within or after this word
        if chars_seen + word_len >= char_position:
            return word.get("end") if use_end else word.get("start")
        
        chars_seen += word_len + 1  # +1 for space between words
    
    # If we're past all words, return the last word's end time
    return words[-1].get("end") if words else None

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def format_timestamp_vtt(seconds):
    """VTT uses . instead of , for milliseconds."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def format_timestamp_ttml(seconds):
    """TTML uses HH:MM:SS.mmm format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def generate_vtt(events: list[dict], output_path: Path):
    """
    Generate WebVTT subtitle file from normalized events.
    """
    lines = ["WEBVTT", ""]
    for i, event in enumerate(events, 1):
        start = format_timestamp_vtt(event["start"])
        end = format_timestamp_vtt(event["end"])
        text = "\n".join(event.get("lines", []))
        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"✅ Created VTT: {output_path.name}")
    return output_path


def generate_ttml(events: list[dict], output_path: Path, lang_code: str = "is"):
    """
    Generate TTML (Timed Text Markup Language) subtitle file.
    Compatible with Netflix, YouTube, and broadcast workflows.
    """
    # Escape XML special characters
    def escape_xml(text):
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))
    
    ttml_header = f'''<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" xml:lang="{lang_code}">
  <head>
    <styling>
      <style xml:id="defaultStyle" tts:fontFamily="Arial" tts:fontSize="100%" tts:textAlign="center"/>
    </styling>
    <layout>
      <region xml:id="bottom" tts:origin="10% 80%" tts:extent="80% 20%" tts:textAlign="center"/>
    </layout>
  </head>
  <body>
    <div>
'''
    ttml_footer = '''    </div>
  </body>
</tt>
'''
    
    paragraphs = []
    for event in events:
        start = format_timestamp_ttml(event["start"])
        end = format_timestamp_ttml(event["end"])
        text = "<br/>".join([escape_xml(line) for line in event.get("lines", [])])
        paragraphs.append(f'      <p begin="{start}" end="{end}" region="bottom">{text}</p>')
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ttml_header)
        f.write("\n".join(paragraphs))
        f.write("\n")
        f.write(ttml_footer)
    
    logger.info(f"✅ Created TTML: {output_path.name}")
    return output_path

def split_into_balanced_lines(text, target_language="is"):
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]

    # If text is extremely long (>84), we might need 3 lines, but let's stick to 2 for now and force split
    middle = len(text) // 2
    candidates = []

    bad_starters = set()
    if target_language == "is":
        bad_starters = {"og", "en", "sem", "að", "eða", "því"}
    elif target_language in ["es", "spanish"]:
        bad_starters = {"y", "o", "que", "pero", "de", "en"}
    
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
            
            # Penalty for exceeding max length
            if len(left) > MAX_CHARS_PER_LINE or len(right) > MAX_CHARS_PER_LINE:
                continue
            
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

def _merge_high_cps_events(events: list[dict]) -> list[dict]:
    if not events:
        return events

    merged: list[dict] = []
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
                        combined_words = None
                        curr_words = curr.get("words")
                        next_words = nxt.get("words")
                        if isinstance(curr_words, list) and isinstance(next_words, list):
                            combined_words = curr_words + next_words

                        merged.append(
                            {
                                "start": curr["start"],
                                "end": nxt["end"],
                                "text": combined_text,
                                **({"words": combined_words} if combined_words is not None else {}),
                            }
                        )
                        i += 2
                        continue

        merged.append(curr)
        i += 1

    return merged

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
    # Extract clean job_id stem from various filename patterns
    stem = approved_path.name
    for suffix in [".json", "_ICELANDIC", "_APPROVED", "_normalized", "_SKELETON", "_SKELETON_DONE"]:
        stem = stem.replace(suffix, "")
    timing_mode = _timing_mode()
    strict_mode = timing_mode == "strict"
    strict_max_extend, strict_fragment_shift = _strict_timing_limits()
    
    with open(approved_path, "r", encoding="utf-8") as f:
        data_wrapper = json.load(f)
    
    if isinstance(data_wrapper, dict):
        data = data_wrapper.get("segments", [])
        graphic_zones = data_wrapper.get("graphic_zones", [])
    else:
        data = data_wrapper
        graphic_zones = []

    def is_in_zone(start, end, zones):
        """Check if segment overlaps with any graphic zone."""
        center = start + (end - start) / 2
        for zone in zones:
            # Check center point or overlap
            # Using center point is safer to avoid edge jitters
            z_start = zone.get("startTime", 0)
            z_end = zone.get("endTime", 0)
            if z_start <= center <= z_end:
                return True
        return False

    processed_events = []
    
    # PASS 1: AGGRESSIVE PRE-PROCESS SPLITTING
    for item in data:
        start = item['start']
        end = item['end']
        text = item['text'].replace("\n", " ").strip()
        text = abbreviate_bible_refs(text, target_language)
        words = item.get('words')  # Word-level timing data (may be None for old skeletons)

        if not text:
            continue
        if _is_music_only(text):
            continue
            
        queue = [{'start': start, 'end': end, 'text': text, 'words': words}]

        while queue:
            curr = queue.pop(0)
            curr_text = curr['text']
            curr_len = len(curr_text)
            curr_words = curr.get('words')
            
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
                
                # Use word-level timing if available, otherwise fall back to ratio
                mid_time = _find_word_boundary_time(curr_words, split_point + 1)
                if mid_time is None:
                    # Fallback: ratio-based timing for old skeletons without word data
                    ratio = len(part1_text) / curr_len
                    mid_time = curr['start'] + ((curr['end'] - curr['start']) * ratio)
                
                # Split word data if available (for recursive splits)
                words1, words2 = None, None
                if curr_words:
                    # Find which word the split occurs at
                    chars_seen = 0
                    split_word_idx = 0
                    for idx, w in enumerate(curr_words):
                        chars_seen += len(w.get("text", "")) + 1
                        if chars_seen >= split_point + 1:
                            split_word_idx = idx + 1
                            break
                    words1 = curr_words[:split_word_idx]
                    words2 = curr_words[split_word_idx:]
                
                queue.insert(0, {'start': mid_time, 'end': curr['end'], 'text': part2_text, 'words': words2})
                queue.insert(0, {'start': curr['start'], 'end': mid_time, 'text': part1_text, 'words': words1})
            else:
                processed_events.append(curr)

    # PASS 1.5: CPS RESCUE MERGE
    for _ in range(2):
        merged_events = _merge_high_cps_events(processed_events)
        if len(merged_events) == len(processed_events):
            break
        processed_events = merged_events

    # PASS 1.6: ORPHAN RESCUER (BBC/Netflix Standard)
    # Move dangling words (og, en, að, því, er) to the next block
    orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i+1]

        words = curr['text'].split()
        if not words:
            continue

        last_word = words[-1].lower().strip(".,:;?!\"")
        if last_word in orphans:
            # Move the word to the next block
            word_to_move = words[-1]
            curr['text'] = " ".join(words[:-1])
            next_item['text'] = f"{word_to_move} {next_item['text']}"
            logger.info(f"   🧹 Rescued orphan '{word_to_move}' from block {i+1}")

            curr_words = curr.get("words")
            next_words = next_item.get("words")
            if isinstance(curr_words, list) and curr_words:
                moved_word = curr_words.pop()
                if isinstance(next_words, list):
                    next_words.insert(0, moved_word)
                else:
                    next_words = [moved_word]
                curr["words"] = curr_words
                next_item["words"] = next_words
                if curr_words:
                    curr["end"] = curr_words[-1].get("end", curr["end"])
                if next_words:
                    next_item["start"] = next_words[0].get("start", next_item["start"])

    # PASS 1.7: STRANDED FRAGMENT RESCUER (Reverse Orphan)
    # Detects: [Block N] "ending" [Block N+1] "and. New sentence" -> Merge "and." back to N.
    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i+1]
        
        curr_text = curr['text'].strip()
        next_text = next_item['text'].strip()
        
        if not curr_text or not next_text:
            continue
            
        # Check if current sentence is "open" (no punctuation)
        curr_ends_open = curr_text[-1] not in ".?!\""
        
        if curr_ends_open:
            next_words = next_text.split()
            if not next_words: continue
            
            first_word = next_words[0]
            # Criteria: Short word (<4 chars), ends with sentence punctuation, starting lowercase usually
            # Example: "að." or "til." or "því."
            clean_word = first_word.strip(".,:;?!")
            has_closing_punct = first_word[-1] in ".?!"
            
            # Allow slightly longer words if they are clearly closing a sentence (e.g. "heim.")
            is_fragment = len(clean_word) <= 4 and has_closing_punct
            
            if is_fragment:
                # MOVE THE FRAGMENT BACK
                curr['text'] = curr_text + " " + first_word
                next_item['text'] = " ".join(next_words[1:])

                curr_words = curr.get("words")
                next_words_timing = next_item.get("words")
                used_word_timing = False

                if isinstance(next_words_timing, list) and next_words_timing:
                    moved_word = next_words_timing.pop(0)
                    if isinstance(curr_words, list):
                        curr_words.append(moved_word)
                    else:
                        curr_words = [moved_word]
                    curr["words"] = curr_words
                    next_item["words"] = next_words_timing
                    if curr_words:
                        curr["end"] = curr_words[-1].get("end", curr["end"])
                    if next_words_timing:
                        next_item["start"] = next_words_timing[0].get("start", next_item["start"])
                    used_word_timing = True

                if not used_word_timing and not strict_mode:
                    # ADJUST TIMING (Heuristic: Shift boundary by 0.35s)
                    # We steal 0.35s from Next and give it to Curr to account for the spoken word
                    shift = 0.35
                    curr['end'] = curr['end'] + shift
                    next_item['start'] = next_item['start'] + shift
                elif not used_word_timing and strict_mode and strict_fragment_shift > 0:
                    shift = strict_fragment_shift
                    curr['end'] = curr['end'] + shift
                    next_item['start'] = next_item['start'] + shift

                # Ensure we didn't break causality (start > end)
                if next_item['start'] > next_item['end']:
                     # If next became too short, clamp it (this effectively squashes next)
                     next_item['start'] = max(next_item['end'] - 0.1, curr['end'])

                logger.info(f"   🩹 Rescued stranded fragment '{first_word}' back to block {i+1}")

    # PASS 2: APPLY TIMING RULES & CPS OPTIMIZER
    final_srt_blocks = []
    normalized_events = []
    srt_counter = 1
    
    for i in range(len(processed_events)):
        current = processed_events[i]
        
        char_count = len(current['text'])
        # CPS Optimizer: Allow extending duration to meet language-specific CPS target
        ideal_cps, _ = get_cps_for_language(target_language)
        required_time = char_count / ideal_cps
        original_duration = current['end'] - current['start']
        current_words = current.get("words")
        word_end = None
        if isinstance(current_words, list) and current_words:
            word_end = current_words[-1].get("end")
        
        next_start = processed_events[i+1]['start'] if i < len(processed_events) - 1 else 999999
        max_end_time = next_start - GAP_SECONDS
        
        if strict_mode:
            base_end = word_end if word_end is not None else current["end"]
            max_extend = max(0.0, strict_max_extend)
            extended_target = base_end + max_extend
            actual_end = min(extended_target, max_end_time)
        else:
            # Allow stealing up to 0.8s from the gap/next segment if available
            extended_target = current['start'] + max(original_duration, MIN_DURATION, required_time)
            actual_end = min(extended_target, max_end_time)

        actual_end = max(actual_end, current["start"] + 0.01)
        current["end"] = actual_end
        
        # If still too fast, log it
        final_duration = actual_end - current['start']
        final_cps = char_count / final_duration if final_duration > 0 else 0
        if final_cps > 20:
             logger.warning(f"   ⚠️ High CPS ({final_cps:.1f}): {current['text'][:20]}...")

        if final_duration < 0.5:
            logger.warning(f"   ⚠️ Subtitle {i+1} squashed to <0.5s: {current['text'][:20]}...")

        lines = split_into_balanced_lines(current['text'], target_language)
        
        # Check Graphic Zones for positioning
        position_tag = ""
        if is_in_zone(current['start'], actual_end, graphic_zones):
             position_tag = "{\\an8}"

        final_text = position_tag + "\n".join(lines)
        
        final_srt_blocks.append(f"{srt_counter}\n{format_timestamp(current['start'])} --> {format_timestamp(actual_end)}\n{final_text}\n\n")
        normalized_events.append({
            "start": current['start'],
            "end": actual_end,
            "lines": lines
        })
        srt_counter += 1

    # SANITY CHECK
    # Count only segments that will produce output (exclude music-only which are filtered in PASS 1)
    valid_input_count = sum(
        1 for item in data 
        if item.get('text', '').strip() and not _is_music_only(item.get('text', ''))
    )
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

    # SAVE VTT (WebVTT for web players)
    vtt_path = config.SRT_DIR / f"{stem}.vtt"
    generate_vtt(normalized_events, vtt_path)

    # SAVE TTML (Netflix/YouTube/Broadcast compatible)
    ttml_path = config.SRT_DIR / f"{stem}.ttml"
    generate_ttml(normalized_events, ttml_path, lang_code=target_language)

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

    try:
        qa_srt = _collect_srt_qc(normalized_events)
        omega_db.update(stem, meta={"qa_srt": qa_srt})
    except Exception as e:
        logger.warning("   ⚠️ SRT QA summary failed: %s", e)

    try:
        qa_timing = _collect_timing_qc(processed_events)
        qa_timing["mode"] = timing_mode
        omega_db.update(stem, meta={"qa_timing": qa_timing})
    except Exception as e:
        logger.warning("   ⚠️ Timing QA summary failed: %s", e)

    return srt_path, normalized_path
