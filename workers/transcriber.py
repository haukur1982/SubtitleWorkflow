import os
import re
import shutil
import subprocess
import json
import logging
import time
import select
from collections import deque
from pathlib import Path
from datetime import datetime
import config
import omega_db

logger = logging.getLogger("OmegaManager.Transcriber")

SAFETY_MARKERS = {"(music)", "[music]", "(song)", "[song]", "(singing)", "[singing]", "(choir)", "[choir]", "♪"}

def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default

def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default

def _is_music_marker_text(text: str) -> bool:
    if not text:
        return False
    lowered = text.strip().lower()
    if lowered in SAFETY_MARKERS:
        return True
    if "♪" in text:
        return True
    return False

def _merge_intervals(intervals):
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return merged

def _coverage_within(segments, window_seconds):
    intervals = []
    for seg in segments:
        start = max(0.0, float(seg.get("start", 0.0)))
        end = min(float(seg.get("end", 0.0)), window_seconds)
        if end <= 0 or start >= window_seconds:
            continue
        if end <= start:
            continue
        intervals.append((start, end))
    merged = _merge_intervals(intervals)
    total = sum(end - start for start, end in merged)
    return total, merged

def _should_run_safety_pass(segments, window_seconds, *, force: bool, gap_threshold: float, coverage_threshold: float, first_gap_threshold: float) -> tuple[bool, dict]:
    stats = {
        "window_seconds": window_seconds,
        "first_start": None,
        "max_gap": None,
        "coverage": None,
    }
    if force:
        return True, stats
    if not segments:
        return True, stats
    window = [seg for seg in segments if float(seg.get("start", 0.0)) < window_seconds]
    if not window:
        return True, stats
    window.sort(key=lambda s: float(s.get("start", 0.0)))
    first_start = float(window[0].get("start", 0.0))
    stats["first_start"] = first_start
    if first_start >= first_gap_threshold:
        return True, stats
    max_gap = max(0.0, first_start)
    prev_end = float(window[0].get("end", 0.0))
    for seg in window[1:]:
        start = float(seg.get("start", 0.0))
        gap = start - prev_end
        if gap > max_gap:
            max_gap = gap
        prev_end = max(prev_end, float(seg.get("end", 0.0)))
    stats["max_gap"] = max_gap
    covered, _ = _coverage_within(window, window_seconds)
    coverage = covered / window_seconds if window_seconds > 0 else 1.0
    stats["coverage"] = round(coverage, 3)
    if max_gap >= gap_threshold:
        return True, stats
    if coverage <= coverage_threshold:
        return True, stats
    return False, stats

def _merge_safety_segments(primary, safety, window_seconds, *, overlap_pad: float = 0.25):
    if not safety:
        return primary, 0
    primary_sorted = sorted(primary, key=lambda s: (float(s.get("start", 0.0)), float(s.get("end", 0.0))))
    added = []
    for seg in safety:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        if start >= window_seconds:
            continue
        if end <= 0 or end <= start:
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if _is_music_marker_text(text):
            continue
        overlaps = False
        for p in primary_sorted:
            p_start = float(p.get("start", 0.0))
            if p_start > window_seconds:
                break
            p_end = float(p.get("end", 0.0))
            if start <= p_end + overlap_pad and end >= p_start - overlap_pad:
                overlaps = True
                break
        if not overlaps:
            added.append({"start": start, "end": end, "text": text})
    if not added:
        return primary, 0
    combined = primary_sorted + added
    combined.sort(key=lambda s: (float(s.get("start", 0.0)), float(s.get("end", 0.0))))
    for idx, seg in enumerate(combined, start=1):
        seg["id"] = idx
    return combined, len(added)

def _audio_duration_seconds(audio_path: Path) -> float:
    cmd = [
        str(config.FFPROBE_BIN),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        value = (result.stdout or "").strip()
        return float(value)
    except Exception as exc:
        logger.warning("Could not read audio duration for %s: %s", audio_path.name, exc)
        return 0.0

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
    
    progress_re = re.compile(r"Progress:\s*([0-9]+(?:\.[0-9]+)?)%")
    transcript_re = re.compile(r"Transcript:\s*\\[(\\d+(?:\\.\\d+)?)\\s*-->\\s*(\\d+(?:\\.\\d+)?)\\]")
    phase = "asr"
    last_progress = 0.0
    last_update = 0.0
    last_lines = deque(maxlen=40)
    last_time_sec = 0.0
    total_seconds = _audio_duration_seconds(audio_path)
    total_minutes = (total_seconds / 60.0) if total_seconds else 0.0

    def update_progress(pct: float, current_phase: str):
        nonlocal last_progress, last_update
        if current_phase == "align":
            overall = 20.0 + (pct / 100.0) * 10.0
            status = f"Aligning ({pct:.0f}%)"
        else:
            overall = 10.0 + (pct / 100.0) * 10.0
            status = f"Transcribing ({pct:.0f}%)"

        if total_minutes:
            done_minutes = min(last_time_sec, total_seconds) / 60.0
            status = f"{status} [{done_minutes:.1f}/{total_minutes:.1f} min]"

        now = time.time()
        if overall <= last_progress + 0.2 and (now - last_update) < 2.0:
            return

        last_progress = max(last_progress, overall)
        last_update = now
        omega_db.update(stem, status=status, progress=round(last_progress, 2))

    try:
        logger.info("Starting WhisperX subprocess...")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if not proc.stdout:
            raise RuntimeError("WhisperX did not return a stdout pipe")

        idle_timeout = _safe_float_env("OMEGA_ASR_IDLE_TIMEOUT", 900.0)
        start_time = time.time()
        last_output = start_time

        while True:
            if proc.poll() is not None:
                break

            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline()
                if line == "" and proc.poll() is not None:
                    break
                stripped = line.strip()
                if stripped:
                    last_output = time.time()
                    last_lines.append(stripped)

                if "Performing alignment" in stripped:
                    phase = "align"
                elif "Performing transcription" in stripped:
                    phase = "asr"

                match = progress_re.search(stripped)
                if match:
                    try:
                        pct = float(match.group(1))
                    except ValueError:
                        continue
                    update_progress(pct, phase)
                    continue

                match = transcript_re.search(stripped)
                if match:
                    try:
                        seg_end = float(match.group(2))
                    except ValueError:
                        continue
                    if seg_end > last_time_sec:
                        last_time_sec = seg_end
            else:
                now = time.time()
                if idle_timeout and (now - last_output) > idle_timeout:
                    logger.error("WhisperX stalled: no output for %.0f seconds", now - last_output)
                    try:
                        proc.terminate()
                        proc.wait(timeout=10)
                    except Exception:
                        proc.kill()
                    raise RuntimeError(f"WhisperX stalled (idle > {idle_timeout:.0f}s)")

        proc.wait()
        if proc.returncode != 0:
            tail = "\n".join(last_lines)
            raise RuntimeError(f"WhisperX failed (code {proc.returncode}). Last output:\\n{tail}")

        logger.info("WhisperX subprocess finished.")
    except Exception as e:
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

        safety_enabled = str(os.environ.get("OMEGA_ASR_SAFETY_PASS", "1")).strip().lower() in {"1", "true", "yes", "on"}
        safety_force = str(os.environ.get("OMEGA_ASR_SAFETY_FORCE", "1")).strip().lower() in {"1", "true", "yes", "on"}
        safety_seconds = _safe_float_env("OMEGA_ASR_SAFETY_SECONDS", 75.0)
        safety_gap = _safe_float_env("OMEGA_ASR_SAFETY_GAP", 6.0)
        safety_coverage = _safe_float_env("OMEGA_ASR_SAFETY_COVERAGE", 0.35)
        safety_first_gap = _safe_float_env("OMEGA_ASR_SAFETY_FIRST_GAP", 2.5)
        safety_onset = _safe_float_env("OMEGA_ASR_SAFETY_VAD_ONSET", 0.35)
        safety_offset = _safe_float_env("OMEGA_ASR_SAFETY_VAD_OFFSET", 0.12)
        safety_chunk = _safe_int_env("OMEGA_ASR_SAFETY_CHUNK_SIZE", 20)

        window_seconds = min(safety_seconds, total_seconds or safety_seconds)
        if safety_enabled and window_seconds >= 10.0:
            should_run, stats = _should_run_safety_pass(
                segments,
                window_seconds,
                force=safety_force,
                gap_threshold=safety_gap,
                coverage_threshold=safety_coverage,
                first_gap_threshold=safety_first_gap,
            )
        else:
            should_run, stats = False, {}

        added_segments = 0
        if should_run:
            omega_db.update(stem, status="Safety pass: rechecking opening")
            safety_audio = audio_path.with_name(f"{stem}__safety.wav")
            try:
                cmd = [
                    config.FFMPEG_BIN, "-y",
                    "-i", str(audio_path),
                    "-t", str(window_seconds),
                    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    str(safety_audio),
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

                safety_cmd = [
                    str(config.WHISPER_BIN),
                    str(safety_audio),
                    "--model", config.WHISPER_MODEL,
                    "--language", "en",
                    "--output_dir", str(output_dir),
                    "--output_format", "json",
                    "--compute_type", "float32",
                    "--batch_size", "1",
                    "--device", config.WHISPER_DEVICE,
                    "--vad_onset", str(safety_onset),
                    "--vad_offset", str(safety_offset),
                    "--chunk_size", str(safety_chunk),
                    "--print_progress", "False",
                ]
                subprocess.run(safety_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

                safety_json = output_dir / f"{stem}__safety.json"
                if safety_json.exists():
                    with open(safety_json, "r") as f:
                        safety_data = json.load(f)
                    safety_segments = []
                    for seg in safety_data.get("segments", []):
                        safety_segments.append({
                            "start": seg.get("start"),
                            "end": seg.get("end"),
                            "text": seg.get("text", "").strip(),
                        })
                    segments, added_segments = _merge_safety_segments(
                        segments,
                        safety_segments,
                        window_seconds,
                    )
                    safety_json.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Safety pass failed: %s", exc)
            finally:
                try:
                    safety_audio.unlink(missing_ok=True)
                except Exception:
                    pass

        omega_db.update(
            stem,
            meta={
                "asr_safety": {
                    "enabled": safety_enabled,
                    "forced": safety_force,
                    "window_seconds": window_seconds,
                    "added_segments": added_segments,
                    **stats,
                }
            },
        )
            
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
