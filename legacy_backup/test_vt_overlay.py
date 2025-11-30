import subprocess
import os
from pathlib import Path

def test_vt_overlay():
    print("🧪 Testing h264_videotoolbox with overlay filter...")
    
    # 1. Create a dummy black video (5 seconds)
    base_video = "test_base.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=5",
        "-c:v", "libx264", base_video
    ], check=True, stderr=subprocess.DEVNULL)
    
    # 2. Create a dummy overlay image
    overlay_img = "test_overlay.png"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red@0.5:s=500x200",
        "-frames:v", "1", overlay_img
    ], check=True, stderr=subprocess.DEVNULL)
    
    # 3. Try to burn with h264_videotoolbox
    output_video = "test_vt_output.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", base_video,
        "-i", overlay_img,
        "-filter_complex", "[0:v][1:v]overlay=100:100[v]",
        "-map", "[v]",
        "-c:v", "h264_videotoolbox",
        "-b:v", "5000k",
        output_video
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Success! h264_videotoolbox supports overlay.")
            print(f"Output: {output_video}")
        else:
            print("❌ Failed.")
            print(result.stderr)
    except Exception as e:
        print(f"❌ Exception: {e}")

    # Cleanup
    for f in [base_video, overlay_img, output_video]:
        if os.path.exists(f):
            os.remove(f)

if __name__ == "__main__":
    test_vt_overlay()
