import os
import time
import shutil
import subprocess
import re
import json
import logging
from pathlib import Path
from datetime import datetime
import config
import omega_db

# Import renderers (assuming they are in root)
# We might need to add root to sys.path if running as module
import sys
sys.path.append(str(config.BASE_DIR))
try:
    from burn_in import burn_subtitles as burn_in_composite
    from subs_render_overlay import render_overlay
except ImportError:
    # Fallback if running from root
    try:
        from burn_in import burn_subtitles as burn_in_composite
        from subs_render_overlay import render_overlay
    except ImportError:
        pass # Will fail later if needed

logger = logging.getLogger("OmegaManager.Publisher")

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
"""

def iso_now():
    return datetime.now().isoformat()

def adjust_ass_time(time_str, delta_ms):
    parts = time_str.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s_parts = parts[2].split('.')
    s = int(s_parts[0])
    cs = int(s_parts[1])
    total_ms = (h * 3600 + m * 60 + s) * 1000 + (cs * 10)
    new_ms = max(0, total_ms + delta_ms)
    
    new_h = new_ms // 3600000
    rem = new_ms % 3600000
    new_m = rem // 60000
    rem = rem % 60000
    new_s = rem // 1000
    new_cs = (rem % 1000) // 10
    return f"{new_h}:{new_m:02d}:{new_s:02d}.{new_cs:02d}"

def publish(video_path: Path, srt_path: Path, subtitle_style: str = "Classic"):
    """
    Burns subtitles into video using the specified style.
    Styles: "Classic" (RuvBox), "Modern" (Default/Shadow).
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")

    stem = video_path.stem.replace("_SUBBED", "") # careful if re-burning
    output_dir = config.DELIVERY_DIR / "VIDEO"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_path = output_dir / f"{stem}_SUBBED.mp4"
    
    logger.info(f"🔥 Burning Subtitles: {stem} (Style: {subtitle_style})")
    
    # Map User Style to ASS Style Name
    # Map User Style to ASS Style Name
    # Map User Style to ASS Style Name
    style_map = config.BURN_METHOD_MAP
    ass_style_name = style_map.get(subtitle_style, "Apple") # Default to Overlay for safety
    
    if ass_style_name == "Apple":
        logger.info("🍎 Using Apple Style (Overlay Engine)")
        
        # 1. Convert SRT to JSON for the Overlay Engine
        # We use the SRT because it is the "Final Truth" (reviewed, finalized).
        temp_json_path = config.VAULT_DATA / f"{stem}_OVERLAY_INPUT.json"
        parse_srt_to_overlay_json(srt_path, temp_json_path)
        
        # 2. Render Overlay (ProRes 4444 MOV)
        overlay_mov_path = config.VAULT_DATA / f"{stem}_OVERLAY.mov"
        
        # Ensure render_overlay is available
        # if 'render_overlay' not in globals():
        #      # Fallback import if not at top level
        #      from subs_render_overlay import render_overlay
             
        render_overlay(
            video_path=str(video_path),
            subs_json_path=str(temp_json_path),
            output_path=str(overlay_mov_path),
            profile_name="AppleTV_IS",
            stem=stem
        )
        
        # 3. Composite Overlay onto Video
        logger.info("   Compositing Overlay...")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(overlay_mov_path),
            "-filter_complex", "[0:v][1:v]overlay=0:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            str(output_path)
        ]
        
    else:
        # Standard ASS Burn-in (Classic / Modern)
        
        # 1. Generate ASS
        ass_path = config.VAULT_DATA / f"{stem}.ass"
        generate_ass_from_srt(srt_path, ass_path, style_name=ass_style_name)
        
        # 2. Burn
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            str(output_path)
        ]
    
    logger.info(f"   Running FFmpeg: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    return output_path

def parse_srt_to_overlay_json(srt_path, json_path):
    """
    Parses SRT and saves as JSON for subs_render_overlay.
    """
    with open(srt_path, 'r', encoding='utf-8') as f:
        srt_content = f.read()
        
    blocks = srt_content.strip().split('\n\n')
    events = []
    
    for block in blocks:
        lines = block.split('\n')
        if len(lines) < 3: continue
        
        time_line = lines[1]
        text_lines = lines[2:]
        
        start_str, end_str = time_line.split(' --> ')
        
        # Convert time to seconds
        def time_to_sec(t_str):
            # 00:00:01,500
            h, m, s = t_str.replace(',', '.').split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
            
        start_sec = time_to_sec(start_str)
        end_sec = time_to_sec(end_str)
        
        events.append({
            "start": start_sec,
            "end": end_sec,
            "lines": [l.strip() for l in text_lines]
        })
        
    data = {
        "video_width": 1920,
        "video_height": 1080,
        "events": events
    }
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def convert_srt_time_to_ass(srt_time):
    # 00:00:01,500 -> 0:00:01.50
    try:
        h, m, s_ms = srt_time.split(':')
        s, ms = s_ms.split(',')
        return f"{int(h)}:{m}:{s}.{ms[:2]}"
    except ValueError:
        return "0:00:00.00"

def generate_ass_from_srt(srt_path, ass_path, style_name="RuvBox"):
    """
    Converts SRT to ASS with the specific style applied to all events.
    """
    with open(srt_path, 'r', encoding='utf-8') as f:
        srt_content = f.read()
        
    # Parse SRT (simple regex or library)
    # We can use a simple parser since our SRTs are clean.
    blocks = srt_content.strip().split('\n\n')
    
    events = []
    for block in blocks:
        lines = block.split('\n')
        if len(lines) < 3: continue
        
        # index = lines[0]
        time_line = lines[1]
        
        start_str, end_str = time_line.split(' --> ')
        start_ass = convert_srt_time_to_ass(start_str)
        end_ass = convert_srt_time_to_ass(end_str)

        # Custom RuvBox Logic: Split lines to control box overlap/spacing
        if style_name == "RuvBox":
            # FORCE CLEARANCE: Subtract 100ms from end time to prevent stacking
            # Use end_ass (H:MM:SS.cs) not end_str (H:MM:SS,mmm) to avoid math errors
            end_ass_adjusted = adjust_ass_time(end_ass, -100)
            
            base_margin_v = 65
            line_height = 68 # Balanced spacing (65 Font + 2 Outline + 1px Overlap)
            
            # Process lines from bottom to top
            for i, line in enumerate(reversed(lines[2:])):
                margin_v = base_margin_v + (i * line_height)
                clean_line = f"\\h\\h{line.strip()}\\h\\h"
                events.append(f"Dialogue: 0,{start_ass},{end_ass_adjusted},{style_name},,0,0,{margin_v},,{clean_line}")
        else:
            # Standard handling
            text = "\\N".join(lines[2:])
            events.append(f"Dialogue: 0,{start_ass},{end_ass},{style_name},,0,0,0,,{text}")
        
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ASS_HEADER + "\n")
        f.write("[Events]\n")
        f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        for event in events:
            f.write(event + "\n")
            
    return ass_path

# Alias for compatibility
srt_to_ass = generate_ass_from_srt

def find_video_file(stem):
    extensions = {'.mp4', '.mov', '.mkv', '.m4v', '.mpg', '.mpeg', '.moc'}
    for ext in extensions:
        path = config.VAULT_VIDEOS / f"{stem}{ext}"
        if path.exists():
            logger.info(f"✅ Found video: {path.name}")
            return path
            
    clean_stem = stem.replace("_RUVBOX", "").replace("_MODERN", "")
    if clean_stem != stem:
        for ext in extensions:
            path = config.VAULT_VIDEOS / f"{clean_stem}{ext}"
            if path.exists():
                logger.info(f"✅ Found video (Fuzzy): {path.name}")
                return path
    return None

def burn(srt_file: Path, forced_style=None):
    """
    Burns subtitles into video.
    Returns output video path.
    """
    stem = srt_file.stem
    logger.info(f"🔥 Processing: {srt_file.name}")
    
    # Fetch job for style info
    job = omega_db.get_job(stem) or {}
    
    video_path = find_video_file(stem)
    if not video_path:
        raise Exception("Video not found in Vault")

    # --- DETERMINE STYLE ---
    # Priority:
    # 1. Forced Style (Function Arg - not used here yet)
    # 2. Filename Tags (_RUVBOX, _MODERN)
    # 3. DB 'subtitle_style' field
    # 4. Config Map based on Show Name
    # 5. Default
    
    chosen_style = None
    
    # 1. Filename Tags
    if "_RUVBOX" in stem:
        chosen_style = "RUV_BOX"
    elif "_MODERN" in stem:
        chosen_style = "OMEGA_MODERN"
        
    # 2. DB Subtitle Style
    if not chosen_style:
        db_style = job.get("subtitle_style")
        if db_style:
            # Map "Classic" -> "RuvBox" using config
            # We need to import config if not already imported or use hardcoded map
            # The legacy file imports config? No, it imports os, time... 
            # Let's check imports. It imports omega_db.
            # I'll add a simple map here to be safe and consistent with config.py
            style_map_internal = {
                "Classic": "RUV_BOX",
                "RuvBox": "RUV_BOX",
                "Modern": "OMEGA_MODERN",
                "OMEGA_MODERN": "OMEGA_MODERN",
                "Apple": "OMEGA_MODERN"
            }
            chosen_style = style_map_internal.get(db_style, "RUV_BOX") # Default to RUV_BOX if unknown
            
    # 3. Show Name Match (Fallback)
    if not chosen_style:
         for key, style in config.STYLE_MAP.items():
            if key != "DEFAULT" and key.lower() in stem.lower().replace("_", " "):
                chosen_style = style
                break
                
    if not chosen_style:
        chosen_style = "RUV_BOX" # Default to RUV_BOX for safety as per user request
            
    print(f"   🎨 Style Selected: {chosen_style}")
    output_file = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"

    if chosen_style == "RUV_BOX":
        logger.info("🎬 Burning RÚV Style (Direct ASS)...")
        temp_ass = config.VIDEO_DIR / f"{stem}_temp.ass"
        srt_to_ass(srt_file, temp_ass, style_name="RuvBox")
        
        ass_path_escaped = str(temp_ass).replace(":", "\\:")
        # Force CPU Encoding (libx264) for stability
        # Hardware encoding (h264_videotoolbox) caused corruption/playback issues.
        logger.info("   🐢 Using CPU Encoding (libx264) for maximum compatibility")
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-vf", f"ass='{ass_path_escaped}'",
            "-c:v", "libx264", "-preset", "faster", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart",
            str(output_file)
        ]
        
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            logger.info(f"✅ Burn Complete: {output_file.name}")
            temp_ass.unlink(missing_ok=True)
            return output_file
        except subprocess.CalledProcessError as e:
            logger.error(f"Burn Failed: {e}")
            raise e

    else:
        # OMEGA MODERN (Overlay)
        logger.info("🎬 Burning Omega Modern Style (Overlay)...")
        
        # 1. Check Normalized JSON
        normalized_json = config.SRT_DIR / f"{stem}_normalized.json"
        if not normalized_json.exists():
             # Try to regenerate? Or fail?
             # For now, fail. Manager should have ensured it.
             raise Exception("Normalized JSON not found")

        # 2. Render Overlay
        import hashlib
        safe_stem = hashlib.md5(stem.encode('utf-8')).hexdigest()
        temp_frames_dir = config.VIDEO_DIR / f"temp_frames_{safe_stem}"
        
        # Cleanup old
        if temp_frames_dir.exists(): shutil.rmtree(temp_frames_dir)
        
        overlay_path = render_overlay(str(video_path), str(normalized_json), str(config.VIDEO_DIR / f"{stem}_overlay.mov"), "AppleTV_IS", stem=stem, skip_encoding=True)
        
        # Move to safe path if needed (render_overlay returns path)
        if overlay_path != temp_frames_dir:
             if temp_frames_dir.exists(): shutil.rmtree(temp_frames_dir)
             shutil.move(str(overlay_path), str(temp_frames_dir))
             overlay_path = temp_frames_dir

        # 3. Composite
        try:
            burn_in_composite(str(video_path), overlay_path, str(output_file), stem=stem)
            
            # Cleanup
            if overlay_path.is_dir(): shutil.rmtree(overlay_path)
            if normalized_json.exists(): normalized_json.unlink()
            
            logger.info(f"✅ Burn Complete: {output_file.name}")
            return output_file
            
        except Exception as e:
            if overlay_path.is_dir(): shutil.rmtree(overlay_path)
            raise e
