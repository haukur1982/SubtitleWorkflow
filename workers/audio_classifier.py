"""
Audio Classifier using inaSpeechSegmenter

Provides professional-grade speech vs music detection for broadcast subtitling.
Uses CNN-based audio analysis (ranked #1 on French TV/Radio benchmark).

Usage:
    from workers.audio_classifier import get_music_ranges, is_music_timestamp
    
    music_ranges = get_music_ranges(audio_path)
    # Returns: [(0.0, 35.2), (120.8, 180.5), ...]
    
    if is_music_timestamp(10.5, music_ranges):
        segment['text'] = '(MUSIC)'

Installation:
    pip install inaSpeechSegmenter
    
Note: Requires TensorFlow. Recommended to run with GPU for speed.
"""

import logging
from pathlib import Path
from typing import List, Tuple, Optional

logger = logging.getLogger("OmegaManager.AudioClassifier")

_segmenter = None
_segmenter_available = None


def _check_segmenter_available() -> bool:
    """Check if inaSpeechSegmenter is installed."""
    global _segmenter_available
    if _segmenter_available is not None:
        return _segmenter_available
    
    try:
        from inaSpeechSegmenter import Segmenter
        _segmenter_available = True
        return True
    except ImportError:
        _segmenter_available = False
        logger.warning("⚠️ inaSpeechSegmenter not installed. Audio classification disabled.")
        logger.warning("   Install with: pip install inaSpeechSegmenter")
        return False


def get_segmenter():
    """
    Get or create the inaSpeechSegmenter instance.
    
    Uses 'smn' engine for speech/music/noise classification.
    Gender detection disabled for speed.
    """
    global _segmenter
    
    if not _check_segmenter_available():
        return None
    
    if _segmenter is None:
        from inaSpeechSegmenter import Segmenter
        logger.info("🎵 Initializing inaSpeechSegmenter (CNN-based audio classifier)...")
        _segmenter = Segmenter(vad_engine='smn', detect_gender=False)
        logger.info("✅ inaSpeechSegmenter ready")
    
    return _segmenter


def classify_audio(audio_path: Path) -> List[Tuple[str, float, float]]:
    """
    Classify audio into speech/music/noise segments.
    
    Args:
        audio_path: Path to audio file (any format supported by ffmpeg)
    
    Returns:
        List of (label, start_seconds, end_seconds) tuples.
        Labels: 'speech', 'music', 'noise', 'male', 'female'
        
    Raises:
        RuntimeError: If inaSpeechSegmenter is not available
    """
    segmenter = get_segmenter()
    if segmenter is None:
        raise RuntimeError("inaSpeechSegmenter not available")
    
    logger.info(f"🔊 Classifying audio: {audio_path.name}")
    result = segmenter(str(audio_path))
    
    # Count segments by type
    counts = {}
    for label, start, end in result:
        counts[label] = counts.get(label, 0) + 1
    
    logger.info(f"✅ Classification complete: {counts}")
    return result


def get_music_ranges(audio_path: Path) -> List[Tuple[float, float]]:
    """
    Get time ranges that are music (not speech).
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        List of (start_seconds, end_seconds) tuples for music segments.
        Returns empty list if classifier not available.
    """
    try:
        segments = classify_audio(audio_path)
        return [(start, end) for label, start, end in segments if label == 'music']
    except Exception as e:
        logger.warning(f"⚠️ Audio classification failed: {e}")
        return []


def is_music_timestamp(timestamp: float, music_ranges: List[Tuple[float, float]]) -> bool:
    """
    Check if a timestamp falls within a music range.
    
    Args:
        timestamp: Time in seconds
        music_ranges: List of (start, end) tuples from get_music_ranges()
        
    Returns:
        True if timestamp is within any music range
    """
    for start, end in music_ranges:
        if start <= timestamp <= end:
            return True
    return False


def mark_music_segments(
    segments: List[dict],
    audio_path: Path,
    music_marker: str = "(MUSIC)"
) -> Tuple[List[dict], int]:
    """
    Mark segments that fall within music time ranges.
    
    Uses inaSpeechSegmenter to detect music regions in the audio,
    then marks any transcript segments within those regions.
    
    Args:
        segments: List of segment dicts with 'start', 'end', 'text'
        audio_path: Path to the source audio file
        music_marker: Text to replace segment content with
        
    Returns:
        Tuple of (modified segments, count of marked segments)
    """
    if not segments:
        return segments, 0
    
    music_ranges = get_music_ranges(audio_path)
    if not music_ranges:
        return segments, 0
    
    marked_count = 0
    
    for segment in segments:
        start = segment.get('start', 0)
        end = segment.get('end', 0)
        mid = (start + end) / 2
        
        if is_music_timestamp(mid, music_ranges):
            segment['original_text'] = segment.get('text', '')
            segment['text'] = music_marker
            segment['is_music'] = True
            segment['music_source'] = 'inaSpeechSegmenter'
            marked_count += 1
    
    return segments, marked_count


# Quick availability check for use without full import
def is_available() -> bool:
    """Check if inaSpeechSegmenter is available."""
    return _check_segmenter_available()
