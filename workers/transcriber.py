import os
import shutil
import subprocess
import json
import logging
from pathlib import Path
from datetime import datetime
import config
import omega_db

logger = logging.getLogger("OmegaManager.Transcriber")

def ingest(file_path: Path):
    """
    Moves video to Vault and extracts audio.
    Returns (video_path, audio_path).
    """
    stem = file_path.stem
    
    # 1. Move to Vault
    vault_video_path = config.VAULT_VIDEOS / file_path.name
    
    if file_path.resolve() != vault_video_path.resolve():
        if vault_video_path.exists():
            os.remove(vault_video_path)
        shutil.move(str(file_path), str(vault_video_path))
        logger.info(f"📦 Moved to Vault: {vault_video_path.name}")
    
    # 2. Extract Audio
    audio_path = config.VAULT_DATA / f"{stem}.wav" # Using VAULT_DATA for audio temp
    # Actually config.py has VAULT_VIDEOS and VAULT_DATA. 
    # auto_skeleton used VAULT_AUDIO. Let's stick to VAULT_DATA for simplicity or add VAULT_AUDIO to config?
    # config.py didn't have VAULT_AUDIO. I'll use VAULT_DATA for now or create it.
    # Let's use VAULT_DATA/Audio to keep it clean.
    
    audio_dir = config.VAULT_DIR / "Audio"
    audio_dir.mkdir(exist_ok=True)
    audio_path = audio_dir / f"{stem}.wav"

    if not audio_path.exists():
        logger.info(f"🔊 Extracting Audio: {audio_path.name}")
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(vault_video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    
    return vault_video_path, audio_path

def transcribe(audio_path: Path):
    """
    Runs WhisperX on the audio file.
    Returns path to Skeleton JSON.
    """
    stem = audio_path.stem
    output_dir = config.VAULT_DATA
    
    logger.info(f"📝 Transcribing: {stem}")
    
    cmd = [
        str(config.WHISPER_BIN),
        str(audio_path),
        "--model", config.WHISPER_MODEL,
        "--language", "en",
        "--output_dir", str(output_dir),
        "--output_format", "json",
        "--compute_type", "float32", # int8 caused crashes on M1 Pro
        "--batch_size", "1", # Reduce batch size to prevent OOM
        "--device", config.WHISPER_DEVICE,
        "--print_progress", "True"
    ]
    
    try:
        print("DEBUG: Starting WhisperX subprocess...")
        # Remove pipes to let it print to console
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) 
        print("DEBUG: WhisperX subprocess finished.")
    except subprocess.CalledProcessError as e:
        logger.error(f"WhisperX Failed: {e}")
        raise e

    # Rename and Clean
    whisper_json = output_dir / f"{stem}.json"
    skeleton_path = output_dir / f"{stem}_SKELETON.json"
    
    if whisper_json.exists():
        with open(whisper_json, "r") as f:
            data = json.load(f)
            
        segments = []
        for i, seg in enumerate(data.get("segments", [])):
            segments.append({
                "id": i + 1, # Force sequential ID (1-based)
                "start": seg.get("start"),
                "end": seg.get("end"),
                "text": seg.get("text", "").strip()
            })
            
        # We don't know Mode/Style here, the Manager should inject it or we update it later.
        # For now, just save segments.
        payload = {
            "file": stem,
            "segments": segments
        }
        
        with open(skeleton_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            
        whisper_json.unlink()
        logger.info(f"✅ Skeleton Saved: {skeleton_path.name}")
        return skeleton_path
    else:
        raise Exception("WhisperX did not produce JSON output")

def run(file_path: Path):
    """
    Full Ingest -> Transcribe pipeline.
    """
    video_path, audio_path = ingest(file_path)
    skeleton_path = transcribe(audio_path)
    return skeleton_path
