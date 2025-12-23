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

MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_CHARS_TOTAL = MAX_CHARS_PER_LINE * MAX_LINES
IDEAL_CPS = 17.0
TIGHT_CPS = 20.0
MIN_DURATION = 1.0
GAP_SECONDS = 0.1
CONTEXT_GAP_MAX = 3.0
MAX_PRIORITY_SEGMENTS = 120


def _status_for_cps(cps: float) -> str:
    if cps <= IDEAL_CPS:
        return "OPTIMAL"
    if cps <= TIGHT_CPS:
        return "TIGHT"
    return "CRITICAL"


def _build_constraint_items(source_segments: list[dict], translated_segments: list[dict]) -> list[dict]:
    trans_map: dict[int, str] = {}
    for seg in translated_segments or []:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        text = str(seg.get("text") or "").strip()
        if text:
            trans_map[seg_id] = text

    items: list[dict] = []
    for idx, seg in enumerate(source_segments or []):
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        duration = max(0.0, end - start)
        next_start = None
        if idx + 1 < len(source_segments):
            try:
                next_start = float(source_segments[idx + 1].get("start") or 0.0)
            except Exception:
                next_start = None
        gap_to_next = None
        max_available = None
        if next_start is not None:
            gap_to_next = next_start - end
            max_available = max(0.0, (next_start - GAP_SECONDS) - start)

        effective_duration = max(duration, MIN_DURATION)
        if max_available is not None and max_available > 0:
            effective_duration = min(effective_duration, max_available)

        text = trans_map.get(seg_id) or ""
        char_count = len(text)
        cps = (char_count / effective_duration) if effective_duration > 0 else 0.0
        status = _status_for_cps(cps) if text else "OPTIMAL"

        items.append(
            {
                "id": seg_id,
                "duration": round(duration, 3),
                "effective_duration": round(effective_duration, 3),
                "gap_to_next": round(gap_to_next, 3) if gap_to_next is not None else None,
                "max_chars_total": MAX_CHARS_TOTAL,
                "max_chars_per_line": MAX_CHARS_PER_LINE,
                "target_cps": IDEAL_CPS,
                "current_cps": round(cps, 2),
                "status": status,
            }
        )
    return items


def _build_priority_context(
    source_segments: list[dict],
    translated_segments: list[dict],
    *,
    include_tight: bool = True,
) -> list[dict]:
    items = _build_constraint_items(source_segments, translated_segments)
    trans_map: dict[int, str] = {}
    for seg in translated_segments or []:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        trans_map[seg_id] = str(seg.get("text") or "").strip()

    priority = []
    for idx, item in enumerate(items):
        status = item.get("status")
        if status == "CRITICAL" or (include_tight and status == "TIGHT"):
            priority.append((idx, item))

    critical = [entry for entry in priority if entry[1].get("status") == "CRITICAL"]
    tight = [entry for entry in priority if entry[1].get("status") == "TIGHT"]
    critical.sort(key=lambda entry: entry[1].get("current_cps", 0.0), reverse=True)
    tight.sort(key=lambda entry: entry[1].get("current_cps", 0.0), reverse=True)

    selected = []
    for entry in critical:
        if len(selected) >= MAX_PRIORITY_SEGMENTS:
            break
        selected.append(entry)
    if len(selected) < MAX_PRIORITY_SEGMENTS:
        for entry in tight:
            if len(selected) >= MAX_PRIORITY_SEGMENTS:
                break
            selected.append(entry)

    result = []
    for idx, item in selected:
        seg = source_segments[idx]
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        prev_ctx = None
        if idx > 0:
            prev_seg = source_segments[idx - 1]
            prev_end = float(prev_seg.get("end") or 0.0)
            if start - prev_end <= CONTEXT_GAP_MAX:
                prev_id = int(prev_seg.get("id"))
                prev_ctx = {
                    "id": prev_id,
                    "src": str(prev_seg.get("text") or "").strip(),
                    "draft": trans_map.get(prev_id, ""),
                }
        next_ctx = None
        if idx + 1 < len(source_segments):
            next_seg = source_segments[idx + 1]
            next_start = float(next_seg.get("start") or 0.0)
            if next_start - end <= CONTEXT_GAP_MAX:
                next_id = int(next_seg.get("id"))
                next_ctx = {
                    "id": next_id,
                    "src": str(next_seg.get("text") or "").strip(),
                    "draft": trans_map.get(next_id, ""),
                }

        active = {
            "id": item["id"],
            "src": str(seg.get("text") or "").strip(),
            "draft": trans_map.get(item["id"], ""),
            "effective_duration": item["effective_duration"],
            "gap_to_next": item["gap_to_next"],
            "target_cps": item["target_cps"],
            "max_chars_total": item["max_chars_total"],
            "max_chars_per_line": item["max_chars_per_line"],
            "current_cps": item["current_cps"],
            "status": item["status"],
        }

        result.append(
            {
                "context_prev": prev_ctx,
                "active": active,
                "context_next": next_ctx,
            }
        )
    return result

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
    
    priority_context = _build_priority_context(source, translation, include_tight=True)

    if lang_suffix.upper() in ["ICELANDIC", "IS"]:
        prompt = f"""
        ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.
        
        YOUR TASK: 
        Review the Icelandic translation against the English source. 
        You are looking for "Robot Mistakes," theological errors, and awkward phrasing.
        
        STRICT RULES:
        1. "Þú" vs "Þér": God is addressed as "Þú" (do NOT use "Þér").
        2. NO ANGLICISMS: 
           - Reject "fyrir þig" (used for "died for you"). Use "vegna þín".
           - Reject "á eldi" (on fire). Use "brennandi".
           - Reject "Bless" if used for impartation. Use "Guð blessi þig".
        3. TERMINOLOGY:
           - "Partners" -> "Bakhjarlar".
           - "I AM" -> "ÉG ER".
           - "Covenant" -> "Sáttmáli".
           - "Pastor" -> "Prestur".
        4. CAPITALIZATION (Broadcast):
           - Treat ALL CAPS as a robot mistake; convert to normal sentence case.
           - Preserve acronyms/initialisms (USA, TV, I-690) and the mandatory title "ÉG ER".
        5. ASR CLEANUP:
           - Fix obvious speech-to-text errors in the SOURCE when the intended word is clear.
           - If uncertain, leave the original wording.
        6. MUSIC VS SPEECH:
           - If the SOURCE contains spoken content (not a pure music marker), the translation must NOT be "(MUSIC)" or blank.
        7. TECHNICAL CONSTRAINTS (Broadcast):
           - Each segment has max 2 lines (<=42 chars each; <=84 total).
           - Use the provided `effective_duration`, `gap_to_next`, and `current_cps` to keep CPS <= 17.
           - If status is TIGHT, shorten only if it improves CPS without losing meaning.
           - If status is CRITICAL, you MUST shorten while preserving theology.
           - If shortening would change theology or remove Scripture, keep meaning and note it in `reason`.
        8. CONTEXT WINDOW:
           - `context_prev` / `context_next` are read-only.
           - Use them to maintain gender/case agreement in Icelandic.
           
        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}
        
        --- DRAFT (Icelandic) ---
        {json.dumps(translation, ensure_ascii=False)}

        --- PRIORITY SEGMENTS (Constraint-Aware Window) ---
        {json.dumps(priority_context, ensure_ascii=False)}
        
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

        STRICT RULES:
        - CAPITALIZATION (Broadcast): Do NOT allow ALL CAPS sentences; convert to natural sentence case while preserving acronyms/initialisms and mandatory titles (e.g., ÉG ER / YO SOY).
        - ASR CLEANUP: Fix obvious speech-to-text errors in the SOURCE when the intended word is clear. If uncertain, leave the original wording.
        - MUSIC VS SPEECH: If the SOURCE contains spoken content (not a pure music marker), the translation must NOT be "(MUSIC)" or blank.
        - TECHNICAL CONSTRAINTS: Use provided `effective_duration`, `gap_to_next`, and `current_cps` in the priority list to keep CPS <= 17; if CRITICAL, shorten without losing meaning.
        - CONTEXT WINDOW: `context_prev` / `context_next` are read-only; use them for grammatical agreement.
        
        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}
        
        --- DRAFT ({lang_suffix}) ---
        {json.dumps(translation, ensure_ascii=False)}

        --- PRIORITY SEGMENTS (Constraint-Aware Window) ---
        {json.dumps(priority_context, ensure_ascii=False)}
        
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
