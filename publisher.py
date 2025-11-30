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
# --- CONFIGURATION ---
BASE_DIR = Path(os.getcwd())
SRT_INBOX = BASE_DIR / "4_DELIVERY" / "SRT"
VIDEO_VAULT = BASE_DIR / "2_VAULT" / "Videos"
OUTBOX = BASE_DIR / "4_DELIVERY" / "VIDEO"
OUTBOX.mkdir(parents=True, exist_ok=True)
ERROR_DIR = BASE_DIR / "99_ERRORS"
ERROR_DIR.mkdir(exist_ok=True)

# --- STYLE MAP (The Brain) ---
# Maps show names (partial match) to specific styles.
# DEFAULT is "OMEGA_MODERN" (Apple TV+ style).
STYLE_MAP = {
    "Joyce Meyer": "RUV_BOX",
    "Praise": "OMEGA_MODERN", 
    "News": "RUV_BOX",
    "CBN": "RUV_BOX",
    "700": "RUV_BOX",
    "DEFAULT": "OMEGA_MODERN"
}

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

# === OMEGA TV ICELAND BROADCAST SUBTITLES ===

# 1. OMEGA MODERN (Apple TV+ Style) - Used via render_overlay (PNG)
# 2. RUV BOX (Classic Broadcast) - Used via libass (Text)

# RUV BOX STYLE DEFINITION:
# Font: SF Pro Display Regular
# Size: 52 (Slightly larger for readability)
# Color: White (&H00FFFFFF)
# BackColor: Black 80% Opacity (&H33000000) -> 33 hex is ~20% transparency (80% opacity)
# BorderStyle: 3 (Opaque Box)
# Outline: 25 (Padding/Breathing Room)
# Shadow: 0

ASS_HEADER = """[Script Info]
Title: Omega TV Iceland Broadcast Subtitles
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H8A000000,&H8A000000,-1,0,0,0,100,100,0,0,4,28,0,2,20,20,50,1
Style: RuvBox,SF Pro Display,65,&H00FFFFFF,&H000000FF,&H33000000,&H33000000,0,0,0,0,100,100,0,0,3,2,0,2,50,50,65,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def adjust_ass_time(time_str, delta_ms):
    # time_str format: H:MM:SS.cs (e.g. 0:00:04.90)
    parts = time_str.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s_parts = parts[2].split('.')
    s = int(s_parts[0])
    cs = int(s_parts[1]) # centiseconds
    
    total_ms = (h * 3600 + m * 60 + s) * 1000 + (cs * 10)
    new_ms = total_ms + delta_ms
    
    if new_ms < 0: new_ms = 0
    
    # Convert back
    new_h = new_ms // 3600000
    rem = new_ms % 3600000
    new_m = rem // 60000
    rem = rem % 60000
    new_s = rem // 1000
    new_cs = (rem % 1000) // 10
    
    return f"{new_h}:{new_m:02d}:{new_s:02d}.{new_cs:02d}"

def srt_time_to_ass(srt_time):
    parts = srt_time.replace(',', '.').split('.')
    return f"{parts[0]}.{parts[1][:2]}"

def srt_to_ass(srt_path, ass_path, style_name="Default"):
    print(f"   Converting {srt_path.name} → ASS ({style_name})")
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read().strip()

    events = []
    for block in re.split(r'\n\n+', content):
        lines = block.strip().split('\n')
        if len(lines) < 3: continue
        if '-->' not in lines[1]: continue

        timing = lines[1]
        text_lines = lines[2:]
        
        start = timing.split('-->')[0].strip().replace(',', '.')
        end = timing.split('-->')[1].strip().replace(',', '.')
        
        # Convert to ASS format (H:MM:SS.cs)
        start = srt_time_to_ass(start)
        end = srt_time_to_ass(end)
        
        # Split multi-line subtitles into separate events for RuvBox
        # This prevents box overlap by manually spacing them
        if style_name == "RuvBox":
            # FORCE CLEARANCE: Subtract 100ms from end time to prevent stacking
            end = adjust_ass_time(end, -100)
            
            base_margin_v = 65
            line_height = 68 # Balanced spacing (65 Font + 2 Outline + 1px Overlap)
            
            # Process lines from bottom to top
            for i, line in enumerate(reversed(text_lines)):
                margin_v = base_margin_v + (i * line_height)
                # Removed \fscy trick, standard text with padding
                clean_line = f"\\h\\h{line.strip()}\\h\\h"
                events.append(f"Dialogue: 0,{start},{end},{style_name},,0,0,{margin_v},,{clean_line}")
        else:
            # Standard handling for other styles
            text = '\\N'.join(text_lines)
            events.append(f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{text}")

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ASS_HEADER + '\n'.join(events))

def find_video_file(stem):
    """
    Finds the source video in the Vault.
    Supports common extensions.
    """
    extensions = {'.mp4', '.mov', '.mkv', '.m4v', '.mpg', '.mpeg', '.moc'}
    
    # 1. Direct Match in Vault
    for ext in extensions:
        path = VIDEO_VAULT / f"{stem}{ext}"
        if path.exists():
            print(f"   ✅ Found video in Vault: {path.name}")
            return path
            
    # 2. Fuzzy Match (if stem has extra tags)
    # Sometimes stem is "MyVideo_RUVBOX", but video is "MyVideo.mp4"
    # We try to strip known suffixes
    clean_stem = stem.replace("_RUVBOX", "").replace("_MODERN", "")
    if clean_stem != stem:
        for ext in extensions:
            path = VIDEO_VAULT / f"{clean_stem}{ext}"
            if path.exists():
                print(f"   ✅ Found video in Vault (Fuzzy): {path.name}")
                return path

    print(f"   ❌ Video NOT found in Vault for: {stem}")
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

    # --- DETERMINE STYLE ---
    # Check STYLE_MAP for matches
    chosen_style = STYLE_MAP["DEFAULT"]
    
    # 1. Check Filename Tags (Highest Priority)
    if "_RUVBOX" in stem:
        chosen_style = "RUV_BOX"
    elif "_MODERN" in stem:
        chosen_style = "OMEGA_MODERN"
    else:
        # 2. Check Show Database (Fallback)
        for key, style in STYLE_MAP.items():
            if key != "DEFAULT" and key.lower() in stem.lower().replace("_", " "):
                chosen_style = style
                break
            
    print(f"   🎨 Style Selected: {chosen_style}")

    # --- RUV BOX WORKFLOW (Direct ASS Burn) ---
    if chosen_style == "RUV_BOX":
        output_file = OUTBOX / f"{stem}_SUBBED.mp4"
        if output_file.exists():
            print(f"   ✅ Job Complete: {stem}")
            done_srt = srt_file.parent / f"DONE_{srt_file.name}"
            shutil.move(str(srt_file), str(done_srt))
            omega_db.update(stem, stage="PUBLISHED", status="Completed", progress=100.0)
            return

        print(f"   🎬 Burning RÚV Style (SF Pro Box) with h264_videotoolbox (M1 Pro)...")
        
        # Create ASS file
        temp_ass = OUTBOX / f"{stem}_temp.ass"
        srt_to_ass(srt_file, temp_ass, style_name="RuvBox")
        
        ass_path_escaped = str(temp_ass).replace(":", "\\:")
        
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-vf", f"ass='{ass_path_escaped}'",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_file)
        ]

        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            print(f"   ✅ PERFECT: {output_file.name}")
            temp_ass.unlink(missing_ok=True)
            
            # Cleanup SRT
            done_srt = srt_file.parent / f"DONE_{srt_file.name}"
            shutil.move(str(srt_file), str(done_srt))
            
            meta = finalize_meta(meta)
            omega_db.update(stem, stage="COMPLETED", status="Ready for Broadcast", progress=100.0, meta=meta)
            return
            
        except subprocess.CalledProcessError as e:
            print(f"   💥 Error burning RÚV style: {e}")
            omega_db.update(stem, status=f"Error: Burn Failed {e}", progress=0)
            raise e

    # --- OMEGA MODERN WORKFLOW (Overlay PNG) ---
    # (Falls through to existing logic below)

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
                if normalized_json.exists():
                    print(f"   ✅ Regenerated Normalized JSON: {normalized_json.name}")
                elif alt_normalized.exists():
                    normalized_json = alt_normalized
            except Exception as e:
                print(f"   ❌ Failed to regenerate normalized JSON: {e}")
    
    if not normalized_json.exists():
         print(f"   ❌ Normalized JSON not found. Cannot render overlay.")
         omega_db.update(stem, status="Error: Missing JSON", progress=0)
         raise Exception("Normalized JSON not found")

    # 2. Render Overlay (Direct PNG Frames)
    # We skip the ProRes encoding step to save time/space
    
    # Use a safe directory name to avoid FFmpeg issues with special characters
    import hashlib
    safe_stem = hashlib.md5(stem.encode('utf-8')).hexdigest()
    temp_frames_dir = OUTBOX / f"temp_frames_{safe_stem}"
    
    # Check if we already have the frames (we might need to rename the old one if it exists)
    # Actually, since the old one failed, let's just start fresh with the safe name.
    # But wait, the user doesn't want to re-render.
    # I should try to find the old directory and rename it if it exists.
    
    old_unsafe_dir = OUTBOX / f"temp_frames_{stem}"
    if old_unsafe_dir.exists() and not temp_frames_dir.exists():
        print(f"   🚚 Renaming unsafe temp dir to safe path...")
        shutil.move(str(old_unsafe_dir), str(temp_frames_dir))

    if temp_frames_dir.exists() and any(temp_frames_dir.iterdir()):
        print(f"   ♻️ Found existing temp frames. Reusing: {temp_frames_dir}")
        overlay_path = temp_frames_dir
    else:
        # We need to pass the SAFE path to render_overlay
        # But render_overlay currently takes output_path as a file path (MOV) or dir?
        # If skip_encoding is True, it returns the temp dir.
        # We need to modify render_overlay call to use this specific dir?
        # render_overlay creates its own temp dir. We need to tell it where to put it?
        # No, render_overlay returns a Path.
        # Wait, my previous edit to publisher.py passed `OUTBOX / f"{stem}_overlay.mov"`
        # and render_overlay (with my edit) copies the temp dir to `output_path.parent / f"temp_frames_{stem}"`.
        # I need to change how render_overlay determines the persistent dir.
        
        # Let's pass the SAFE directory path directly to render_overlay if possible?
        # No, render_overlay signature is (video_path, json, output_path, ...)
        # And it derives the persistent dir from output_path and stem.
        
        # I will modify render_overlay to accept an explicit 'persistent_dir' or just handle the move here.
        # Actually, simpler: Let render_overlay do its thing, it returns a path.
        # If that path has special chars, we move it HERE to a safe path.
        
        overlay_path = render_overlay(str(video_path), str(normalized_json), str(OUTBOX / f"{stem}_overlay.mov"), "AppleTV_IS", stem=stem, skip_encoding=True)
        
        if overlay_path and overlay_path.exists():
             # Move to safe path immediately
             if overlay_path != temp_frames_dir:
                 if temp_frames_dir.exists(): shutil.rmtree(temp_frames_dir)
                 shutil.move(str(overlay_path), str(temp_frames_dir))
                 overlay_path = temp_frames_dir

    if not overlay_path:
        print(f"   ❌ Overlay render failed.")
        omega_db.update(stem, status="Error: Overlay Failed", progress=0)
        raise Exception("Overlay render failed")

    # 3. Composite (Burn-in)
    print(f"   🎛️ Compositing final video...")
    
    try:
        from burn_in import burn_subtitles
        burn_subtitles(str(video_path), overlay_path, str(output_file), stem=stem)
        
        # Cleanup
        if overlay_path.is_dir():
             shutil.rmtree(overlay_path)
        elif overlay_path.exists():
             os.remove(overlay_path)
             
        if normalized_json.exists(): os.remove(normalized_json)
        
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
        print(f"   ❌ Compositing failed: {e}")
        omega_db.update(stem, status=f"Error: Compositing Failed {e}", progress=0)
        
        # Cleanup on failure too
        if overlay_path.is_dir():
             shutil.rmtree(overlay_path)
        elif overlay_path.exists():
             os.remove(overlay_path)
             
        raise e
    except Exception as e:
        print(f"   ⚠️ Overlay pipeline failed, falling back to ASS burn: {e}")

    print(f"   🎬 Burning RÚV Style (SF Pro Box) with h264_videotoolbox (M1 Pro)...")
    
    ass_path_escaped = str(temp_ass).replace(":", "\\:")

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(video_path),
        "-vf", f"ass='{ass_path_escaped}'",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
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
            if srt.name.startswith("DONE_"): continue
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
    # with ProcessLock("publisher"):
    main()
