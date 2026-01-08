import json
from pathlib import Path

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds * 1000) % 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def generate_srt(json_path, output_path):
    with open(json_path, 'r') as f:
        data = json.load(f)
        
    segments = data.get("segments", [])
    if not segments:
        print("No segments found.")
        return

    with open(output_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments):
            start = format_timestamp(seg['start'])
            end = format_timestamp(seg['end'])
            text = seg['text'].strip()
            
            f.write(f"{i+1}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{text}\n\n")
            
    print(f"✅ Generated: {output_path}")

if __name__ == "__main__":
    base_dir = Path("/Users/haukur/SubtitleWorkflow")
    skeleton = base_dir / "2_VAULT/Data/TimesSquareChurch_20251116_SKELETON_DONE.json"
    output = base_dir / "4_DELIVERY/SRT/TimesSquareChurch_20251116_ENGLISH.srt"
    
    if skeleton.exists():
        generate_srt(skeleton, output)
    else:
        print(f"❌ Skeleton not found: {skeleton}")
