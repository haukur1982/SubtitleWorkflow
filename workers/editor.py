import os
import json
import shutil
import logging
from pathlib import Path
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
import config
import omega_db

logger = logging.getLogger("OmegaManager.Editor")

def ensure_credentials() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"): return True
    default_path = config.BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True
    return False

def review(translation_path: Path):
    """
    Reviews the translation using Gemini 3 Pro.
    Returns path to _APPROVED.json.
    """
    if not ensure_credentials():
        raise Exception("Google Credentials not found")

    # Infer stem and language from filename
    # Expected format: {stem}_{LANG}.json
    parts = translation_path.stem.split("_")
    if len(parts) >= 2:
        lang_suffix = parts[-1]
        stem = "_".join(parts[:-1])
    else:
        # Fallback
        stem = translation_path.stem.replace("_ICELANDIC", "")
        lang_suffix = "ICELANDIC"
        
    logger.info(f"🕵️‍♂️ Starting Review: {stem} ({lang_suffix})")
    
    with open(translation_path, 'r') as f: data = json.load(f)
    
    if "source_data" not in data or "translated_data" not in data:
        raise ValueError("Invalid file format: Missing source/translated data")
        
    source = data["source_data"]
    translation = data["translated_data"]
    
    # Re-init for Global (Required for Gemini 3 Preview)
    vertexai.init(project="sermon-translator-system", location=config.GEMINI_LOCATION)
    
    # Use model from config
    model_name = config.MODEL_EDITOR
    
    logger.info(f"💎 Connecting to {model_name}...")
    model = GenerativeModel(model_name)
    
    if lang_suffix.upper() in ["ICELANDIC", "IS"]:
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
        Return a JSON object with 'corrections' and a 'report'.
        
        Format: 
        {{ 
            "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation" }} ],
            "report": {{
                "rating": 8.5,  // 1-10 Score (Float)
                "quality_tier": "Broadcast Ready", // "Broadcast Ready", "Needs Minor Polish", "Needs Review", "Draft"
                "summary": "Brief analysis of the translation quality.",
                "major_issues": ["Anglicisms", "Theological Errors"], // List of strings
                "suggestions": "Actionable advice for the translator."
            }}
        }}
        """
    else:
        # Generic / Spanish Editor
        prompt = f"""
        ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.
        
        YOUR TASK: 
        Review the {lang_suffix} translation against the English source.
        Ensure flow, grammar, and theological accuracy.
        
        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}
        
        --- DRAFT ({lang_suffix}) ---
        {json.dumps(translation, ensure_ascii=False)}
        
        OUTPUT: 
        Return a JSON object with 'corrections' and a 'report'.
        
        Format: 
        {{ 
            "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation of error" }} ],
            "report": {{
                "rating": 8.0,
                "quality_tier": "Broadcast Ready",
                "summary": "Brief analysis.",
                "major_issues": [],
                "suggestions": ""
            }}
        }}
        """

    try:
        response = model.generate_content(
            prompt, 
            generation_config=GenerationConfig(response_mime_type="application/json", temperature=0.1)
        )
        
        # Parse Response
        try:
            text = response.text.replace("```json", "").replace("```", "").strip()
            result = json.loads(text)
            corrections = result.get("corrections", [])
            report = result.get("report", {})
            
            # Save Report to DB
            if report:
                omega_db.update(stem, editor_report=json.dumps(report))
                logger.info(f"📝 Editor Report: {report.get('rating')}/10 - {report.get('quality_tier')}")
                
        except Exception as e:
            logger.error(f"Failed to parse editor response: {e}")
            logger.error(f"Raw response: {response.text}")
            corrections = []

        final_segments = translation # Start with draft
        
        if corrections:
            logger.info(f"🛠️ Applied {len(corrections)} fixes.")
            # Apply corrections to the draft
            # Corrections format: {id, fix, reason}
            correction_map = {c['id']: c['fix'] for c in corrections}
            
            for seg in final_segments:
                seg_id = seg.get('id')
                if seg_id in correction_map:
                    seg['text'] = correction_map[seg_id]
        else:
            logger.info("✅ Perfect translation. No fixes needed.")

        # CRITICAL FIX: Merge Timestamps AND Source Text from Source
        # Translator output often lacks start/end, so we must re-attach them from source.
        source_map = {s['id']: {'start': s['start'], 'end': s['end'], 'text': s['text']} for s in source}
        
        for seg in final_segments:
            seg_id = seg.get('id')
            if seg_id in source_map:
                seg['start'] = source_map[seg_id]['start']
                seg['end'] = source_map[seg_id]['end']
                seg['source_text'] = source_map[seg_id]['text']
            else:
                logger.warning(f"⚠️ Segment {seg_id} has no matching source timestamp!")

        # Save Approved Version
        output_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
        
        # Wrap in "segments" key for consistency with Finalizer expectations
        final_payload = {
            "segments": final_segments,
            "meta": {
                "editor_model": model_name,
                "rating": report.get("rating"),
                "quality_tier": report.get("quality_tier")
            }
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_payload, f, indent=2, ensure_ascii=False)
            
        logger.info(f"✅ Editor Approved: {output_path.name}")
        
        # Cleanup input
        translation_path.unlink()
        
        return output_path

    except Exception as e:
        logger.error(f"Editor Failed: {e}")
        raise e
