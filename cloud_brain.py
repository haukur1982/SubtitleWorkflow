import os
import json
import time
import shutil
import math
import datetime
from pathlib import Path
from typing import Optional, List
from google.cloud import storage
import vertexai
from vertexai.preview import caching
from vertexai.generative_models import GenerativeModel, Part, SafetySetting, HarmCategory, HarmBlockThreshold, GenerationConfig
from concurrent.futures import ThreadPoolExecutor, as_completed
from lock_manager import ProcessLock
import system_health
import omega_db

# --- CONFIGURATION ---
PROJECT_ID = "sermon-translator-system"
BUCKET_NAME = "audio-hq-sermon-translator-55"
LOCATION = "us-central1"
MAX_WORKERS = 3
BATCH_SIZE = 60

# --- PATHS ---
BASE_DIR = Path(os.getcwd())
INBOX = BASE_DIR / "2_VAULT" / "Data"
OUTBOX = BASE_DIR / "3_EDITOR"
ERROR_DIR = BASE_DIR / "99_ERRORS"
PROCESSED_DIR = BASE_DIR / "2_VAULT" / "Data" / "processed"
STAGING_DIR = BASE_DIR / "3_TRANSLATED_DONE"

# Ensure directories exist
for d in [INBOX, OUTBOX, ERROR_DIR, PROCESSED_DIR, STAGING_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- CREDENTIALS ---
def ensure_credentials() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"): return True
    default_path = BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True
    return False

# --- GLOSSARY & SAFETY ---
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

# --- ENRICHED GLOSSARY & STYLE GUIDE ---
GLOSSARY = {
    # Divine Titles
    "I AM": "ÉG ER", 
    "Lord": "Drottinn", 
    "Savior": "Frelsari", 
    "Holy Spirit": "Heilagur Andi",
    # Theological Terms
    "Grace": "Náð", 
    "Salvation": "Frelsi",
    "Gospel": "Fagnaðarerindið", 
    "Sin": "Synd", 
    "Repentance": "Iðrun", 
    "Covenant": "Sáttmáli",    # NOT Samningur
    "Anointing": "Smurning",   # NOT Áburður
    "Revival": "Vakning",      # NOT Endurvakning
    "Harvest": "Uppskera",
    "Gentiles": "Heiðingjar",  # NOT Útlendingar
    "Evangelist": "Trúboði",   # NOT Guðspjallamaður
    # Ministry Terms
    "Partners": "Bakhjarlar",  # NOT Samstarfsaðilar
    "Gospel Campaign": "Trúboðsátak",
    "Sponsored by": "Kostaður af", # NOT Kostinn af
    # Proper Nouns (Keep English)
    "In Touch": "In Touch", 
    "Shake the Nations": "Shake the Nations"
}

# --- HELPERS ---
def upload_to_gcs(local_path: Path, destination_name: str) -> Optional[str]:
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(destination_name)
        if not blob.exists():
            print(f"   ☁️ Uploading audio: {local_path.name}...")
            blob.upload_from_filename(str(local_path))
        return f"gs://{BUCKET_NAME}/{destination_name}"
    except Exception as e:
        print(f"   ❌ GCS Upload Failed: {e}")
        return None

def create_context_cache(gcs_uri: str, stem: str) -> Optional[str]:
    print(f"   ⚡️ Creating Context Cache (Gemini 2.5 Pro) for {stem}...")
    
    # THE OMEGA STANDARD PROMPT
    system_instruction = f"""
    ROLE: You are the Lead Translator for Omega TV (Iceland). 
    Your goal is to translate English Christian content into **fluent, idiomatic Icelandic**.
    
    1. THEOLOGICAL ACCURACY (Strict):
       - God is addressed as "Þér" (Reverent Formal).
       - Humans/Friends are addressed as "Þú" (Casual).
       - Never use "ég er" for God's title; use "ÉG ER".
       
    2. AVOID "TRANSLATION-ESE" (Anglicisms):
       - "Died for you" -> "Dó vegna þín" (NOT "fyrir þig").
       - "Believe in miracles" -> "Trúa á kraftaverk" (NOT "í kraftaverk").
       - "Share a verse" -> "Lesa vers" or "Gefa orð" (NOT "deila versi").
       - "On fire for God" -> "Brennandi í andanum" (NOT "á eldi").
       - "Bless you" -> "Guð blessi þig" (NOT just "Bless", unless saying goodbye).
       
    3. BROADCAST CLARITY:
       - Remove stutters (e.g., "I... I think" -> "Ég held").
       - Use active voice. Make it sound like it was written by an Icelander.

    4. MUSIC & LYRICS:
       - **IGNORE** all singing, choir, and song lyrics. 
       - If a segment is purely singing, return an empty string "" for the text.
       - Do NOT translate lyrics.
       
    GLOSSARY: {json.dumps(GLOSSARY, ensure_ascii=False)}
    
    OUTPUT FORMAT: Return a JSON array matching the input IDs exactly.
    """
    try:
        cached_content = caching.CachedContent.create(
            model_name="gemini-2.5-pro", # QUALITY MODEL
            display_name=f"cache_{stem}",
            system_instruction=system_instruction,
            contents=[Part.from_uri(gcs_uri, mime_type="audio/mpeg")],
            ttl=datetime.timedelta(minutes=120),
        )
        print(f"   ✅ Cache Active! ID: {cached_content.name}")
        return cached_content.name
    except Exception as e:
        print(f"   ❌ Cache Creation Failed: {e}")
        return None

def translate_batch_with_cache(cache_name: str, target_batch: List[dict], pre: List[dict], post: List[dict], batch_num: int):
    model = GenerativeModel.from_cached_content(cached_content=cache_name)
    prompt = f"""
    CONTEXT BEFORE: {json.dumps(pre, ensure_ascii=False)}
    TARGET BATCH: {json.dumps(target_batch, ensure_ascii=False)}
    CONTEXT AFTER: {json.dumps(post, ensure_ascii=False)}
    TASK: Translate TARGET BATCH to Icelandic. Use cached audio for context.
    """
    generation_config = GenerationConfig(
        response_mime_type="application/json",
        response_schema={"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}}},
        temperature=0.3
    )
    for attempt in range(1, 4):
        try:
            response = model.generate_content(prompt, generation_config=generation_config, safety_settings=SAFETY_SETTINGS)
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:-3]
            parsed = json.loads(text)
            if len(parsed) != len(target_batch): raise ValueError("Count mismatch")
            print(f"      ✓ Batch {batch_num} Done")
            return parsed
        except Exception as e:
            print(f"      ⚠️ Batch {batch_num} Retry {attempt}/3: {e}")
            time.sleep(2 ** attempt)
    raise Exception(f"Batch {batch_num} Failed")

def process_translation(json_file: Path):
    print(f"\n🚀 STARTING TRANSLATION: {json_file.name}")
    stem = json_file.stem.replace("_SKELETON", "")
    job = omega_db.get_job(stem)
    if job and job.get("stage") in ["TRANSLATED", "REVIEW", "COMPLETED"]:
        shutil.move(str(json_file), str(PROCESSED_DIR / json_file.name))
        return

    # Audio Logic
    audio_path = INBOX / f"{stem}.mp3"
    if not audio_path.exists(): audio_path = BASE_DIR / "1_INBOX" / f"DONE_{stem}.mp3"
    if not audio_path.exists(): return

    # Init & Cache
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    gcs_uri = upload_to_gcs(audio_path, f"audio_cache/{stem}.mp3")
    if not gcs_uri: return
    cache_name = create_context_cache(gcs_uri, stem)
    if not cache_name: return

    # Load Data
    with open(json_file, 'r') as f: wrapper = json.load(f)
    full_data = wrapper.get("segments", wrapper) if isinstance(wrapper, dict) else wrapper
    meta = wrapper.get("meta", {}) if isinstance(wrapper, dict) else {}

    # Run Translation
    translated_segments = []
    results_map = {}
    print(f"   🏗️ Translating {len(full_data)} segments with Gemini 2.5 Pro...")
    
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            current_idx = 0
            batch_num = 1
            while current_idx < len(full_data):
                target_end = min(current_idx + BATCH_SIZE, len(full_data))
                batch = full_data[current_idx:target_end]
                pre = full_data[max(0, current_idx-5):current_idx]
                post = full_data[target_end:min(len(full_data), target_end+5)]
                future = executor.submit(translate_batch_with_cache, cache_name, batch, pre, post, batch_num)
                futures[future] = batch_num
                current_idx = target_end
                batch_num += 1
            
            for future in as_completed(futures):
                system_health.update_heartbeat("cloud_brain")
                idx = futures[future]
                results_map[idx] = future.result()
                omega_db.update(stem, progress=int((len(results_map)/batch_num)*100))

        for i in sorted(results_map.keys()): translated_segments.extend(results_map[i])

        # SAVE INTERMEDIATE FILE (Handover to Editor)
        mid_file = STAGING_DIR / f"{stem}_TRANSLATED.json"
        # We assume if the skeleton has "needs_human_review", we keep it here
        # But we save it as a simple list or dict for the Editor to pick up
        # 6. Save Output
        mode = meta.get("mode", "REVIEW") # Default to review for safety
        
        if mode == "AUTO":
             # Auto-Approve: Save directly as _APPROVED.json
             # MUST MERGE TIMING FIRST
             merged_output = []
             trans_map = {item['id']: item['text'] for item in translated_segments}
             
             for seg in full_data:
                 seg_id = seg['id']
                 merged_output.append({
                     "id": seg_id,
                     "start": seg['start'],
                     "end": seg['end'],
                     "text": trans_map.get(seg_id, seg.get('text', ''))
                 })
                 
             output_path = OUTBOX / f"{stem}_APPROVED.json"
             print(f"   🤖 Auto-Pilot: Auto-Approving {output_path.name}")
             omega_db.update(stem, stage="FINALIZING", status="Auto-Approved", progress=80.0)
             
             with open(output_path, "w", encoding="utf-8") as f:
                json.dump(merged_output, f, indent=2, ensure_ascii=False)

        else:
             # Human Review: Save as _ICELANDIC.json
             # MUST SAVE SOURCE + TRANSLATION for Editor
             payload = {
                 "source_data": full_data,
                 "translated_data": translated_segments
             }
             
             output_path = OUTBOX / f"{stem}_ICELANDIC.json"
             print(f"   👤 Human Review: Saved {output_path.name}")
             omega_db.update(stem, stage="REVIEW", status="Waiting for Review", progress=70.0)

             with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            
        # Move input skeleton to processed? Or just leave it in Vault?
        # Leave it in Vault/Data as record.
        
        # Remove from INBOX queue (which is Vault/Data, so we rename it to indicate done?)
        # Actually, Cloud Brain scans for _SKELETON.json. 
        # We should rename it to _SKELETON_DONE.json to stop re-processing.
        
        done_skeleton = INBOX / f"{stem}_SKELETON_DONE.json"
        shutil.move(str(json_file), str(done_skeleton))

    except Exception as e:
        print(f"   💥 Translation Failed: {e}")
        omega_db.update(stem, status=f"Error: {str(e)[:50]}", progress=0)
        shutil.move(str(json_file), str(ERROR_DIR / json_file.name))
    
    finally:
        # --- COST OPTIMIZATION: CLEANUP CREW ---
        print("   🧹 Cleanup Crew: Removing cloud resources to save costs...")
        
        # 1. Evict Cache (Stop the hourly rent)
        try:
            if 'cache_name' in locals() and cache_name:
                caching.CachedContent(name=cache_name).delete()
                print(f"      ✅ Cache Evicted: {cache_name}")
        except Exception as e:
            print(f"      ⚠️ Cache Eviction Failed: {e}")

        # 2. Delete Audio from GCS (Stop storage costs)
        try:
            if 'gcs_uri' in locals() and gcs_uri:
                blob_name = f"audio_cache/{stem}.mp3"
                storage_client = storage.Client()
                bucket = storage_client.bucket(BUCKET_NAME)
                blob = bucket.blob(blob_name)
                blob.delete()
                print(f"      ✅ GCS Blob Deleted: {blob_name}")
        except Exception as e:
            print(f"      ⚠️ GCS Blob Deletion Failed: {e}")

if __name__ == "__main__":
    # lock = ProcessLock("cloud_brain")
    # with lock:
    print("✅ Cloud Brain Active (Translator Only)")
    while True:
        system_health.update_heartbeat("cloud_brain")
        if ensure_credentials():
            for f in INBOX.glob("*_SKELETON.json"):
                process_translation(f)
        time.sleep(5)
