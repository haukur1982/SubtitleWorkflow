import argparse
import subprocess
import sys
from pathlib import Path


import omega_db
import config

def burn_subtitles(master_path: str, overlay_path: str, output_path: str, stem: str = None) -> None:
    ffmpeg_bin = config.FFMPEG_BIN
    ffprobe_bin = config.FFPROBE_BIN
    master = Path(master_path)
    overlay = Path(overlay_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    
    if stem:
        omega_db.update(stem, status="Compositing Final Video", progress=91.0)

    # Get duration and framerate
    total_duration = 1
    framerate = "25" # Default fallback
    try:
        # Probe Duration
        duration_cmd = [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(master)]
        duration_res = subprocess.run(duration_cmd, capture_output=True, text=True)
        total_duration = float(duration_res.stdout.strip()) if duration_res.stdout else 1
        
        # Probe Framerate
        fps_cmd = [ffprobe_bin, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(master)]
        fps_res = subprocess.run(fps_cmd, capture_output=True, text=True)
        if fps_res.stdout:
            framerate = fps_res.stdout.strip()
    except: pass

    # Check if overlay is a directory (Image Sequence) or file (MOV)
    if overlay.is_dir():
        # Image Sequence Input via Concat Demuxer (Robust against special chars)
        # 1. Generate file list
        list_path = overlay / "input_list.txt"
        png_files = sorted(overlay.glob("*.png"))
        
        if not png_files:
            raise Exception(f"No PNG files found in {overlay}")
            
        with open(list_path, "w", encoding="utf-8") as f:
            for png in png_files:
                # Escape single quotes for concat demuxer
                safe_path = str(png).replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
                f.write(f"duration {1/float(eval(framerate) if '/' in framerate else framerate)}\n")
        
        # Note: The last image needs to be repeated or handled, but usually duration is enough.
        # Actually, for image sequence in concat, we just list them. 
        # But standard image2 is better if we can just point to the file.
        # Let's try standard concat with just filenames if we are in the dir? No, absolute paths are safer.
        
        # Simpler Concat format for image sequence:
        # file 'path'
        # duration 0.04
        # ...
        
        # REVISION: The simplest way is to just use the glob but change directory to the target first?
        # No, subprocess cwd doesn't change ffmpeg's internal resolving.
        
        # Let's go with the concat list.
        # We need to calculate frame duration.
        try:
            num = float(eval(framerate) if '/' in framerate else framerate)
            frame_dur = 1.0 / num
        except:
            frame_dur = 0.04 # 25fps fallback

        with open(list_path, "w", encoding="utf-8") as f:
            for png in png_files:
                safe_path = str(png).replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
                f.write(f"duration {frame_dur:.6f}\n")
            # Repeat last frame to prevent cut-off
            f.write(f"file '{safe_path}'\n")
        
        overlay_input = ["-f", "concat", "-safe", "0", "-i", str(list_path)]
        # Use premultiplied_alpha=1 for straight alpha PNGs to avoid halos
        # UPDATE: User's FFmpeg does not support premultiplied_alpha option (Exit code 8). Removing it.
        filter_complex = "[0:v]eq=gamma=1.1[vid];[vid][1:v]overlay=0:0:shortest=1:format=auto,unsharp=5:5:0.8:3:3:0.4[v]"
    else:
        # Standard Video Input
        overlay_input = ["-i", str(overlay)]
        filter_complex = "[0:v]eq=gamma=1.1[vid];[vid][1:v]overlay=0:0:shortest=1,unsharp=5:5:0.8:3:3:0.4[v]"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(master),
        *overlay_input,
        "-filter_complex",
        filter_complex,
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",           # Software Encoding (More Stable)
        "-crf", "18",                # High Quality
        "-preset", "fast",           # Reasonable speed
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
        "-progress", "pipe:1"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
        
        while True:
            line = process.stdout.readline()
            if not line: break
            if "out_time_us=" in line and stem:
                try:
                    us = int(line.split("=")[1])
                    current_sec = us / 1000000
                    # Map 91% -> 99%
                    prog = 91.0 + (current_sec / total_duration) * 8.0
                    omega_db.update(stem, progress=prog)
                except: pass
        
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
            
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg failed: {exc}")
        if stem: omega_db.update(stem, status="Error: Compositing Failed", progress=0)
        sys.exit(1)

    print(f"✅ Burn-in complete: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Burn subtitle overlay into master video."
    )
    parser.add_argument("master_path", help="Path to master video")
    parser.add_argument("overlay_path", help="Path to overlay MOV (ProRes 4444)")
    parser.add_argument("output_path", help="Path to output MP4")
    args = parser.parse_args()

    burn_subtitles(args.master_path, args.overlay_path, args.output_path)
