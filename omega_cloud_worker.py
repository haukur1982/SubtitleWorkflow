import argparse
import json
import logging
import os
import re
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

MUSIC_MARKERS = (
    "(music)",
    "[music]",
    "(song)",
    "[song]",
    "(singing)",
    "[singing]",
    "(choir)",
    "[choir]",
)

MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_CHARS_TOTAL = MAX_CHARS_PER_LINE * MAX_LINES
IDEAL_CPS = 17.0
TIGHT_CPS = 20.0
MIN_DURATION = 1.0
GAP_SECONDS = 0.1
CONTEXT_GAP_MAX = 3.0
MAX_PRIORITY_SEGMENTS = 120

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿÞþÐð]+", re.UNICODE)


def _is_music_marker_text(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if "♪" in text:
        return True
    if any(marker in lowered for marker in MUSIC_MARKERS):
        return True
    cleaned = re.sub(r"[^a-z]", "", lowered)
    return cleaned in {"music", "song", "singing", "choir", "instrumental"}


def _looks_like_speech(text: str) -> bool:
    words = _WORD_RE.findall(text or "")
    if len(words) >= 3:
        return True
    if len(words) >= 2 and any(ch in (text or "") for ch in ".?!"):
        return True
    return False


def _status_for_cps(cps: float) -> str:
    if cps <= IDEAL_CPS:
        return "OPTIMAL"
    if cps <= TIGHT_CPS:
        return "TIGHT"
    return "CRITICAL"


def _build_constraint_items(
    source_segments: list[dict],
    translated_segments: list[dict],
) -> list[dict]:
    trans_map: Dict[int, str] = {}
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
    trans_map: Dict[int, str] = {}
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


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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


def _sample_segments_for_brief(segments: list[dict], max_segments: int) -> list[dict]:
    if max_segments <= 0 or not segments:
        return []
    if len(segments) <= max_segments:
        return segments
    if max_segments == 1:
        return [segments[0]]
    total = len(segments)
    step = (total - 1) / float(max_segments - 1)
    selected: list[int] = []
    seen: set[int] = set()
    for i in range(max_segments):
        idx = int(round(i * step))
        if idx in seen:
            continue
        selected.append(idx)
        seen.add(idx)
    return [segments[i] for i in selected]


def _build_document_brief(
    model: GenerativeModel,
    *,
    segments: list[dict],
    max_segments: int,
    max_chars: int,
) -> str:
    sampled = _sample_segments_for_brief(segments, max_segments)
    if not sampled:
        return ""

    lines: list[str] = []
    total_chars = 0
    for seg in sampled:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        seg_id = seg.get("id")
        line = f"{seg_id}: {text}" if seg_id is not None else text
        if max_chars and (total_chars + len(line) + 1) > max_chars:
            break
        lines.append(line)
        total_chars += len(line) + 1

    if not lines:
        return ""

    excerpt = "\n".join(lines)
    prompt = f"""
ROLE: You are a broadcast program summarizer for Omega TV.

TASK:
- Read the transcript excerpts.
- Produce a concise "document brief" to guide translation consistency.
- Output plain text with 3 sections on separate lines:
  Summary: <3-5 sentences>
  Keywords: <comma-separated list of key terms/names>
  Tone: <one sentence about tone/register>
- Keep under 900 characters total.
- Do not invent facts or add details not in the excerpts.
- Write the brief in English.

EXCERPTS:
{excerpt}
"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.2),
            safety_settings=SAFETY_SETTINGS,
        )
    except Exception as exc:
        logger.warning("   ⚠️ Document brief failed: %s", exc)
        return ""

    brief = _clean_model_json(getattr(response, "text", "") or "").strip()
    if len(brief) > 1200:
        brief = brief[:1200].rstrip()
    return brief


def _translate_chunk_once(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
    doc_brief: str,
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
    brief_block = ""
    if doc_brief:
        brief_block = f"""
DOCUMENT BRIEF (for consistency only; do not infer facts):
{doc_brief}
"""

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
{brief_block}

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
    doc_brief: str,
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
                doc_brief=doc_brief,
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
        doc_brief=doc_brief,
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
        doc_brief=doc_brief,
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    return left + right


def _music_heuristic_ids(segments: list[dict]) -> set[int]:
    ids: set[int] = set()
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if _is_music_marker_text(text):
            try:
                ids.add(int(seg.get("id")))
            except Exception:
                pass
    return ids


def _detect_music_chunk_once(model: GenerativeModel, *, chunk: list[dict]) -> list[int]:
    input_payload = [{"id": int(seg["id"]), "text": str(seg.get("text") or "").strip()} for seg in chunk]

    prompt = f"""
ROLE: You are a broadcast segment classifier for Omega TV.

TASK:
- Identify segments that are clearly music/lyrics/choir/worship singing (non-spoken content).
- Be conservative: ONLY return IDs when you are confident it is singing/lyrics.
- Do NOT mark segments where speakers merely talk about music.
- If speech is present over music (e.g., organ under speech), do NOT mark it as music.
- Return ONLY JSON (no markdown fences).
- Output MUST be a JSON array of integer IDs.

INPUT:
{json.dumps(input_payload, ensure_ascii=False)}
"""

    generation_config = GenerationConfig(
        response_mime_type="application/json",
        response_schema={"type": "array", "items": {"type": "integer"}},
        temperature=0.0,
    )

    response = model.generate_content(
        prompt,
        generation_config=generation_config,
        safety_settings=SAFETY_SETTINGS,
    )

    cleaned = _clean_model_json(getattr(response, "text", "") or "")
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Music detector response is not a JSON array")

    expected_ids = set(_iter_input_ids(input_payload))
    music_ids: list[int] = []
    for item in parsed:
        try:
            seg_id = int(item)
        except Exception:
            continue
        if seg_id in expected_ids:
            music_ids.append(seg_id)
    return music_ids


def _detect_music_chunk(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    max_attempts: int,
    split_after_attempts: int,
    _depth: int = 0,
) -> list[int]:
    if not chunk:
        return []

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _detect_music_chunk_once(model, chunk=chunk)
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if len(chunk) > 1 and attempt >= split_after_attempts:
                break
            logger.warning("   ⚠️ Music detect retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)
        except Exception as exc:
            last_exc = exc
            logger.warning("   ⚠️ Music detect retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)

    if len(chunk) <= 1:
        raise last_exc or RuntimeError("Music detection failed")

    mid = max(1, len(chunk) // 2)
    logger.warning(
        "   🔪 Splitting music chunk (%s segments) at depth %s after failures: %s",
        len(chunk),
        _depth,
        last_exc,
    )
    left = _detect_music_chunk(
        model,
        chunk=chunk[:mid],
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    right = _detect_music_chunk(
        model,
        chunk=chunk[mid:],
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    return left + right


def _detect_music_ids(
    model: GenerativeModel,
    *,
    segments: list[dict],
    max_attempts: int,
    split_after_attempts: int,
    chunk_size: int,
) -> set[int]:
    music_ids = _music_heuristic_ids(segments)
    remaining: list[dict] = []
    for seg in segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        if seg_id in music_ids:
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if _looks_like_speech(text):
            continue
        remaining.append(seg)

    for offset in range(0, len(remaining), chunk_size):
        chunk = remaining[offset : offset + chunk_size]
        music_ids.update(
            _detect_music_chunk(
                model,
                chunk=chunk,
                max_attempts=max_attempts,
                split_after_attempts=split_after_attempts,
            )
        )
    safe_ids: set[int] = set()
    for seg in segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        if seg_id not in music_ids:
            continue
        text = str(seg.get("text") or "").strip()
        if _is_music_marker_text(text) or not _looks_like_speech(text):
            safe_ids.add(seg_id)
    return safe_ids


def _polish_chunk_once(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
    doc_brief: str,
) -> list[dict]:
    input_payload = [
        {
            "id": int(seg["id"]),
            "source": str(seg.get("source") or "").strip(),
            "draft": str(seg.get("draft") or "").strip(),
        }
        for seg in chunk
    ]

    continuity_payload = [
        {
            "id": int(seg["id"]),
            "translated": str(seg.get("translated") or "").strip(),
        }
        for seg in (continuity or [])
        if seg.get("translated")
    ]

    system_instruction = profiles.get_system_instruction(target_language_code, program_profile)
    brief_block = ""
    if doc_brief:
        brief_block = f"""
DOCUMENT BRIEF (for consistency only; do not infer facts):
{doc_brief}
"""

    prompt = f"""
ROLE: You are the Senior Broadcast Editor for Omega TV.

SYSTEM INSTRUCTION (obey strictly):
{system_instruction}

TASK:
- Polish the DRAFT translation into natural, broadcast-ready {target_language_name}.
- Preserve the SOURCE meaning exactly (no added or missing information).
- Keep names, scripture references, and theological terms intact.
- Be concise to reduce CPS; remove filler and tighten phrasing.
- Return ONLY JSON (no markdown fences).
- Output MUST be a JSON array of objects: {{ "id": <int>, "text": <string> }}.
- Output MUST contain EXACTLY the IDs from INPUT (no extras, none missing).
- Do NOT output ALL CAPS sentences. Preserve acronyms/initialisms and mandatory titles.
{brief_block}

CONTEXT (for consistency only; DO NOT rewrite these IDs):
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
        temperature=0.2,
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


def _polish_chunk(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
    doc_brief: str,
    max_attempts: int,
    split_after_attempts: int,
    _depth: int = 0,
) -> list[dict]:
    if not chunk:
        return []

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _polish_chunk_once(
                model,
                chunk=chunk,
                target_language_name=target_language_name,
                target_language_code=target_language_code,
                program_profile=program_profile,
                continuity=continuity,
                doc_brief=doc_brief,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if len(chunk) > 1 and attempt >= split_after_attempts:
                break
            logger.warning("   ⚠️ Polish retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)
        except Exception as exc:
            last_exc = exc
            logger.warning("   ⚠️ Polish retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)

    if len(chunk) <= 1:
        raise last_exc or RuntimeError("Chunk polishing failed")

    mid = max(1, len(chunk) // 2)
    logger.warning(
        "   🔪 Splitting polish chunk (%s segments) at depth %s after failures: %s",
        len(chunk),
        _depth,
        last_exc,
    )
    left = _polish_chunk(
        model,
        chunk=chunk[:mid],
        target_language_name=target_language_name,
        target_language_code=target_language_code,
        program_profile=program_profile,
        continuity=continuity,
        doc_brief=doc_brief,
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    tail_context = _build_continuity_window(continuity, left, max_items=len(continuity) or 8)
    right = _polish_chunk(
        model,
        chunk=chunk[mid:],
        target_language_name=target_language_name,
        target_language_code=target_language_code,
        program_profile=program_profile,
        continuity=tail_context,
        doc_brief=doc_brief,
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
        "pt": "Portuguese",
        "it": "Italian",
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
    polish_model_name = str(job.get("polish_model") or config.MODEL_POLISH).strip()
    polish_pass = bool(job.get("polish_pass"))
    music_detect = _is_truthy(job.get("music_detect", os.environ.get("OMEGA_CLOUD_MUSIC_DETECT", "1")))
    doc_brief_enabled = _is_truthy(job.get("doc_brief", os.environ.get("OMEGA_CLOUD_DOC_BRIEF", "1")))

    max_attempts = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_MAX_ATTEMPTS", "6") or 6)
    split_after_attempts = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_SPLIT_AFTER", "2") or 2)
    chunk_size = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_CHUNK_SIZE", "90") or 90)
    chunk_size = max(10, min(chunk_size, 220))
    continuity_size = int(os.environ.get("OMEGA_CLOUD_CONTINUITY_SIZE", "8") or 8)
    continuity_size = max(0, min(continuity_size, 30))
    polish_chunk_size = int(os.environ.get("OMEGA_CLOUD_POLISH_CHUNK_SIZE", "70") or 70)
    polish_chunk_size = max(10, min(polish_chunk_size, 180))
    polish_continuity_size = int(
        os.environ.get("OMEGA_CLOUD_POLISH_CONTINUITY_SIZE", str(continuity_size)) or continuity_size
    )
    polish_continuity_size = max(0, min(polish_continuity_size, 30))
    music_chunk_size = int(os.environ.get("OMEGA_CLOUD_MUSIC_CHUNK_SIZE", "120") or 120)
    music_chunk_size = max(20, min(music_chunk_size, 240))
    polish_max_fixes = int(os.environ.get("OMEGA_CLOUD_POLISH_MAX_FIXES", "8") or 8)
    polish_max_fixes = max(0, min(polish_max_fixes, 20))
    doc_brief_segments = int(os.environ.get("OMEGA_CLOUD_DOC_BRIEF_SEGMENTS", "120") or 120)
    doc_brief_segments = max(20, min(doc_brief_segments, 240))
    doc_brief_chars = int(os.environ.get("OMEGA_CLOUD_DOC_BRIEF_CHARS", "12000") or 12000)
    doc_brief_chars = max(2000, min(doc_brief_chars, 24000))

    _write_progress(
        storage_client,
        paths=paths,
        stage="CLOUD_TRANSLATING",
        status="Loading skeleton",
        progress=40.0,
        meta={
            "translator_model": translator_model_name,
            "editor_model": editor_model_name,
            "polish_model": polish_model_name,
            "polish_pass": polish_pass,
            "music_detect": music_detect,
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
    doc_brief = ""
    if doc_brief_enabled:
        _write_progress(
            storage_client,
            paths=paths,
            stage="CLOUD_TRANSLATING",
            status="Summarizing program",
            progress=40.2,
        )
        doc_brief = _build_document_brief(
            translator_model,
            segments=segments,
            max_segments=doc_brief_segments,
            max_chars=doc_brief_chars,
        )

    checkpoint = None
    if blob_exists(storage_client, paths.bucket, paths.translation_checkpoint_json()):
        checkpoint = download_json(storage_client, bucket=paths.bucket, blob_name=paths.translation_checkpoint_json())
    translated_map: Dict[str, str] = {}
    if isinstance(checkpoint, dict):
        raw = checkpoint.get("translated") or {}
        if isinstance(raw, dict):
            translated_map = {str(k): str(v) for k, v in raw.items() if v is not None}

    if music_detect:
        _write_progress(
            storage_client,
            paths=paths,
            stage="CLOUD_DETECTING_MUSIC",
            status="Detecting music segments",
            progress=41.0,
        )
        music_ids = _detect_music_ids(
            translator_model,
            segments=segments,
            max_attempts=max_attempts,
            split_after_attempts=split_after_attempts,
            chunk_size=music_chunk_size,
        )
        applied_music_ids: list[int] = []
        if music_ids:
            for seg in segments:
                try:
                    seg_id = int(seg.get("id"))
                except Exception:
                    continue
                if seg_id not in music_ids:
                    continue
                text = str(seg.get("text") or "").strip()
                if _is_music_marker_text(text) or not text:
                    seg["music_original_text"] = seg.get("text")
                    seg["text"] = "(MUSIC)"
                    applied_music_ids.append(seg_id)
                else:
                    seg["music_hint"] = True
            for seg_id in applied_music_ids:
                translated_map[str(seg_id)] = "(MUSIC)"
        _write_progress(
            storage_client,
            paths=paths,
            stage="CLOUD_DETECTING_MUSIC",
            status=f"Marked {len(applied_music_ids)} music segments",
            progress=42.0,
            meta={"music_segments": len(applied_music_ids), "music_detected": len(music_ids)},
        )

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
                doc_brief=doc_brief,
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

    review_segments = translated_segments

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
        translated_segments=review_segments,
        lang_suffix=target_language_code.upper(),
    )
    editor_response = editor_model.generate_content(
        editor_prompt,
        generation_config=GenerationConfig(response_mime_type="application/json", temperature=0.1),
    )

    corrections, report = _parse_editor_response(getattr(editor_response, "text", "") or "")
    approved_segments = _apply_editor_corrections(
        source_segments=segments,
        translated_segments=review_segments,
        corrections=corrections,
    )
    post_polish_segments = approved_segments
    polish_fixes = 0
    if polish_pass and polish_max_fixes > 0:
        _write_progress(
            storage_client,
            paths=paths,
            stage="CLOUD_POLISHING",
            status="Polishing (global sweep)",
            progress=70.0,
            meta={"polish_max_fixes": polish_max_fixes},
        )
        polish_model = GenerativeModel(polish_model_name)
        polish_prompt = _build_polish_prompt(
            source_segments=segments,
            translated_segments=approved_segments,
            lang_suffix=target_language_code.upper(),
            max_fixes=polish_max_fixes,
        )
        polish_corrections: list[dict] = []
        try:
            polish_response = polish_model.generate_content(
                polish_prompt,
                generation_config=GenerationConfig(
                    response_mime_type="application/json",
                    response_schema={
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "fix": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["id", "fix"],
                        },
                    },
                    temperature=0.15,
                ),
                safety_settings=SAFETY_SETTINGS,
            )
            polish_corrections = _parse_polish_corrections(getattr(polish_response, "text", "") or "")
        except Exception as exc:
            logger.warning("   ⚠️ Global polish failed: %s", exc)
            polish_corrections = []

        if polish_corrections:
            post_polish_segments = _apply_editor_corrections(
                source_segments=segments,
                translated_segments=approved_segments,
                corrections=polish_corrections,
            )
            polish_fixes = len(polish_corrections)
    approved_payload = {
        "segments": post_polish_segments,
        "meta": {
            "editor_model": editor_model_name,
            "polish_model": polish_model_name if polish_pass else None,
            "polish_pass": polish_pass,
            "polish_mode": "global" if polish_pass else None,
            "polish_fixes": polish_fixes,
            "polish_max_fixes": polish_max_fixes if polish_pass else None,
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
    priority_context = _build_priority_context(source_segments, translated_segments, include_tight=True)
    if lang_suffix.upper() in {"ICELANDIC", "IS"}:
        return f"""
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
7. ASR CONTEXTUAL CORRECTION:
   - Look for homophones or contextually jarring words (e.g., "hole" vs "hold", "Halloween" vs "Hallowed" in a prayer).
   - Correct these in the translation based on the surrounding theological or program context.
8. NATURAL PHRASING:
   - Avoid literal "We have gotten" (Við höfum fengið) for weather or states; prefer existential "það er/hefur verið" (there is/has been).
9. TECHNICAL CONSTRAINTS (Broadcast):
   - Each segment has max 2 lines (<=42 chars each; <=84 total).
   - Use the provided `effective_duration`, `gap_to_next`, and `current_cps` to keep CPS <= 17.
   - If status is TIGHT, shorten only if it improves CPS without losing meaning.
   - If status is CRITICAL, you MUST shorten while preserving theology.
   - If shortening would change theology or remove Scripture, keep meaning and note it in `reason`.
10. CONTEXT WINDOW:
   - `context_prev` / `context_next` are read-only.
   - Use them to maintain gender/case agreement in Icelandic.

INPUT DATA:
--- SOURCE (English) ---
{json.dumps(source_segments, ensure_ascii=False)}

--- DRAFT (Icelandic) ---
{json.dumps(translated_segments, ensure_ascii=False)}

--- PRIORITY SEGMENTS (Constraint-Aware Window) ---
{json.dumps(priority_context, ensure_ascii=False)}

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
- ASR CLEANUP: Fix obvious speech-to-text errors in the SOURCE when the intended word is clear. If uncertain, leave the original wording.
- MUSIC VS SPEECH: If the SOURCE contains spoken content (not a pure music marker), the translation must NOT be "(MUSIC)" or blank.
- TECHNICAL CONSTRAINTS: Use provided `effective_duration`, `gap_to_next`, and `current_cps` in the priority list to keep CPS <= 17; if CRITICAL, shorten without losing meaning.
- CONTEXT WINDOW: `context_prev` / `context_next` are read-only; use them for grammatical agreement.

INPUT DATA:
--- SOURCE (English) ---
{json.dumps(source_segments, ensure_ascii=False)}

--- DRAFT ({lang_suffix}) ---
{json.dumps(translated_segments, ensure_ascii=False)}

--- PRIORITY SEGMENTS (Constraint-Aware Window) ---
{json.dumps(priority_context, ensure_ascii=False)}

OUTPUT:
Return a JSON object with 'corrections' and a 'report'.
"""


def _build_polish_prompt(
    *,
    source_segments: list[dict],
    translated_segments: list[dict],
    lang_suffix: str,
    max_fixes: int,
) -> str:
    source_payload = []
    for seg in source_segments or []:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        source_payload.append({"id": seg_id, "text": str(seg.get("text") or "").strip()})

    translated_payload = []
    for seg in translated_segments or []:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        translated_payload.append({"id": seg_id, "text": str(seg.get("text") or "").strip()})

    lang_label = lang_suffix.upper()
    max_fixes = max(0, int(max_fixes))
    extra_rules = ""
    if lang_label in {"IS", "ICELANDIC"}:
        extra_rules = '- God is addressed as "Þú" (do NOT use "Þér").\n'
    return f"""
ROLE: You are the Senior Polish Editor for Omega TV.

TASK:
- Read the full SOURCE and TRANSLATION for context.
- Identify only a small number of clear, high-impact wording improvements.
- Limit to at most {max_fixes} fixes. If nothing is truly better, return [].
- Preserve meaning exactly; do NOT add/remove information.
- Keep names, scripture references, and theological terms intact.
- Keep phrasing concise to reduce CPS and avoid awkward line breaks.
- Fix obvious speech-to-text errors if the intended word is clear; do not introduce new meaning.
- Return ONLY JSON (no markdown fences).
- Output MUST be a JSON array of objects: {{ "id": <int>, "fix": <string>, "reason": <string> }}.
- Do NOT output ALL CAPS sentences; keep acronyms/initialisms intact.
- ASR Context: Look for contextually jarring words (robot mistakes) and fix them.
- Natural Flow: Convert stiff "Við höfum fengið" weather/state reports to natural "Það er/hefur verið".
{extra_rules}

SOURCE (English):
{json.dumps(source_payload, ensure_ascii=False)}

TRANSLATION ({lang_label}):
{json.dumps(translated_payload, ensure_ascii=False)}
"""


def _parse_polish_corrections(text: str) -> list[dict]:
    cleaned = _clean_model_json(text)
    result = json.loads(cleaned)
    if isinstance(result, dict) and "corrections" in result:
        result = result.get("corrections")
    if not isinstance(result, list):
        raise ValueError("Polish response is not a JSON array")

    corrections: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        fix = item.get("fix")
        try:
            seg_id_int = int(seg_id)
        except Exception:
            continue
        if not isinstance(fix, str) or not fix.strip():
            continue
        corrections.append(
            {
                "id": seg_id_int,
                "fix": fix.strip(),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return corrections


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
