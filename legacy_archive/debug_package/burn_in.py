import argparse
import subprocess
import sys
from pathlib import Path


import omega_db

def burn_subtitles(master_path: str, overlay_path: str, output_path: str, stem: str = None) -> None:
    from publisher import get_ffmpeg_binary

    ffmpeg_bin = get_ffmpeg_binary()
    master = Path(master_path)
    overlay = Path(overlay_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    
    if stem:
        omega_db.update(stem, status="Compositing Final Video", progress=91.0)

    # Get duration for progress
    total_duration = 1
    try:
        duration_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(master)]
        duration_res = subprocess.run(duration_cmd, capture_output=True, text=True)
        total_duration = float(duration_res.stdout.strip()) if duration_res.stdout else 1
    except: pass

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(master),
        "-i", str(overlay),
        "-filter_complex",
        "[0:v]eq=gamma=1.1[vid];[vid][1:v]overlay=0:0:shortest=1,unsharp=5:5:0.8:3:3:0.4[v]",
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
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
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
