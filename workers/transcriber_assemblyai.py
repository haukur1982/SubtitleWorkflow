"""
AssemblyAI Transcription Module

Provides fast, reliable transcription via AssemblyAI API with:
- Word-level timestamps
- Word Boost for religious vocabulary
- Skeleton output compatible with the pipeline
"""

import os
import json
import logging
import time
from pathlib import Path
from typing import Optional

try:
    import assemblyai as aai
except ImportError:
    aai = None

import config
import omega_db

logger = logging.getLogger("OmegaManager.Transcriber.AssemblyAI")

# Default religious vocabulary for Word Boost
RELIGIOUS_VOCABULARY = [
    # Names
    "Jesus", "Christ", "Holy Spirit", "God", "Lord", "Father",
    "Messiah", "Savior", "Redeemer",
    # Biblical Places
    "Jerusalem", "Bethlehem", "Galilee", "Nazareth", "Israel",
    "Jordan", "Samaria", "Judea",
    # Terms
    "Hallelujah", "Amen", "Scripture", "Gospel", "Bible",
    "Resurrection", "Redemption", "Salvation", "Covenant",
    "Grace", "Faith", "Baptism", "Communion", "Crucifixion",
    "Prophet", "Apostle", "Disciple", "Pharisee",
    # Shows/Ministries
    "Times Square Church", "Billy Graham", "CBN", "700 Club",
    "Joyce Meyer", "Praise", "Gospel",
]


def _get_word_boost() -> list[str]:
    """
    Returns combined word boost list from defaults + config.
    """
    words = list(RELIGIOUS_VOCABULARY)
    
    # Add custom words from config
    custom = getattr(config, "ASSEMBLYAI_WORD_BOOST", "")
    if custom:
        custom_words = [w.strip() for w in custom.split(",") if w.strip()]
        words.extend(custom_words)
    
    return words


def _get_boost_weight() -> str:
    """Returns boost weight from config (low, default, high)."""
    weight = getattr(config, "ASSEMBLYAI_BOOST_WEIGHT", "high").lower()
    if weight not in {"low", "default", "high"}:
        weight = "high"
    return weight


def _segment_words(words: list) -> list[dict]:
    """
    Groups word-level timestamps into sentence segments.
    Splits on sentence-ending punctuation (. ? !)
    """
    if not words:
        return []
    
    segments = []
    current_words = []
    current_start = None
    segment_id = 1
    
    for word in words:
        word_text = word.text if hasattr(word, 'text') else word.get('text', '')
        word_start = word.start if hasattr(word, 'start') else word.get('start', 0)
        word_end = word.end if hasattr(word, 'end') else word.get('end', 0)
        
        # AssemblyAI returns milliseconds, convert to seconds
        word_start = word_start / 1000.0
        word_end = word_end / 1000.0
        
        if current_start is None:
            current_start = word_start
        
        current_words.append(word_text)
        
        # End segment on sentence-ending punctuation
        if word_text.rstrip().endswith(('.', '?', '!')):
            text = ' '.join(current_words)
            segments.append({
                "id": segment_id,
                "start": round(current_start, 3),
                "end": round(word_end, 3),
                "text": text.strip()
            })
            segment_id += 1
            current_words = []
            current_start = None
    
    # Handle remaining words (no sentence-ender)
    if current_words:
        last_word = words[-1]
        last_end = last_word.end if hasattr(last_word, 'end') else last_word.get('end', 0)
        last_end = last_end / 1000.0  # Convert ms to seconds
        
        text = ' '.join(current_words)
        segments.append({
            "id": segment_id,
            "start": round(current_start, 3),
            "end": round(last_end, 3),
            "text": text.strip()
        })
    
    return segments


# Opening music detection heuristic
OPENING_MUSIC_SECONDS = 90  # First 90 seconds can be opening worship
WORSHIP_PATTERNS = [
    # Direct worship words
    "almighty", "hallelujah", "praise", "glory", "holy", "amen",
    "worship", "lord", "jesus", "god", "savior", "king of kings",
    # Common worship phrases (short)
    "you are", "we praise", "we worship", "i love you", "thank you",
    # Music markers
    "♪", "(music)", "[music]", "(singing)", "[singing]",
]
# Phrases that indicate SPEECH (not music) even in opening
SPEECH_INDICATORS = [
    "today", "tonight", "we're going to", "i want to", "let me",
    "good morning", "good evening", "welcome to", "we're calling",
    "our message", "this message", "my subtitle", "chapter",
]


def _is_worship_pattern(text: str) -> bool:
    """Check if text matches worship/music patterns."""
    lowered = text.lower().strip()
    
    # Check for speech indicators (not music)
    for indicator in SPEECH_INDICATORS:
        if indicator in lowered:
            return False
    
    # Check for worship patterns
    for pattern in WORSHIP_PATTERNS:
        if pattern in lowered:
            return True
    
    # Short repetitive phrases in opening are likely worship
    word_count = len(lowered.split())
    if word_count <= 6:
        # Very short phrases with certain words
        if any(w in lowered for w in ["lord", "god", "jesus", "you", "praise", "glory"]):
            return True
    
    return False


def _mark_opening_music(segments: list[dict]) -> tuple[list[dict], int]:
    """
    Marks opening worship/music segments as (MUSIC).
    
    Returns (modified segments, count of marked segments).
    
    Heuristic:
    1. Check segments in first OPENING_MUSIC_SECONDS
    2. If they match worship patterns and are short, mark as music
    3. Stop when we hit clear speech content
    """
    if not segments:
        return segments, 0
    
    marked_count = 0
    in_speech = False
    
    for segment in segments:
        # Stop processing if we've passed the opening window
        if segment.get("start", 0) > OPENING_MUSIC_SECONDS:
            break
        
        # Once we've hit speech, stop marking
        if in_speech:
            break
        
        text = segment.get("text", "")
        
        # Check if this is clearly speech (not music)
        lowered = text.lower()
        for indicator in SPEECH_INDICATORS:
            if indicator in lowered:
                in_speech = True
                break
        
        if in_speech:
            break
        
        # Check if this matches worship patterns
        if _is_worship_pattern(text):
            segment["original_text"] = text
            segment["text"] = "(MUSIC)"
            segment["is_music"] = True
            marked_count += 1
            logger.debug(f"🎵 Marked as music: [{segment['id']}] {text[:50]}")
    
    return segments, marked_count


def transcribe_assemblyai(audio_path: Path, max_retries: int = 3) -> Path:
    """
    Transcribes audio via AssemblyAI API.
    
    Returns path to skeleton JSON in pipeline format.
    
    Raises:
        ValueError: If API key not configured
        RuntimeError: If transcription fails after retries
    """
    if aai is None:
        raise RuntimeError("assemblyai package not installed. Run: pip install assemblyai")
    
    api_key = getattr(config, "ASSEMBLYAI_API_KEY", "") or os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY not configured")
    
    stem = audio_path.stem
    output_dir = config.VAULT_DATA
    skeleton_path = output_dir / f"{stem}_SKELETON.json"
    
    logger.info(f"📤 AssemblyAI: Submitting {audio_path.name}")
    omega_db.update(stem, status="Submitting to AssemblyAI", progress=12.0)
    
    # Configure AssemblyAI
    aai.settings.api_key = api_key
    
    word_boost = _get_word_boost()
    boost_weight = _get_boost_weight()
    
    transcription_config = aai.TranscriptionConfig(
        language_code="en",
        word_boost=word_boost,
        boost_param=boost_weight,
    )
    
    # Retry loop
    last_error = None
    for attempt in range(max_retries):
        try:
            omega_db.update(stem, status=f"Transcribing via AssemblyAI (attempt {attempt + 1})", progress=15.0)
            
            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(str(audio_path), config=transcription_config)
            
            if transcript.status == aai.TranscriptStatus.error:
                raise RuntimeError(f"AssemblyAI error: {transcript.error}")
            
            # Success
            word_count = len(transcript.words) if transcript.words else 0
            logger.info(f"✅ AssemblyAI: Transcription complete ({word_count} words)")
            omega_db.update(stem, status=f"Transcribed ({word_count} words)", progress=25.0)
            
            # Build skeleton
            segments = _segment_words(transcript.words)
            
            # Music detection: try professional classifier first, fallback to heuristic
            music_count = 0
            try:
                from workers.audio_classifier import is_available, mark_music_segments
                if is_available():
                    omega_db.update(stem, status="Detecting music (CNN classifier)", progress=27.0)
                    segments, music_count = mark_music_segments(segments, audio_path)
                    if music_count > 0:
                        logger.info(f"🎵 inaSpeechSegmenter: Marked {music_count} segments as (MUSIC)")
            except Exception as e:
                logger.debug(f"inaSpeechSegmenter not available: {e}")
            
            # If professional classifier didn't mark anything, use heuristic for opening
            if music_count == 0:
                segments, music_count = _mark_opening_music(segments)
                if music_count > 0:
                    logger.info(f"🎵 Heuristic: Marked {music_count} opening segments as (MUSIC)")
            
            skeleton = {
                "file": stem,
                "segments": segments
            }
            
            # Save skeleton
            with open(skeleton_path, "w", encoding="utf-8") as f:
                json.dump(skeleton, f, indent=2, ensure_ascii=False)
            
            logger.info(f"✅ Skeleton saved: {skeleton_path.name} ({len(segments)} segments)")
            return skeleton_path
            
        except Exception as e:
            last_error = e
            logger.warning(f"⚠️ AssemblyAI attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
    
    raise RuntimeError(f"AssemblyAI failed after {max_retries} attempts: {last_error}")
