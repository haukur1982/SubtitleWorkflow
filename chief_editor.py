import os
import json
import time
import shutil
import logging
from pathlib import Path
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig, SafetySetting, HarmCategory, HarmBlockThreshold
from lock_manager import ProcessLock
import system_health
import omega_db

# --- CONFIG ---
PROJECT_ID = "sermon-translator-system"
LOCATION = "us-central1"

# --- PATHS ---
BASE_DIR = Path(os.getcwd())
STAGING_DIR = BASE_DIR / "3_EDITOR"
OUTBOX = BASE_DIR / "3_TRANSLATED_DONE"
ERROR_DIR = BASE_DIR / "99_ERRORS"

OUTBOX.mkdir(parents=True, exist_ok=True)

# --- CREDENTIALS ---
def ensure_credentials() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"): return True
    default_path = BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True
    return False

GLOSSARY_TEXT = """
"I AM" -> "ÉG ER", "Lord" -> "Drottinn", "Grace" -> "Náð", 
"Savior" -> "Frelsari", "Holy Spirit" -> "Heilagur Andi".
"""

def run_editor_pass(json_file: Path):
    print(f"\n🕵️‍♂️ EDITOR: Picking up {json_file.name}")
    stem = json_file.stem.replace("_ICELANDIC", "")
    
    job = omega_db.get_job(stem)
    if job and job.get("stage") in ["COMPLETED", "REVIEW"]:
        print("   ⚠️ Job already reviewed. Skipping.")
        return

    try:
        with open(json_file, 'r') as f: data = json.load(f)
        
        # Handle payload structure
        if "source_data" in data and "translated_data" in data:
            source = data["source_data"]
            translation = data["translated_data"]
        else:
            print("   ❌ Invalid file format.")
            shutil.move(str(json_file), str(ERROR_DIR / json_file.name))
            return

        # Ensure credentials are loaded
        if not ensure_credentials():
            print("   ❌ Google Cloud credentials not found")
            return

        # Re-init for Global (Required for Gemini 3 Preview)
        vertexai.init(project=PROJECT_ID, location="global")
        
        # --- GEMINI 3 PRO PREVIEW (The Brain) ---
        print("   💎 Connecting to Gemini 3 Pro Preview (Global)...")
        model = GenerativeModel("gemini-3-pro-preview")
        
        # IMPROVED AUDITOR PROMPT
        prompt = f"""
        ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.
        
        YOUR TASK: 
        Review the Icelandic translation against the English source. 
        You are looking for "Robot Mistakes," theological errors, and awkward phrasing.
        
        STRICT RULES:
        1. "Þú" vs "Þér": Ensure God is addressed as "Þér" and humans as "Þú".
        2. NO ANGLICISMS: 
           - Reject "fyrir þig" (used for "died for you"). Use "vegna þín".
           - Reject "á eldi" (on fire). Use "brennandi".
           - Reject "Bless" if used for impartation. Use "Guð blessi þig".
        3. TERMINOLOGY:
           - "Partners" -> "Bakhjarlar".
           - "I AM" -> "ÉG ER".
           - "Covenant" -> "Sáttmáli".
           
        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}
        
        --- DRAFT (Icelandic) ---
        {json.dumps(translation, ensure_ascii=False)}
        
        OUTPUT: 
        Return a JSON object with a list of 'corrections'. 
        Only provide corrections if there is an error or significant stylistic improvement needed.
        If the translation is perfect, return an empty list.
        
        Format: {{ "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation of error" }} ] }}
        """

        response = model.generate_content(
            prompt, 
            generation_config=GenerationConfig(response_mime_type="application/json", temperature=0.1)
        )
        
        result = json.loads(response.text)
        corrections = result.get("corrections", [])
        
        final_segments = translation # Start with draft
        
        if corrections:
            print(f"   🛠️ Applied {len(corrections)} fixes from Gemini 3.")
            trans_map = {item['id']: item for item in translation}
            for fix in corrections:
                if fix['id'] in trans_map:
                    trans_map[fix['id']]['text'] = fix['fix']
            final_segments = list(trans_map.values())
        else:
            print("   ✅ Perfect translation. No fixes needed.")

        # --- MERGE TIMING FROM SOURCE ---
        # Gemini output often lacks timing, so we re-attach it from source
        source_map = {item['id']: item for item in source}
        merged_output = []
        for seg in final_segments:
            seg_id = seg['id']
            if seg_id in source_map:
                original = source_map[seg_id]
                merged_seg = {
                    "id": seg_id,
                    "start": original['start'],
                    "end": original['end'],
                    "text": seg['text']
                }
                merged_output.append(merged_seg)
            else:
                merged_output.append(seg)
        final_segments = merged_output

        # SAVE FINAL
        final_output = OUTBOX / f"{stem}_APPROVED.json"
        with open(final_output, "w", encoding="utf-8") as f:
            json.dump(final_segments, f, indent=2, ensure_ascii=False)

        print(f"   🏁 Job Completed: {final_output.name}")
        omega_db.update(stem, stage="REVIEW", status="Editor Approved", progress=100.0)
        
        # Remove from staging (Done)
        os.remove(json_file)

    except Exception as e:
        print(f"   💥 Editor Failed: {e}")
        # We do NOT delete the file. We leave it in staging to retry later.
        omega_db.update(stem, status=f"Editor Error: {str(e)[:50]}")

if __name__ == "__main__":
    lock = ProcessLock("chief_editor")
    with lock:
        print("✅ Chief Editor Active (Gemini 3 Watcher)")
        while True:
            system_health.update_heartbeat("chief_editor")
            if ensure_credentials():
                for f in STAGING_DIR.glob("*_ICELANDIC.json"):
                    run_editor_pass(f)
            time.sleep(5)
