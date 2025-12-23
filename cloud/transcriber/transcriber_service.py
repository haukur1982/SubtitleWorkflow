#!/usr/bin/env python3
"""
Cloud Transcriber Service — WhisperX on GPU

Runs WhisperX transcription with word-level alignment on NVIDIA GPU.
Designed for Cloud Run Jobs with L4 GPU.

Usage:
    python transcriber_service.py --job-id <id> --bucket <bucket> --prefix <prefix>

Expects:
    gs://{bucket}/{prefix}/{job_id}/audio.wav
    gs://{bucket}/{prefix}/{job_id}/job.json (optional, for metadata)

Produces:
    gs://{bucket}/{prefix}/{job_id}/skeleton.json
    gs://{bucket}/{prefix}/{job_id}/transcription_progress.json
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import torch
from google.cloud import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("CloudTranscriber")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WHISPER_MODEL = os.environ.get("OMEGA_WHISPER_MODEL", "large-v3")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "float32"
BATCH_SIZE = int(os.environ.get("OMEGA_WHISPER_BATCH_SIZE", "16" if DEVICE == "cuda" else "1"))

# Safety markers to detect music segments
SAFETY_MARKERS = {
    "(music)", "[music]", "(song)", "[song]",
    "(singing)", "[singing]", "(choir)", "[choir]", "♪"
}


def _is_music_marker(text: str) -> bool:
    """Check if text is a music marker."""
    if not text:
        return False
    lowered = text.strip().lower()
    return lowered in SAFETY_MARKERS or "♪" in text


# ---------------------------------------------------------------------------
# GCS Helpers
# ---------------------------------------------------------------------------

def download_blob(client: storage.Client, bucket: str, blob_path: str, local_path: Path) -> bool:
    """Download a blob from GCS. Returns True if successful."""
    try:
        blob = client.bucket(bucket).blob(blob_path)
        blob.download_to_filename(str(local_path))
        logger.info(f"Downloaded gs://{bucket}/{blob_path}")
        return True
    except Exception as e:
        logger.warning(f"Failed to download gs://{bucket}/{blob_path}: {e}")
        return False


def upload_json(client: storage.Client, bucket: str, blob_path: str, data: dict):
    """Upload JSON data to GCS."""
    blob = client.bucket(bucket).blob(blob_path)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    blob.upload_from_string(content, content_type="application/json")
    logger.info(f"Uploaded gs://{bucket}/{blob_path}")


def write_progress(
    client: storage.Client,
    bucket: str,
    prefix: str,
    job_id: str,
    *,
    stage: str,
    status: str,
    progress: float,
    error: Optional[str] = None,
):
    """Write progress JSON for polling by the local manager."""
    blob_path = f"{prefix}/{job_id}/transcription_progress.json"
    payload = {
        "stage": stage,
        "status": status,
        "progress": round(progress, 2),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if error:
        payload["error"] = error
    upload_json(client, bucket, blob_path, payload)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_audio(audio_path: Path, language: str = "en") -> dict:
    """
    Run WhisperX transcription with word-level alignment.
    
    Returns skeleton dict in the format expected by the pipeline:
    {
        "file": "<stem>",
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.5, "text": "Hello world"},
            ...
        ]
    }
    """
    import whisperx
    
    logger.info(f"Device: {DEVICE}, Compute: {COMPUTE_TYPE}, Batch: {BATCH_SIZE}")
    logger.info(f"Loading WhisperX model: {WHISPER_MODEL}")
    
    # Load model
    model = whisperx.load_model(
        WHISPER_MODEL,
        DEVICE,
        compute_type=COMPUTE_TYPE,
        language=language,
    )
    
    # Load audio
    logger.info(f"Loading audio: {audio_path}")
    audio = whisperx.load_audio(str(audio_path))
    
    # Transcribe
    logger.info("Starting transcription...")
    result = model.transcribe(audio, batch_size=BATCH_SIZE, language=language)
    logger.info(f"Transcription complete: {len(result.get('segments', []))} segments")
    
    # Align for word-level timestamps
    logger.info("Loading alignment model...")
    model_a, metadata = whisperx.load_align_model(language_code=language, device=DEVICE)
    
    logger.info("Aligning for word-level timestamps...")
    result = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        audio,
        DEVICE,
        return_char_alignments=False,
    )
    logger.info("Alignment complete")
    
    # Build skeleton in pipeline format
    segments = []
    for i, seg in enumerate(result.get("segments", [])):
        text = (seg.get("text") or "").strip()
        
        # Skip empty or music-only segments
        if not text or _is_music_marker(text):
            continue
        
        segments.append({
            "id": i + 1,
            "start": seg.get("start"),
            "end": seg.get("end"),
            "text": text,
        })
    
    # Re-number after filtering
    for i, seg in enumerate(segments):
        seg["id"] = i + 1
    
    logger.info(f"Final skeleton: {len(segments)} segments")
    
    return {
        "file": audio_path.stem,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_job(*, bucket: str, prefix: str, job_id: str):
    """Main job runner."""
    client = storage.Client()
    
    # Report starting
    write_progress(client, bucket, prefix, job_id, stage="TRANSCRIBING", status="Initializing", progress=0.0)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        audio_path = tmpdir_path / "audio.wav"
        
        # Download audio
        audio_blob_path = f"{prefix}/{job_id}/audio.wav"
        write_progress(client, bucket, prefix, job_id, stage="TRANSCRIBING", status="Downloading audio", progress=5.0)
        
        if not download_blob(client, bucket, audio_blob_path, audio_path):
            write_progress(
                client, bucket, prefix, job_id,
                stage="ERROR", status="Failed to download audio", progress=0.0,
                error=f"Audio not found: gs://{bucket}/{audio_blob_path}"
            )
            return 1
        
        # Optionally load job.json for language/profile info
        job_json_path = tmpdir_path / "job.json"
        language = "en"  # Default
        if download_blob(client, bucket, f"{prefix}/{job_id}/job.json", job_json_path):
            try:
                with open(job_json_path) as f:
                    job_meta = json.load(f)
                # Could extract source language if needed
            except Exception:
                pass
        
        # Transcribe
        write_progress(client, bucket, prefix, job_id, stage="TRANSCRIBING", status="Running WhisperX", progress=10.0)
        
        try:
            skeleton = transcribe_audio(audio_path, language=language)
        except Exception as e:
            logger.exception("Transcription failed")
            write_progress(
                client, bucket, prefix, job_id,
                stage="ERROR", status="Transcription failed", progress=0.0,
                error=str(e)
            )
            return 1
        
        # Upload skeleton
        write_progress(client, bucket, prefix, job_id, stage="TRANSCRIBING", status="Uploading skeleton", progress=95.0)
        skeleton_blob_path = f"{prefix}/{job_id}/skeleton.json"
        upload_json(client, bucket, skeleton_blob_path, skeleton)
        
        # Done
        write_progress(client, bucket, prefix, job_id, stage="DONE", status="Transcription complete", progress=100.0)
        logger.info("Job complete!")
    
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cloud Transcriber Service")
    parser.add_argument("--job-id", required=True, help="Job ID (folder name in GCS)")
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument("--prefix", default="jobs", help="GCS prefix (default: jobs)")
    args = parser.parse_args(argv)
    
    logger.info(f"Starting transcription job: {args.job_id}")
    logger.info(f"Bucket: {args.bucket}, Prefix: {args.prefix}")
    logger.info(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        logger.info(f"GPU: {torch.cuda.get_device_name(0)}")
    
    return run_job(bucket=args.bucket, prefix=args.prefix, job_id=args.job_id)


if __name__ == "__main__":
    sys.exit(main())
