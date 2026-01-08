import os
import time
import shutil
import subprocess
import re
import sys
from pathlib import Path
from datetime import datetime
import omega_db
from lock_manager import ProcessLock
import system_health

def get_duration(file_path):
    """Returns duration in seconds using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return float(res.stdout.strip()) if res.stdout else 0
    except:
        return 0

# --- CONFIGURATION ---
BASE_DIR = Path(os.getcwd())
SRT_INBOX = BASE_DIR / "4_FINAL_OUTPUT"
# Video source paths (v2.0 - VIP/Auto workflow)
VIDEO_SOURCE_PRIMARY = BASE_DIR / "2_READY_FOR_CLOUD" / "processed"
VIDEO_SOURCE_SECONDARY = BASE_DIR / "1_INBOX"
VIDEO_SOURCE_AUTO = BASE_DIR / "1_INBOX" / "AUTO_PILOT" / "processed"
VIDEO_SOURCE_VIP = BASE_DIR / "1_INBOX" / "VIP_REVIEW" / "processed"
OUTBOX = BASE_DIR / "5_DELIVERABLES"

OUTBOX.mkdir(exist_ok=True)
ERROR_DIR = BASE_DIR / "99_ERRORS"
ERROR_DIR.mkdir(exist_ok=True)

# --- BINARY HUNTER (Finds the 'HEAD' version of FFmpeg) ---
def get_ffmpeg_binary():
    candidates = [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "ffmpeg"
    ]
    for path in candidates:
        if shutil.which(path):
            return path
    return "ffmpeg"

FFMPEG_BIN = get_ffmpeg_binary()
print(f"Using FFmpeg: {FFMPEG_BIN}")

from burn_in import burn_subtitles as burn_in_composite
from finalize import json_to_srt
from subs_render_overlay import render_overlay

def iso_now():
    return datetime.now().isoformat()

def seconds_between(start_ts, end_ts):
    """Return seconds between two ISO timestamps (or None on parse error)."""
    if not start_ts or not end_ts:
        return None
    try:
        start = datetime.fromisoformat(start_ts)
        end = datetime.fromisoformat(end_ts)
        return (end - start).total_seconds()
    except Exception:
        return None

def finalize_meta(meta, end_time=None):
    """Add end time and durations to meta dict."""
    meta = dict(meta or {})
    end_ts = end_time or iso_now()
    meta["burn_end_time"] = end_ts
    meta["duration_burn_seconds"] = seconds_between(meta.get("burn_start_time"), end_ts)
    meta["duration_ingest_to_burn_seconds"] = seconds_between(meta.get("ingest_time"), meta.get("burn_start_time"))
    meta["duration_ingest_to_complete_seconds"] = seconds_between(meta.get("ingest_time"), end_ts)
    return meta

# === OMEGA TV ICELAND BROADCAST SUBTITLES (Unified Box, Translucent) ===
ASS_HEADER = """[Script Info]
Title: Omega TV Iceland Broadcast Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H8A000000,&H8A000000,-1,0,0,0,100,100,0,0,4,28,0,2,20,20,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def srt_time_to_ass(srt_time):
    parts = srt_time.replace(',', '.').split('.')
    return f"{parts[0]}.{parts[1][:2]}"

def srt_to_ass(srt_path, ass_path):
    print(f"   Converting {srt_path.name} → perfect Apple TV+ style")
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read().strip()

    events = []
    for block in re.split(r'\n\n+', content):
        lines = block.strip().split('\n')
        if len(lines) < 3: continue
        if '-->' not in lines[1]: continue

        timing = lines[1]
        text_lines = lines[2:]
        text = '\\N'.join(text_lines)
        
        start = timing.split('-->')[0].strip().replace(',', '.')
        end = timing.split('-->')[1].strip().replace(',', '.')
        
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ASS_HEADER + '\n'.join(events))

def find_video_file(stem):
    # Support common containers plus .mpg from broadcast ingest
    for ext in ['.mp4', '.mov', '.mkv', '.m4v', '.mpg']:
        for source in [VIDEO_SOURCE_AUTO, VIDEO_SOURCE_VIP, VIDEO_SOURCE_PRIMARY, VIDEO_SOURCE_SECONDARY]:
            for prefix in ["", "DONE_"]:
                path = source / f"{prefix}{stem}{ext}"
                if path.exists(): return path
    return None

def burn_subtitles(srt_file):
    stem = srt_file.stem
    print(f"🔥 Processing: {srt_file.name}")
    job = omega_db.get_job(stem) or {}
    meta = dict(job.get("meta") or {})

    try:
        stat = srt_file.stat()
        ingest_time = meta.get("ingest_time") or datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime)).isoformat()
    except Exception:
        ingest_time = meta.get("ingest_time") or iso_now()
    meta["ingest_time"] = ingest_time
    meta["burn_start_time"] = iso_now()

    omega_db.update(stem, stage="BURNING", status="Initializing Burn", progress=90.0, meta=meta)
    
    video_path = find_video_file(stem)
    if not video_path:
        print(f"   ❌ Video not found")
        omega_db.update(stem, status="Error: Video Not Found", progress=0)
        raise Exception("Video not found")

    output_file = OUTBOX / f"{stem}_SUBBED.mp4"
    if output_file.exists():
        # 4. Finalize
        print(f"   ✅ Job Complete: {stem}")
        
        # Move SRT to Archive/Done to prevent re-processing
        # We'll just rename it to DONE_ for now, or let Archivist handle it?
        # Better to rename it so we don't pick it up again in the next loop 1ms later.
        done_srt = srt_file.parent / f"DONE_{srt_file.name}"
        shutil.move(str(srt_file), str(done_srt))
        
        omega_db.update(stem, stage="PUBLISHED", status="Completed", progress=100.0)
        return

    # 1. Normalize JSON (Ensure it exists)
    normalized_json = srt_file.with_name(f"{stem}_normalized.json")
    alt_normalized = OUTBOX / f"{stem}_normalized.json"
    
    if not normalized_json.exists() and alt_normalized.exists():
        normalized_json = alt_normalized
        
    if not normalized_json.exists():
        # Fallback: Create from Translated JSON if missing
        print(f"   ⚠️ Normalized JSON missing. Attempting to regenerate...")
        
        source_json = BASE_DIR / "3_TRANSLATED_DONE" / f"{stem}_ICELANDIC.json"
        approved_json = BASE_DIR / "3_TRANSLATED_DONE" / f"{stem}_APPROVED.json"
        
        target_source = approved_json if approved_json.exists() else source_json
        
        if target_source.exists():
            try:
                from finalize import json_to_srt
                json_to_srt(target_source)
                # Re-check
                if alt_normalized.exists():
                    normalized_json = alt_normalized
            except Exception as e:
                print(f"   ❌ Failed to regenerate normalized JSON: {e}")
    
    if not normalized_json.exists():
         print(f"   ❌ Normalized JSON not found. Cannot render overlay.")
         omega_db.update(stem, status="Error: Missing JSON", progress=0)
         raise Exception("Normalized JSON not found")

    # 2. Render Overlay (Transparent MOV)
    overlay_path = OUTBOX / f"{stem}_overlay.mov"
    
    # Check if overlay already exists and is valid
    should_render = True
    if overlay_path.exists():
        print(f"   ⚠️ Overlay file found. Verifying integrity...")
        overlay_dur = get_duration(overlay_path)
        video_dur = get_duration(video_path)
        if abs(overlay_dur - video_dur) < 5.0:
            print(f"   ✅ Overlay matches video duration ({overlay_dur}s). Skipping render.")
            should_render = False
        else:
            print(f"   ❌ Overlay duration mismatch ({overlay_dur}s vs {video_dur}s). Re-rendering.")
    
    if should_render:
        print(f"   🖼️ Rendering overlay (ProRes 4444)...")
        try:
            from subs_render_overlay import render_overlay
            render_overlay(str(video_path), str(normalized_json), str(overlay_path), "AppleTV_IS", stem=stem)
        except Exception as e:
            print(f"   ❌ Overlay render failed: {e}")
            omega_db.update(stem, status=f"Error: Overlay Failed {e}", progress=0)
            raise Exception(f"Overlay render failed: {e}")

    # 3. Composite (Burn-in)
    print(f"   🎛️ Compositing final video...")
    
    try:
        from burn_in import burn_subtitles
        burn_subtitles(str(video_path), str(overlay_path), str(output_file), stem=stem)
        
        # Cleanup
        if overlay_path.exists(): os.remove(overlay_path)
        if normalized_json.exists(): os.remove(normalized_json)
        
        meta = finalize_meta(meta)
        omega_db.update(stem, stage="COMPLETED", status="Ready for Broadcast", progress=100.0, meta=meta)
        
    except Exception as e:
        print(f"   ❌ Compositing failed: {e}")
        omega_db.update(stem, status=f"Error: Compositing Failed {e}", progress=0)
        render_overlay(str(video_path), str(normalized_json), str(overlay_path), "AppleTV_IS")

        print(f"   🎛️ Compositing...")
        burn_in_composite(str(video_path), str(overlay_path), str(output_file))

        temp_ass.unlink(missing_ok=True)
        overlay_path.unlink(missing_ok=True)
        normalized_json.unlink(missing_ok=True)
        meta = finalize_meta(meta)
        omega_db.update(stem, stage="COMPLETED", status="Ready for Broadcast", progress=100.0, meta=meta)
        print(f"   ✅ PERFECT: {output_file.name}")
        
        # --- VERIFICATION ---
        vid_dur = get_duration(video_path)
        out_dur = get_duration(output_file)
        if abs(vid_dur - out_dur) > 5.0:
             print(f"   ❌ VERIFICATION FAILED: Output duration mismatch ({out_dur}s vs {vid_dur}s)")
             omega_db.update(stem, status="Error: Burn Verification Failed", progress=0)
             os.remove(output_file)
             return

        return
    except Exception as e:
        print(f"   ⚠️ Overlay pipeline failed, falling back to ASS burn: {e}")

    print(f"   🎬 Burning final Apple TV+ style with libx264...")
    
    ass_path_escaped = str(temp_ass).replace(":", "\\:")

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(video_path),
        "-vf", f"ass='{ass_path_escaped}'",
        "-c:v", "libx264",
        "-crf", "19",
        "-preset", "slow",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_file)
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"   ✅ PERFECT: {output_file.name}")
        temp_ass.unlink(missing_ok=True)
    except subprocess.CalledProcessError as e:
        print(f"   💥 Error – ASS kept for debugging: {temp_ass}")

def main():
    print("🖨️  Apple TV+ Perfect Burn Publisher – Final Version (Nov 2025)")
    while True:
        system_health.update_heartbeat("publisher")
        for srt in SRT_INBOX.glob("*.srt"):
            # --- HEALTH CHECK ---
            if not system_health.check_disk_space(min_gb=10) or not system_health.check_memory(min_mb=500):
                print("   ⚠️ System resources low. Pausing Publisher for 60s...")
                time.sleep(60)
                continue
                
            try:
                burn_subtitles(srt)
            except Exception as e:
                print(f"   ❌ CRITICAL FAILURE: {e}")
                shutil.move(str(srt), str(ERROR_DIR / srt.name))
        time.sleep(5)


if __name__ == "__main__":
    with ProcessLock("publisher"):
        main()
