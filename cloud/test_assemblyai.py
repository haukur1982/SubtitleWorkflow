#!/usr/bin/env python3
"""
AssemblyAI Transcription Test

Tests AssemblyAI's transcription service for:
1. Word-level timestamp accuracy
2. Comparison with local WhisperX output

Usage:
    python test_assemblyai.py --audio /path/to/audio.wav --local-skeleton /path/to/skeleton.json
    
Set ASSEMBLYAI_API_KEY environment variable before running.
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import assemblyai as aai
except ImportError:
    print("Installing assemblyai...")
    os.system("pip install assemblyai")
    import assemblyai as aai


def transcribe_audio(audio_path: Path) -> dict:
    """
    Transcribe audio using AssemblyAI and return skeleton-format output.
    """
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY environment variable not set")
    
    aai.settings.api_key = api_key
    
    print(f"📤 Submitting to AssemblyAI: {audio_path}")
    
    config = aai.TranscriptionConfig(
        language_code="en",
    )
    
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(str(audio_path), config=config)
    
    if transcript.status == aai.TranscriptStatus.error:
        raise Exception(f"Transcription failed: {transcript.error}")
    
    print(f"✅ Transcription complete: {len(transcript.words)} words")
    
    # Build skeleton in pipeline format
    # Group words into segments (sentences based on punctuation)
    segments = []
    current_segment_words = []
    current_start = None
    segment_id = 1
    
    for word in transcript.words:
        if current_start is None:
            current_start = word.start / 1000.0  # Convert ms to seconds
        
        current_segment_words.append(word.text)
        
        # End segment on sentence-ending punctuation
        if word.text.rstrip().endswith(('.', '?', '!')):
            text = ' '.join(current_segment_words)
            segments.append({
                "id": segment_id,
                "start": current_start,
                "end": word.end / 1000.0,
                "text": text.strip()
            })
            segment_id += 1
            current_segment_words = []
            current_start = None
    
    # Handle remaining words
    if current_segment_words:
        text = ' '.join(current_segment_words)
        last_word = transcript.words[-1]
        segments.append({
            "id": segment_id,
            "start": current_start,
            "end": last_word.end / 1000.0,
            "text": text.strip()
        })
    
    skeleton = {
        "file": audio_path.stem,
        "segments": segments,
        "words": [
            {
                "word": w.text,
                "start": w.start / 1000.0,
                "end": w.end / 1000.0,
                "confidence": w.confidence
            }
            for w in transcript.words
        ]
    }
    
    return skeleton


def compare_timestamps(local_skeleton: dict, cloud_skeleton: dict) -> dict:
    """
    Compare word timestamps between local and cloud transcriptions.
    """
    local_words = local_skeleton.get("words", [])
    cloud_words = cloud_skeleton.get("words", [])
    
    if not local_words:
        # Extract words from segments if not available
        print("⚠️  Local skeleton has no word-level data, comparing segment timestamps")
        local_segs = local_skeleton.get("segments", [])
        cloud_segs = cloud_skeleton.get("segments", [])
        
        min_segs = min(len(local_segs), len(cloud_segs))
        diffs = []
        for i in range(min_segs):
            start_diff = abs(local_segs[i]["start"] - cloud_segs[i]["start"])
            end_diff = abs(local_segs[i]["end"] - cloud_segs[i]["end"])
            diffs.append((start_diff + end_diff) / 2)
        
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        max_diff = max(diffs) if diffs else 0
        
        return {
            "comparison_type": "segment",
            "segments_compared": min_segs,
            "avg_timestamp_diff_ms": avg_diff * 1000,
            "max_timestamp_diff_ms": max_diff * 1000,
            "acceptable": avg_diff < 0.5  # Less than 500ms average
        }
    
    # Word-level comparison
    min_words = min(len(local_words), len(cloud_words))
    diffs = []
    for i in range(min_words):
        start_diff = abs(local_words[i]["start"] - cloud_words[i]["start"])
        diffs.append(start_diff)
    
    avg_diff = sum(diffs) / len(diffs) if diffs else 0
    max_diff = max(diffs) if diffs else 0
    
    return {
        "comparison_type": "word",
        "words_compared": min_words,
        "avg_timestamp_diff_ms": avg_diff * 1000,
        "max_timestamp_diff_ms": max_diff * 1000,
        "acceptable": avg_diff < 0.3  # Less than 300ms average for words
    }


def main():
    parser = argparse.ArgumentParser(description="Test AssemblyAI transcription")
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--local-skeleton", help="Path to local WhisperX skeleton for comparison")
    parser.add_argument("--output", help="Output path for AssemblyAI skeleton")
    args = parser.parse_args()
    
    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"❌ Audio file not found: {audio_path}")
        sys.exit(1)
    
    # Transcribe
    skeleton = transcribe_audio(audio_path)
    
    # Save output
    output_path = Path(args.output) if args.output else Path(f"assemblyai_{audio_path.stem}.json")
    with open(output_path, "w") as f:
        json.dump(skeleton, f, indent=2)
    print(f"💾 Saved skeleton to: {output_path}")
    
    # Compare if local skeleton provided
    if args.local_skeleton:
        local_path = Path(args.local_skeleton)
        if local_path.exists():
            with open(local_path) as f:
                local_skeleton = json.load(f)
            
            comparison = compare_timestamps(local_skeleton, skeleton)
            print("\n📊 Timestamp Comparison:")
            print(f"   Type: {comparison['comparison_type']}")
            print(f"   Items compared: {comparison.get('words_compared', comparison.get('segments_compared'))}")
            print(f"   Avg difference: {comparison['avg_timestamp_diff_ms']:.1f}ms")
            print(f"   Max difference: {comparison['max_timestamp_diff_ms']:.1f}ms")
            print(f"   Acceptable: {'✅ YES' if comparison['acceptable'] else '❌ NO'}")
        else:
            print(f"⚠️  Local skeleton not found: {local_path}")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
