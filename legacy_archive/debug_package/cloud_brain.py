import os
import json
import re
import time
import shutil
import math
from pathlib import Path
from typing import Optional
from google.cloud import storage
import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    Part,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
    GenerationConfig,
)
from concurrent.futures import ThreadPoolExecutor, as_completed
from lock_manager import ProcessLock
import system_health
import omega_db

# --- ⚙️ CONFIGURATION ---
PROJECT_ID = "sermon-translator-system"
BUCKET_NAME = "audio-hq-sermon-translator-55"
LOCATION = "global"  # Gemini 3.0 Pro Preview requires global endpoint

# --- PERFORMANCE SETTINGS ---
# --- PERFORMANCE SETTINGS ---
BATCH_SIZE = 40                  # Reduced from 80 to 40 for higher stability and fewer retries
USE_FLASH_FOR_SPEED = False      # Switch to Pro for final runs — Pro almost never truncates
MAX_WORKERS = 10                 # Number of parallel batches (10 = ~10x speedup)

# --- PATHS ---
BASE_DIR = Path(os.getcwd())
INBOX = BASE_DIR / "2_READY_FOR_CLOUD"
PROCESSED_DIR = INBOX / "processed"
OUTBOX = BASE_DIR / "3_TRANSLATED_DONE"
ERROR_DIR = BASE_DIR / "99_ERRORS"

PROCESSED_DIR.mkdir(exist_ok=True)
OUTBOX.mkdir(exist_ok=True)
ERROR_DIR.mkdir(exist_ok=True)

# --- CREDENTIALS HANDLING ---
_CREDENTIALS_READY = False

def ensure_credentials() -> bool:
    """
    Make sure GOOGLE_APPLICATION_CREDENTIALS points to a real file.
    - If the env var is already set and the file exists, use it.
    - Else, if service_account.json exists in BASE_DIR, set env var to it.
    - Otherwise, log a clear error and pause upstream processing.
    """
    global _CREDENTIALS_READY
    if _CREDENTIALS_READY:
        return True

    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path:
        cred_path = Path(env_path).expanduser()
        if cred_path.exists():
            _CREDENTIALS_READY = True
            return True
        print(f"   ☁️ Upload Error: GOOGLE_APPLICATION_CREDENTIALS points to missing file: {cred_path}")
        return False

    default_path = BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        _CREDENTIALS_READY = True
        return True

    print("   ☁️ Upload Error: service_account.json not found.")
    print("      Place it at the project root or set GOOGLE_APPLICATION_CREDENTIALS to a valid path.")
    return False

# --- SAFETY SETTINGS ---
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

# --- THEOLOGICAL GLOSSARY (Consistency is King) ---
GLOSSARY = {
    # --- CORE THEOLOGY ---
    "I AM": "ÉG ER",                # Critical: Ensures John 8:58/Exodus 3:14 accuracy
    "Lord": "Drottinn",
    "Grace": "Náð",
    "Salvation": "Frelsi",
    "Savior": "Frelsari",
    "Holy Spirit": "Heilagur Andi",
    "Scripture": "Ritningin",
    "Gospel": "Fagnaðarerindið",
    "Sin": "Synd",
    "Repentance": "Iðrun",
    "Faith": "Trú",
    "Eternal Life": "Eilíft líf",
    "Disciple": "Lærisveinn",
    "Apostle": "Postuli",
    "Resurrection": "Upprisa",
    "Redemption": "Endurlausn",
    "Gentiles": "Heiðingjar",

    # --- MINISTRY TITLES & ROLES ---
    "Evangelist": "Trúboði",        # FIX: "Guðspjallamaður" is archaic (Gospel writers).
                                    # "Trúboði" is correct for modern preachers.
    "Covenant Partners": "Sáttmáls-bakhjarlar",
    "Partners": "Bakhjarlar",       # Warmer/stronger than 'samstarfsaðilar'
    "Harvest": "Uppskera",          # Biblical metaphor
    "Anointing": "Smurning",
    "Revival": "Vakning",
    "Gospel Campaign": "Trúboðsátak", # Avoids "herferð" (military campaign)

    # --- BROADCAST TERMS ---
    "Sponsored by": "Kostaður af",  # Fixes the grammar error
    "In Touch": "In Touch",         # Do not translate show titles
    "Shake the Nations": "Shake the Nations",
    "Desire of All Nations": "Þrá allra þjóða",
    "Face to Face": "Face to Face", # Keep branded segment titles in English
    "Behind the Signs": "Behind the Signs"
}

# --- STRUCTURED OUTPUT SCHEMA (Dictionary format – 100% enforced on Gemini 2.5 global endpoint) ---
RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "start": {"type": "number"},
            "end": {"type": "number"},
            "text": {"type": "string"}
        },
        "required": ["id", "start", "end", "text"]
    }
}

GENERATION_CONFIG = GenerationConfig(
    response_mime_type="application/json",
    response_schema=RESPONSE_SCHEMA,
    temperature=0.1 if USE_FLASH_FOR_SPEED else 0.2,
    max_output_tokens=8192,
)

def upload_audio(local_path: str, destination_name: str) -> Optional[str]:
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(destination_name)
        if not blob.exists():
            print(f"   ☁️ Uploading new audio: {destination_name}")
            blob.upload_from_filename(local_path)
        else:
            print(f"   ☁️ Using cached audio: {destination_name}")
        return f"gs://{BUCKET_NAME}/{destination_name}"
    except Exception as e:
        print(f"   ☁️ Upload Error: {e}")
        return None

def move_to_error(json_file: Path, audio_path: Optional[Path], reason: str):
    print(f"   🚨 Moving {json_file.name} to ERROR: {reason}")
    try:
        shutil.move(str(json_file), str(ERROR_DIR / json_file.name))
        if audio_path and audio_path.exists():
            shutil.move(str(audio_path), str(ERROR_DIR / audio_path.name))
    except Exception:
        pass

def clean_json_response(text: str) -> str:
    """Strip markdown fences and clean malformed JSON responses from Gemini."""
    # Remove markdown code fences
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def safe_load_json(text: str):
    """Robust JSON parser for handling malformed Gemini responses."""
    text = text.strip()
    
    # Remove markdown fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    # Try standard parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Most common issue: literal newlines within string values
        # Strategy: Convert the malformed JSON to a single line, then parse
        # This works because the schema is simple and predictable
        
        # First, let's try a simple fix: replace all newlines that appear
        # between opening and closing brackets with spaces
        try:
            # Normalize whitespace: replace actual newlines with spaces
            # but preserve the JSON structure
            fixed = text.replace('\n', ' ')
            # Clean up multiple spaces
            fixed = re.sub(r'\s+', ' ', fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            # If that doesn't work, print debug info
            lines = text.split('\n')
            if hasattr(e, 'lineno') and e.lineno <= len(lines):
                print(f"      JSON Error at line {e.lineno}: {lines[e.lineno-1][:100]}")
            raise

def verify_batch(source_batch, translated_batch):
    """Quick heuristic check to catch dropped lines."""
    if len(source_batch) != len(translated_batch):
        return False, f"Count mismatch: {len(source_batch)} vs {len(translated_batch)}"
    
    for src, trans in zip(source_batch, translated_batch):
        # Flag if source has text but translation is empty
        if len(src['text'].strip()) > 0 and len(trans.get('text', '').strip()) == 0:
            return False, f"Dropped segment ID {src['id']}"
            
    return True, "OK"

def translate_batch_smart(model, audio_part, target_batch, pre_context, post_context, batch_num, total_batches):
    print(f"      [Batch {batch_num}/{total_batches}] Sending {len(target_batch)} segments...")
    start_time = time.time()

    prompt = f"""
ROLE: You are the Lead Translator for a premium Christian Television Network in Iceland (e.g., Omega/Lindin).

CONTEXT BEFORE (use to fix broken sentences at start):
{json.dumps(pre_context, ensure_ascii=False)}

TARGET BATCH (Translate this):
{json.dumps(target_batch, ensure_ascii=False)}

CONTEXT AFTER (use to fix broken sentences at end):
{json.dumps(post_context, ensure_ascii=False)}

GLOSSARY (Use these terms):
{json.dumps(GLOSSARY, ensure_ascii=False)}

TASK: Translate the TARGET BATCH into fluent, spiritually resonant Icelandic subtitles.

THE "GOLD STANDARD" GUIDELINES:

1. DYNAMIC REGISTER (Tone Switching):
   - **DURING PRAYER & SCRIPTURE:** Use the formal/reverent tone ("Þér", "Faðirinn"). Use the vocabulary of the standard Icelandic Bible.
   - **DURING INTERVIEWS & CASUAL CHAT:** When the host speaks to a guest (friend), use a natural, respectful conversational tone. Use "Þú" (singular) for friends, not the formal plural.
   - **AVOID ARCHAIC TITLES:** Never use "Guðspjallamaður" for a modern person; use "Trúboði" (Evangelist).

2. THEOLOGICAL PRECISION (Biblíufélagið Standard): Use the vocabulary and phrasing of the standard Icelandic Bible for scripture and spiritual concepts.
   - Example: Translate "Saved" as "Frelsaður" (in evangelical contexts) or "Hólpinn" (in formal biblical contexts) as appropriate.
   - Maintain reverence. Capitalize references to the Deity (e.g., "Hann", "Faðirinn") if emphasizing divinity, consistent with Christian literature.
   - CRITICAL: "I AM" (Exodus 3:14, John 8:58) MUST be translated as "ÉG ER", never as "ég er" or any other variation.

3. NATIVE FLOW (No "Translationese"):
   - Do not translate word-for-word. Listen to the *preacher's intent* and rewrite it as an Icelandic pastor would say it.
   - CRITICAL GRAMMAR: Fix singular/plural mismatches instantly. (e.g., Never translate "Who... their" literally. Change to "Hverjum... þeirra" or "Hver... hans" to match agreement).
   - **GRAMMAR TRAP:** When translating "Sponsored by", ALWAYS use "Kostaður af" (masculine). Never use "Kostinn".
   - **PROGRAM TITLES**: Keep "In Touch", "Shake the Nations", "Face to Face" in English. Do NOT translate TV show names or ministry brands.
   - **FAST SPEECH:** If the speaker stutters or repeats words (e.g., "I... I... I want to say"), REMOVE the stutters in the translation. Keep it clean and concise.

4. INTELLIGENT REPAIR:
   - The audio source has stutters and missing verbs. Do not translate errors. Restore the sentence to its intended glory before translating.
   - Fix broken sentences that span across batch boundaries using the provided CONTEXT.

4. CLEAN VERBATIM:
   - Remove distracting filler words ("Jæja", "You know") unless they carry emotional weight. Keep the subtitles clean and readable.

5. FORMAT:
   - Return a COMPLETE JSON array matching the Target Batch exactly (same ids, starts, ends).
   - NEVER truncate.

6. MUSIC & SINGING (CRITICAL):
   - If a segment contains **singing, worship music, or lyrics**, output an EMPTY STRING `""` for the `text` field.
   - Do NOT translate lyrics. We do not subtitle singing.
   - If the segment is mixed (speaking then singing), translate ONLY the speaking part.

OUTPUT: Return ONLY the JSON array.
"""

    for attempt in range(1, 5):
        try:
            response = model.generate_content(
                [audio_part, prompt],
                generation_config=GENERATION_CONFIG,
                safety_settings=SAFETY_SETTINGS,
            )
            text = response.text.strip()

            # Remove markdown if present
            text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE).strip()

            # Detect obvious truncation
            if text.count('{') > text.count('}') or not text.endswith(']'):
                raise ValueError("Truncated response detected")

            parsed = json.loads(text)
            
            # --- BATCH VERIFICATION (Catch dropped segments) ---
            is_valid, msg = verify_batch(target_batch, parsed)
            if not is_valid:
                print(f"      ⚠️ Verification Failed: {msg}. Retrying...")
                raise ValueError(f"Verification failed: {msg}")
            # ------------------------------------------------
            
            if len(parsed) != len(target_batch):
                raise ValueError(f"Incomplete batch: expected {len(target_batch)}, got {len(parsed)}")

            elapsed = time.time() - start_time
            print(f"      [Batch {batch_num}/{total_batches}] ✓ Complete in {elapsed:.1f}s")
            return parsed

        except Exception as e:
            is_truncation = "Truncated response detected" in str(e)
            print(f"      [Batch {batch_num}/{total_batches}] ⚠️ Attempt {attempt}/4 failed: {e}")
            
            # If truncation detected, split IMMEDIATELY. Do not retry the same size.
            if is_truncation or attempt == 4:
                if is_truncation:
                    print(f"      ✂️ Truncation detected. Splitting batch {batch_num} immediately...")
                else:
                    print(f"      Splitting batch {batch_num} into two smaller batches (max retries)...")
                
                # Final fallback: split this batch in half recursively
                mid = len(target_batch) // 2
                
                # Calculate new contexts for split
                first_half = target_batch[:mid]
                second_half = target_batch[mid:]
                
                # Context for first half: Same pre, post is start of second half
                first_post = second_half[:5]
                
                # Context for second half: Pre is end of first half, same post
                second_pre = first_half[-5:]
                
                return (translate_batch_smart(model, audio_part, first_half, pre_context, first_post, batch_num, total_batches) +
                        translate_batch_smart(model, audio_part, second_half, second_pre, post_context, batch_num, total_batches))
            
            # Exponential Backoff (5s, 10s, 20s, 40s)
            sleep_time = 5 * (2 ** (attempt - 1))
            print(f"      ⏳ Retrying in {sleep_time}s...")
            time.sleep(sleep_time)

    raise Exception("Unrecoverable failure")

def process_translation(json_file: Path):
    print(f"\n🧠 Waking up Brain for: {json_file.name}")
    
    stem = json_file.stem.replace("_SKELETON", "")
    audio_path = BASE_DIR / "1_INBOX" / f"DONE_{stem}.mp3"
    if not audio_path.exists():
        audio_path = INBOX / f"{stem}.mp3"
    
    if not audio_path.exists():
        print("   ❌ Missing Audio")
        move_to_error(json_file, None, "Missing Audio")
        return

    gcs_uri = upload_audio(str(audio_path), f"audio_cache/{stem}.mp3")
    if not gcs_uri:
        return

    vertexai.init(project=PROJECT_ID, location=LOCATION)

    model_id = "gemini-3-pro-preview"
    model = GenerativeModel(model_id)
    mode = "FAST TESTING MODE" if USE_FLASH_FOR_SPEED else "MAX QUALITY MODE"
    print(f"   ✨ Connected to {model_id.upper()} ({mode})")

    with open(json_file, "r", encoding="utf-8") as f:
        full_data_wrapper = json.load(f)

    # HANDLE NEW FORMAT (Dict vs List)
    if isinstance(full_data_wrapper, dict):
        full_data = full_data_wrapper["segments"]
        is_vip = full_data_wrapper.get("needs_human_review", False)
    else:
        full_data = full_data_wrapper
        is_vip = False

    audio_part = Part.from_uri(gcs_uri, mime_type="audio/mpeg")  # Sent once per sermon
    full_context_str = json.dumps(full_data, ensure_ascii=False)

    # Estimate total batches (for progress bar only)
    estimated_batches = math.ceil(len(full_data) / BATCH_SIZE)
    print(f"   📦 ~{estimated_batches} batch(es), {len(full_data)} segments total (VIP: {is_vip})")

    translated_segments = []
    overall_start = time.time()

    try:
        # Parallel Processing
        futures = {}
        results_map = {}
        
        print(f"      🚀 Launching batches (Dynamic Semantic Slicing)...")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            current_idx = 0
            batch_num = 1
            
            while current_idx < len(full_data):
                # 1. Determine Target End (Standard Batch Size)
                target_end = min(current_idx + BATCH_SIZE, len(full_data))
                
                # 2. Semantic Lookahead: Find best split point
                # Look for . ? ! in the window [target_end - 5, target_end + 5]
                best_split = target_end
                
                if target_end < len(full_data):
                    search_start = max(current_idx + BATCH_SIZE - 5, current_idx)
                    search_end = min(current_idx + BATCH_SIZE + 5, len(full_data))
                    
                    found_split = False
                    for k in range(search_end - 1, search_start - 1, -1):
                        text = full_data[k]['text'].strip()
                        if text.endswith('.') or text.endswith('?') or text.endswith('!'):
                            best_split = k + 1
                            found_split = True
                            break
                    
                    if found_split:
                        pass # We found a good split!
                    else:
                        pass # No punctuation found, sticking to hard limit
                
                # 3. Create Batch
                batch = full_data[current_idx : best_split]
                
                # Context Sliding Window
                pre_start = max(0, current_idx - 5)
                pre_context = full_data[pre_start : current_idx]
                
                post_end = min(len(full_data), best_split + 5)
                post_context = full_data[best_split : post_end]

                future = executor.submit(
                    translate_batch_smart, 
                    model, audio_part, batch, pre_context, post_context, batch_num, estimated_batches
                )
                futures[future] = batch_num
                
                # Advance
                current_idx = best_split
                batch_num += 1

            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_result = future.result()
                    results_map[batch_idx] = batch_result
                    print(f"      → Batch {batch_idx} finished ({len(batch_result)} segments)")
                    # Update progress for each completed batch
                    current_progress = 70.0 + (len(results_map) / estimated_batches) * 20.0 # 70-90%
                    omega_db.update(stem, status=f"Translating Batch {batch_idx}/{estimated_batches} complete", progress=current_progress)
                except Exception as exc:
                    print(f"      💥 Batch {batch_idx} generated an exception: {exc}")
                    omega_db.update(stem, status=f"Error: Batch {batch_idx} failed", progress=0)
                    raise exc

        # Assemble results in order
        sorted_batches = sorted(results_map.keys())
        for b_idx in sorted_batches:
            translated_segments.extend(results_map[b_idx])

        output_file = OUTBOX / f"{stem}_ICELANDIC.json"
        
        # If VIP, we save the Wrapper (Dict) so the Review Gate sees the flag
        # If Auto, we can save the List (or just save Dict and have Publisher handle it)
        final_payload = {
            "needs_human_review": is_vip,
            "segments": translated_segments
        }
        
        # --- FINAL INTEGRITY CHECK ---
        if len(translated_segments) != len(full_data):
            print(f"   ❌ CRITICAL ERROR: Segment count mismatch! Input: {len(full_data)}, Output: {len(translated_segments)}")
            omega_db.update(stem, status="Error: Segment Count Mismatch", progress=0)
            move_to_error(json_file, None, "Segment Count Mismatch")
            return

        # Sort by ID to be safe
        translated_segments.sort(key=lambda x: x['id'])
        
        # Save Final JSON
        final_output = BASE_DIR / "3_TRANSLATED_DONE" / f"{stem}_ICELANDIC.json"
        with open(final_output, "w", encoding="utf-8") as f:
            json.dump(translated_segments, f, indent=2, ensure_ascii=False)

        total_time = time.time() - overall_start
        print(f"   ✅ FULL TRANSLATION COMPLETE in {total_time:.1f}s → {final_output.name}")
        omega_db.update(stem, stage="REVIEW", status="Translation Complete", progress=90.0)
        shutil.move(str(json_file), str(PROCESSED_DIR / json_file.name))

    except Exception as e:
        print(f"   💥 Process Failed: {e}")
        move_to_error(json_file, audio_path, str(e))
        omega_db.update(stem, status=f"Error: Process Failed - {str(e)}", progress=0)

# --- RUNNER ---
if __name__ == "__main__":
    with ProcessLock("cloud_brain"):
        print("🚀 Gemini 3.0 Cloud Brain Active – Production Ready")
        while True:
            system_health.update_heartbeat("cloud_brain")
            if not ensure_credentials():
                time.sleep(10)
                continue
            for skeleton in INBOX.glob("*_SKELETON.json"):
                process_translation(skeleton)
            time.sleep(5)
