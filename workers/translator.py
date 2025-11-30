import os
import json
import time
import shutil
import math
import datetime
import logging
from pathlib import Path
from typing import Optional, List
from google.cloud import storage
import vertexai
from vertexai.preview import caching
from vertexai.generative_models import GenerativeModel, Part, Content, SafetySetting, HarmCategory, HarmBlockThreshold, GenerationConfig
from concurrent.futures import ThreadPoolExecutor, as_completed
import config
import omega_db
import system_health

logger = logging.getLogger("OmegaManager.Translator")

# --- CONFIG ---
PROJECT_ID = "sermon-translator-system"
BUCKET_NAME = "audio-hq-sermon-translator-55"
LOCATION = config.GEMINI_LOCATION
MAX_WORKERS = 3
BATCH_SIZE = 60

SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

GLOSSARY = {
    "I AM": "ÉG ER", "Lord": "Drottinn", "Savior": "Frelsari", "Holy Spirit": "Heilagur Andi",
    "Grace": "Náð", "Salvation": "Frelsi", "Gospel": "Fagnaðarerindið", "Sin": "Synd",
    "Repentance": "Iðrun", "Covenant": "Sáttmáli", "Anointing": "Smurning", "Revival": "Vakning",
    "Harvest": "Uppskera", "Gentiles": "Heiðingjar", "Evangelist": "Trúboði",
    "Partners": "Bakhjarlar", "Gospel Campaign": "Trúboðsátak", "Sponsored by": "Kostaður af",
    "In Touch": "In Touch", "Shake the Nations": "Shake the Nations"
}

def ensure_credentials() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"): return True
    default_path = config.BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True
    return False

def upload_to_gcs(local_path: Path, destination_name: str) -> Optional[str]:
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(destination_name)
        if not blob.exists():
            logger.info(f"☁️ Uploading audio: {local_path.name}...")
            blob.upload_from_filename(str(local_path))
        return f"gs://{BUCKET_NAME}/{destination_name}"
    except Exception as e:
        logger.error(f"GCS Upload Failed: {e}")
        return None

import profiles

def create_context_cache(gcs_uri: str, stem: str, target_language: str = "Icelandic", program_profile: str = "standard") -> Optional[str]:
    logger.info(f"⚡️ Creating Context Cache ({config.MODEL_TRANSLATOR}) for {stem} in {target_language} (Profile: {program_profile})...")
    
    # Map full language name to code if needed, but profiles expects code (is, es)
    # The manager should pass the code (e.g. "is", "es")
    # If target_language is "Icelandic", map to "is"
    lang_map = {
        "icelandic": "is",
        "english": "en",
        "spanish": "es",
        "french": "fr",
        "german": "de"
    }
    lang_code = lang_map.get(target_language.lower(), target_language.lower())
    
    system_instruction = profiles.get_system_instruction(lang_code, program_profile)
    
    mime_type = "audio/wav" if gcs_uri.endswith(".wav") else "audio/mpeg"
    
    try:
        # Create Cache
        cached_content = caching.CachedContent.create(
            model_name=config.MODEL_TRANSLATOR,
            system_instruction=system_instruction,
            contents=[
                Content(role="user", parts=[
                    Part.from_uri(mime_type=mime_type, uri=gcs_uri)
                ])
            ],
            ttl=datetime.timedelta(minutes=60)
        )
        logger.info(f"✅ Cache Active! ID: {cached_content.name}")
        return cached_content.name
    except Exception as e:
        logger.error(f"Cache Creation Failed: {e}")
        return None

def translate_batch_with_cache(model, batch, target_language: str, program_profile: str = "standard"):
    """
    Translates a batch of segments using the cached context.
    """
    prompt = f"""
    TRANSLATE these segments to {target_language} (Profile: {program_profile}).
    Return ONLY JSON.
    
    INPUT:
    {json.dumps(batch, ensure_ascii=False)}
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
            if len(parsed) != len(batch): raise ValueError("Count mismatch")
            logger.info(f"   ✓ Batch Translated")
            return parsed
        except Exception as e:
            logger.warning(f"   ⚠️ Batch Retry {attempt}/3: {e}")
            time.sleep(2 ** attempt)
    raise Exception(f"Batch {batch_num} Failed")

def translate(transcription_path: Path, target_language_code: str = "is", program_profile: str = "standard"):
    """
    Translates the transcription using Gemini 1.5 Flash (Cached).
    """
    if not ensure_credentials():
        raise Exception("Google Credentials not found")

    stem = transcription_path.stem.replace("_SKELETON_DONE", "").replace("_SKELETON", "")
    
    # Map code to name for logging/cache creation if needed, 
    # but we primarily use code now.
    lang_map = {
        "is": "Icelandic",
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German"
    }
    target_language = lang_map.get(target_language_code, "Icelandic")
    
    audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
    if not audio_path.exists():
        # Fallback to old location
        audio_path = config.VAULT_DATA / f"{stem}.mp3"
        
    if not audio_path.exists():
        raise Exception(f"Audio file not found for {stem}")

    # Init & Cache
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    gcs_uri = upload_to_gcs(audio_path, f"audio_cache/{stem}{audio_path.suffix}")
    if not gcs_uri: raise Exception("GCS Upload failed")
    
    cache_name = create_context_cache(gcs_uri, stem, target_language)
    if not cache_name: raise Exception("Cache creation failed")

    # Load Data
    with open(transcription_path, 'r') as f: wrapper = json.load(f)
    full_data = wrapper.get("segments", wrapper) if isinstance(wrapper, dict) else wrapper
    meta = wrapper.get("meta", {}) if isinstance(wrapper, dict) else {}
    mode = wrapper.get("mode", "REVIEW") # Get mode from skeleton wrapper

    # Instantiate Model from Cache
    model = GenerativeModel.from_cached_content(cached_content=caching.CachedContent(cached_content_name=cache_name))

    # Run Translation
    translated_segments = []
    results_map = {}
    
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            current_idx = 0
            batch_num = 1
            while current_idx < len(full_data):
                target_end = min(current_idx + BATCH_SIZE, len(full_data))
                batch = full_data[current_idx:target_end]
                # pre/post removed as they were unused in prompt
                future = executor.submit(translate_batch_with_cache, model, batch, target_language, program_profile)
                futures[future] = batch_num
                current_idx = target_end
                batch_num += 1
            
            for future in as_completed(futures):
                system_health.update_heartbeat("omega_manager")
                idx = futures[future]
                results_map[idx] = future.result()
                # omega_db update handled by manager? Or here?
                # Ideally manager updates progress. But we are inside a long running task.
                # We can update DB here.
                omega_db.update(stem, progress=int((len(results_map)/batch_num)*100))

            # Reassemble results
            for i in sorted(results_map.keys()):
                translated_segments.extend(results_map[i])

        # Save Output
        # Save Output
        # ALWAYS send to Editor (Chief Editor Protocol)
        # We no longer bypass for AUTO mode.
        
        payload = {
             "source_data": full_data,
             "translated_data": translated_segments
        }
        output_path = config.EDITOR_DIR / f"{stem}_{target_language_code.upper()}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"👤 Sent to Chief Editor: {output_path.name}")
        return output_path

    finally:
        # Cleanup
        logger.info("🧹 Cleanup Crew: Removing cloud resources...")
        try:
            if cache_name: caching.CachedContent(name=cache_name).delete()
        except: pass
        try:
            if gcs_uri:
                blob_name = f"audio_cache/{stem}{audio_path.suffix}"
                storage.Client().bucket(BUCKET_NAME).blob(blob_name).delete()
        except: pass
