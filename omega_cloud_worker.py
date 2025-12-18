import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

from google.cloud import storage
import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    GenerationConfig,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
)

import config
import profiles
from gcp_auth import ensure_google_application_credentials
from gcs_jobs import (
    GcsJobPaths,
    backoff_sleep,
    blob_exists,
    download_json,
    upload_json,
    utc_iso_now,
)

logger = logging.getLogger("OmegaCloudWorker")


SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]


def _clean_model_json(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def _iter_input_ids(segments: list[dict]) -> list[int]:
    ids: list[int] = []
    for seg in segments:
        seg_id = seg.get("id")
        if seg_id is None:
            raise ValueError("Segment missing 'id'")
        ids.append(int(seg_id))
    return ids


def _translate_chunk_once(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
) -> list[dict]:
    input_payload = [{"id": int(seg["id"]), "text": str(seg.get("text") or "").strip()} for seg in chunk]

    continuity_payload = [
        {
            "id": int(seg["id"]),
            "source": str(seg.get("source") or "").strip(),
            "translated": str(seg.get("translated") or "").strip(),
        }
        for seg in (continuity or [])
        if seg.get("translated")
    ]

    system_instruction = profiles.get_system_instruction(target_language_code, program_profile)

    prompt = f"""
ROLE: You are the Lead Translator for Omega TV.

SYSTEM INSTRUCTION (obey strictly):
{system_instruction}

TASK:
- Translate the INPUT segments to {target_language_name}.
- Return ONLY JSON (no markdown fences).
- Output MUST be a JSON array of objects: {{ "id": <int>, "text": <string> }}.
- Output MUST contain EXACTLY the IDs from INPUT (no extras, none missing).

BROADCAST CAPS:
- Do NOT output ALL CAPS sentences.
- If the source text is ALL CAPS, convert to natural sentence case.
- Preserve acronyms/initialisms (USA, TV, I-690) and mandatory titles (ÉG ER / YO SOY).

CONTEXT (for consistency only; DO NOT translate these IDs again):
{json.dumps(continuity_payload, ensure_ascii=False)}

INPUT:
{json.dumps(input_payload, ensure_ascii=False)}
"""

    generation_config = GenerationConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
            },
        },
        temperature=0.25,
    )

    response = model.generate_content(
        prompt,
        generation_config=generation_config,
        safety_settings=SAFETY_SETTINGS,
    )

    cleaned = _clean_model_json(getattr(response, "text", "") or "")
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Model response is not a JSON array")

    expected_ids = set(_iter_input_ids(input_payload))
    result_map: Dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        text = item.get("text")
        try:
            seg_id_int = int(seg_id)
        except Exception:
            continue
        if seg_id_int not in expected_ids:
            continue
        if not isinstance(text, str):
            continue
        result_map[seg_id_int] = text

    missing = [seg_id for seg_id in expected_ids if seg_id not in result_map]
    if missing:
        raise ValueError(f"Missing IDs in model response: {sorted(missing)[:8]}")

    ordered_ids = _iter_input_ids(input_payload)
    return [{"id": seg_id, "text": result_map[seg_id]} for seg_id in ordered_ids]


def _translate_chunk(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
    max_attempts: int,
    split_after_attempts: int,
    _depth: int = 0,
) -> list[dict]:
    if not chunk:
        return []

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _translate_chunk_once(
                model,
                chunk=chunk,
                target_language_name=target_language_name,
                target_language_code=target_language_code,
                program_profile=program_profile,
                continuity=continuity,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if len(chunk) > 1 and attempt >= split_after_attempts:
                break
            logger.warning("   ⚠️ Translate retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)
        except Exception as exc:
            last_exc = exc
            logger.warning("   ⚠️ Translate retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)

    if len(chunk) <= 1:
        raise last_exc or RuntimeError("Chunk translation failed")

    mid = max(1, len(chunk) // 2)
    logger.warning(
        "   🔪 Splitting chunk (%s segments) at depth %s after failures: %s",
        len(chunk),
        _depth,
        last_exc,
    )
    left = _translate_chunk(
        model,
        chunk=chunk[:mid],
        target_language_name=target_language_name,
        target_language_code=target_language_code,
        program_profile=program_profile,
        continuity=continuity,
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    tail_context = _build_continuity_window(continuity, left, max_items=len(continuity) or 8)
    right = _translate_chunk(
        model,
        chunk=chunk[mid:],
        target_language_name=target_language_name,
        target_language_code=target_language_code,
        program_profile=program_profile,
        continuity=tail_context,
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    return left + right


def _build_continuity_window(
    prior: list[dict],
    translated: list[dict],
    *,
    max_items: int,
) -> list[dict]:
    merged: list[dict] = []
    for item in prior or []:
        if item.get("translated"):
            merged.append(item)
    # Add newest translations (source text is optional here; we keep translated for consistency).
    for item in translated or []:
        merged.append({"id": item["id"], "source": "", "translated": item["text"]})
    return merged[-max_items:] if max_items and len(merged) > max_items else merged


def _lang_name(code: str) -> str:
    mapping = {
        "is": "Icelandic",
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
    }
    return mapping.get((code or "").lower().strip(), (code or "Icelandic").strip() or "Icelandic")


def _write_progress(
    storage_client: storage.Client,
    *,
    paths: GcsJobPaths,
    stage: str,
    status: str,
    progress: float,
    meta: Optional[dict] = None,
) -> None:
    payload = {
        "stage": stage,
        "status": status,
        "progress": float(progress),
        "updated_at": utc_iso_now(),
        "meta": meta or {},
    }
    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.progress_json(), payload=payload)


def run_job(*, bucket: str, prefix: str, job_id: str) -> None:
    # Prefer Workload Identity / attached service accounts in Cloud Run; for local runs
    # we fall back to ./service_account.json.
    ensure_google_application_credentials()

    storage_client = storage.Client()
    paths = GcsJobPaths(bucket=bucket, prefix=prefix, job_id=job_id)

    job = download_json(storage_client, bucket=paths.bucket, blob_name=paths.job_json())
    target_language_code = str(job.get("target_language_code") or "is").strip().lower() or "is"
    program_profile = str(job.get("program_profile") or "standard").strip() or "standard"

    translator_model_name = str(job.get("translator_model") or config.MODEL_TRANSLATOR).strip()
    editor_model_name = str(job.get("editor_model") or config.MODEL_EDITOR).strip()

    max_attempts = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_MAX_ATTEMPTS", "6") or 6)
    split_after_attempts = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_SPLIT_AFTER", "2") or 2)
    chunk_size = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_CHUNK_SIZE", "90") or 90)
    chunk_size = max(10, min(chunk_size, 220))
    continuity_size = int(os.environ.get("OMEGA_CLOUD_CONTINUITY_SIZE", "8") or 8)
    continuity_size = max(0, min(continuity_size, 30))

    _write_progress(
        storage_client,
        paths=paths,
        stage="CLOUD_TRANSLATING",
        status="Loading skeleton",
        progress=40.0,
        meta={
            "translator_model": translator_model_name,
            "editor_model": editor_model_name,
            "target_language_code": target_language_code,
            "program_profile": program_profile,
        },
    )

    skeleton = download_json(storage_client, bucket=paths.bucket, blob_name=paths.skeleton_json())
    segments = skeleton.get("segments", skeleton) if isinstance(skeleton, dict) else skeleton
    if not isinstance(segments, list):
        raise ValueError("skeleton.json is not a list (or {segments: [...]})")

    target_language_name = _lang_name(target_language_code)

    vertexai.init(project=job.get("project_id") or "sermon-translator-system", location=config.GEMINI_LOCATION)
    translator_model = GenerativeModel(translator_model_name)

    checkpoint = None
    if blob_exists(storage_client, paths.bucket, paths.translation_checkpoint_json()):
        checkpoint = download_json(storage_client, bucket=paths.bucket, blob_name=paths.translation_checkpoint_json())
    translated_map: Dict[str, str] = {}
    if isinstance(checkpoint, dict):
        raw = checkpoint.get("translated") or {}
        if isinstance(raw, dict):
            translated_map = {str(k): str(v) for k, v in raw.items() if v is not None}

    input_ids = [int(seg.get("id")) for seg in segments]
    total = len(input_ids)

    def _progress(done: int) -> float:
        if total <= 0:
            return 40.0
        return 40.0 + (max(0.0, min(1.0, done / total)) * 15.0)

    # Determine what still needs translation.
    to_translate: list[dict] = []
    for seg in segments:
        seg_id = str(seg.get("id"))
        existing = translated_map.get(seg_id)
        if not existing or not existing.strip():
            to_translate.append(seg)

    if not to_translate and translated_map:
        logger.info("✅ Translation already complete; re-emitting draft from checkpoint.")
    else:
        _write_progress(
            storage_client,
            paths=paths,
            stage="CLOUD_TRANSLATING",
            status=f"Translating ({target_language_code})",
            progress=_progress(total - len(to_translate)),
            meta={"remaining": len(to_translate), "total": total},
        )

        continuity: list[dict] = []
        if continuity_size > 0:
            continuity = []

        for offset in range(0, len(to_translate), chunk_size):
            chunk = to_translate[offset : offset + chunk_size]
            translated_chunk = _translate_chunk(
                translator_model,
                chunk=chunk,
                target_language_name=target_language_name,
                target_language_code=target_language_code,
                program_profile=program_profile,
                continuity=continuity,
                max_attempts=max_attempts,
                split_after_attempts=split_after_attempts,
            )
            for item in translated_chunk:
                translated_map[str(item["id"])] = item["text"]

            if continuity_size > 0:
                continuity = _build_continuity_window(continuity, translated_chunk, max_items=continuity_size)

            done_count = sum(1 for seg_id in input_ids if str(seg_id) in translated_map)
            checkpoint_payload = {
                "version": 1,
                "job_id": job_id,
                "target_language_code": target_language_code,
                "program_profile": program_profile,
                "translated": translated_map,
                "translated_count": int(done_count),
                "total_count": int(total),
                "updated_at": utc_iso_now(),
            }
            upload_json(
                storage_client,
                bucket=paths.bucket,
                blob_name=paths.translation_checkpoint_json(),
                payload=checkpoint_payload,
            )
            _write_progress(
                storage_client,
                paths=paths,
                stage="CLOUD_TRANSLATING",
                status=f"Translating ({done_count}/{total})",
                progress=_progress(done_count),
            )

    missing = [seg_id for seg_id in input_ids if str(seg_id) not in translated_map]
    if missing:
        raise RuntimeError(f"Translation incomplete; missing {len(missing)} segments")

    translated_segments = [{"id": seg_id, "text": translated_map[str(seg_id)]} for seg_id in input_ids]
    draft_payload = {"source_data": segments, "translated_data": translated_segments}
    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.translation_draft_json(), payload=draft_payload)

    _write_progress(
        storage_client,
        paths=paths,
        stage="CLOUD_REVIEWING",
        status="Chief Editor reviewing",
        progress=60.0,
    )

    editor_model = GenerativeModel(editor_model_name)
    editor_prompt = _build_editor_prompt(
        source_segments=segments,
        translated_segments=translated_segments,
        lang_suffix=target_language_code.upper(),
    )
    editor_response = editor_model.generate_content(
        editor_prompt,
        generation_config=GenerationConfig(response_mime_type="application/json", temperature=0.1),
    )

    corrections, report = _parse_editor_response(getattr(editor_response, "text", "") or "")
    approved_segments = _apply_editor_corrections(
        source_segments=segments,
        translated_segments=translated_segments,
        corrections=corrections,
    )
    approved_payload = {
        "segments": approved_segments,
        "meta": {
            "editor_model": editor_model_name,
            "rating": report.get("rating") if isinstance(report, dict) else None,
            "quality_tier": report.get("quality_tier") if isinstance(report, dict) else None,
            "generated_at": utc_iso_now(),
        },
    }

    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.approved_json(), payload=approved_payload)
    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.editor_report_json(), payload=report or {})

    _write_progress(
        storage_client,
        paths=paths,
        stage="CLOUD_DONE",
        status="Approved",
        progress=70.0,
    )


def _build_editor_prompt(*, source_segments: list[dict], translated_segments: list[dict], lang_suffix: str) -> str:
    if lang_suffix.upper() in {"ICELANDIC", "IS"}:
        return f"""
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
4. CAPITALIZATION (Broadcast):
   - Treat ALL CAPS as a robot mistake; convert to normal sentence case.
   - Preserve acronyms/initialisms (USA, TV, I-690) and the mandatory title "ÉG ER".

INPUT DATA:
--- SOURCE (English) ---
{json.dumps(source_segments, ensure_ascii=False)}

--- DRAFT (Icelandic) ---
{json.dumps(translated_segments, ensure_ascii=False)}

OUTPUT:
Return a JSON object with 'corrections' and a 'report'.

Format:
{{
  "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation" }} ],
  "report": {{
    "rating": 8.5,
    "quality_tier": "Broadcast Ready",
    "summary": "Brief analysis of the translation quality.",
    "major_issues": ["Anglicisms", "Theological Errors"],
    "suggestions": "Actionable advice for the translator."
  }}
}}
"""

    return f"""
ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.

YOUR TASK:
Review the {lang_suffix} translation against the English source.
Ensure flow, grammar, and theological accuracy.

STRICT RULES:
- CAPITALIZATION (Broadcast): Do NOT allow ALL CAPS sentences; convert to natural sentence case while preserving acronyms/initialisms and mandatory titles (e.g., ÉG ER / YO SOY).

INPUT DATA:
--- SOURCE (English) ---
{json.dumps(source_segments, ensure_ascii=False)}

--- DRAFT ({lang_suffix}) ---
{json.dumps(translated_segments, ensure_ascii=False)}

OUTPUT:
Return a JSON object with 'corrections' and a 'report'.
"""


def _parse_editor_response(text: str) -> tuple[list[dict], dict]:
    cleaned = _clean_model_json(text)
    try:
        result = json.loads(cleaned)
    except Exception as exc:
        raise ValueError(f"Failed to parse editor JSON: {exc}") from exc

    if not isinstance(result, dict):
        raise ValueError("Editor response is not a JSON object")

    corrections = result.get("corrections") or []
    report = result.get("report") or {}
    if not isinstance(corrections, list):
        corrections = []
    if not isinstance(report, dict):
        report = {}
    return corrections, report


def _apply_editor_corrections(
    *,
    source_segments: list[dict],
    translated_segments: list[dict],
    corrections: list[dict],
) -> list[dict]:
    correction_map: Dict[int, str] = {}
    for item in corrections or []:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        fix = item.get("fix")
        try:
            seg_id_int = int(seg_id)
        except Exception:
            continue
        if isinstance(fix, str) and fix.strip():
            correction_map[seg_id_int] = fix.strip()

    source_map: Dict[int, dict] = {}
    for seg in source_segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        source_map[seg_id] = {
            "start": seg.get("start"),
            "end": seg.get("end"),
            "source_text": seg.get("text"),
        }

    final_segments: list[dict] = []
    for seg in translated_segments:
        seg_id = int(seg.get("id"))
        text = str(seg.get("text") or "").strip()
        if seg_id in correction_map:
            text = correction_map[seg_id]

        merged = {"id": seg_id, "text": text}
        if seg_id in source_map:
            merged.update(source_map[seg_id])
        final_segments.append(merged)

    return final_segments


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Omega cloud-first worker (GCS artifacts + Vertex).")
    parser.add_argument("--bucket", default=config.OMEGA_JOBS_BUCKET, help="GCS bucket for job artifacts")
    parser.add_argument("--prefix", default=config.OMEGA_JOBS_PREFIX, help="GCS prefix (folder) for job artifacts")
    parser.add_argument("--job-id", required=True, help="Job id (GCS folder name)")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    logger.info("☁️ Omega Cloud Worker starting: job_id=%s bucket=%s", args.job_id, args.bucket)
    start = time.time()
    try:
        run_job(bucket=args.bucket, prefix=args.prefix, job_id=args.job_id)
    except Exception as exc:
        logger.error("❌ Cloud worker failed: %s", exc)
        try:
            storage_client = storage.Client()
            paths = GcsJobPaths(bucket=args.bucket, prefix=args.prefix, job_id=args.job_id)
            _write_progress(
                storage_client,
                paths=paths,
                stage="CLOUD_ERROR",
                status=f"Error: {exc}",
                progress=0.0,
            )
        except Exception:
            pass
        return 1
    finally:
        elapsed = time.time() - start
        logger.info("🏁 Done in %.1fs", elapsed)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
