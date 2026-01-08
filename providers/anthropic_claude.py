"""
Anthropic Claude API Adapter for Phase 3 (Senior Polish Editor).

This module provides the integration with Claude Opus 4 for the final
translation polish step. It receives the full source + draft translation
in a single API call and returns corrections with confidence scores.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
import config

logger = logging.getLogger("OmegaCloudWorker.Claude")

# Check for anthropic package
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic package not installed. Claude Phase 3 will be unavailable.")


def is_claude_available() -> bool:
    """Check if Claude API is available and configured."""
    if not ANTHROPIC_AVAILABLE:
        return False
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    return bool(api_key)


def polish_with_claude(
    *,
    source_segments: List[Dict],
    draft_segments: List[Dict],
    target_language_code: str,
    target_language_name: str,
    bible_version: str,
    god_address: str,
    program_profile: str,
    glossary: Dict[str, str],
    max_fixes: int = 20,
) -> Dict[str, Any]:
    """
    Send the full translation to Claude for final polish.
    
    Args:
        source_segments: List of {id, text} with English source
        draft_segments: List of {id, text} with translation draft
        target_language_code: e.g., "is", "de", "es"
        target_language_name: e.g., "Icelandic", "German", "Spanish"
        bible_version: e.g., "Biblían 2007", "Luther 2017"
        god_address: e.g., "Þú (NOT Þér)", "Du (reverent)"
        program_profile: e.g., "In Touch - Charles Stanley"
        glossary: Dict of term -> translation
        max_fixes: Maximum corrections to return
        
    Returns:
        Dict with:
        - rating: 1-10 quality score
        - summary: Overall assessment
        - corrections: List of {id, original, fix, reason, confidence, category}
        - patterns: List of recurring issues to feed back to Phases 1/2
    """
    if not ANTHROPIC_AVAILABLE:
        raise RuntimeError("anthropic package not installed")
    
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    
    # Build combined segments for context
    segments_text = []
    draft_map = {int(s.get("id")): s.get("text", "") for s in draft_segments if s.get("id")}
    
    for seg in source_segments:
        try:
            seg_id = int(seg.get("id"))
        except (TypeError, ValueError):
            continue
        source_text = str(seg.get("text") or "").strip()
        draft_text = str(draft_map.get(seg_id, "")).strip()
        if source_text or draft_text:
            segments_text.append(f"[{seg_id}] EN: {source_text}")
            segments_text.append(f"    {target_language_code.upper()}: {draft_text}")
            segments_text.append("")
    
    content = "\n".join(segments_text)
    glossary_text = "\n".join([f"- {en} → {trans}" for en, trans in glossary.items()])
    
    prompt = f"""ROLE: You are the Senior Polish Editor at Omega TV — a native {target_language_name} speaker with 20 years in broadcast subtitling.

CONTEXT:
This translation has been through Lead Translator and Chief Editor (both AI). Your role is the FINAL QUALITY GATE.

TARGET LANGUAGE: {target_language_name} ({target_language_code})
BIBLE VERSION: {bible_version}
GOD ADDRESS: {god_address}
PROGRAM: {program_profile}

GLOSSARY (MANDATORY TERMS):
{glossary_text}

YOUR EXPERTISE:
- Native {target_language_name} (not "translation-isms")
- Theological {target_language_name} ({bible_version})
- Broadcast constraints (14-17 CPS, natural flow)

TASK:
Read as a native viewer. Find ONLY lines that:
1. Sound unnatural to a native {target_language_name} ear
2. Use "translation-isms" instead of natural phrasing
3. Have theological errors or wrong Bible terminology
4. Violate the glossary terms
5. Have wrong formal/informal address (God vs humans)

Limit to at most {max_fixes} fixes. If translation is already excellent, say so.

OUTPUT FORMAT (JSON):
{{
  "rating": 8,
  "summary": "Brief overall assessment",
  "corrections": [
    {{
      "id": 123,
      "original": "the draft text",
      "fix": "the corrected text",
      "reason": "why this fix is needed",
      "confidence": 0.95,
      "category": "anglicism|theological|glossary|register|naturalness"
    }}
  ],
  "patterns": [
    "Recurring issue 1 that should be fixed in Phase 1/2 prompts",
    "Recurring issue 2"
  ]
}}

CONTENT TO REVIEW:
{content}"""

    client = anthropic.Anthropic(api_key=api_key)
    
    try:
        message = client.messages.create(
            model=config.OMEGA_CLAUDE_MODEL,
            max_tokens=8192,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text if message.content else "{}"
        
        # Parse JSON response
        # Handle potential markdown code blocks
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        
        result = json.loads(cleaned)
        
        # Validate structure
        if "rating" not in result:
            result["rating"] = 7
        if "summary" not in result:
            result["summary"] = "Review complete"
        if "corrections" not in result:
            result["corrections"] = []
        if "patterns" not in result:
            result["patterns"] = []
            
        logger.info(f"✅ Claude Polish: {len(result['corrections'])} corrections, rating {result['rating']}/10")
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Claude response was not valid JSON: {e}")
        return {
            "rating": 0,
            "summary": f"Failed to parse Claude response: {e}",
            "corrections": [],
            "patterns": [],
            "raw_response": response_text[:1000] if 'response_text' in dir() else ""
        }
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        raise


def apply_claude_corrections(
    segments: List[Dict],
    corrections: List[Dict],
    min_confidence: float = 0.7,
) -> tuple[List[Dict], int]:
    """
    Apply Claude's corrections to the segments.
    
    Args:
        segments: List of {id, text, ...} segments
        corrections: List of corrections from polish_with_claude
        min_confidence: Only apply corrections above this confidence
        
    Returns:
        Tuple of (updated_segments, count_applied)
    """
    correction_map = {}
    for c in corrections:
        try:
            seg_id = int(c.get("id"))
            confidence = float(c.get("confidence", 0))
            if confidence >= min_confidence:
                correction_map[seg_id] = c.get("fix", "")
        except (TypeError, ValueError):
            continue
    
    applied = 0
    for seg in segments:
        try:
            seg_id = int(seg.get("id"))
        except (TypeError, ValueError):
            continue
        if seg_id in correction_map:
            seg["text"] = correction_map[seg_id]
            applied += 1
    
    return segments, applied
