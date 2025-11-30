import re
from pathlib import Path

def parse_srt(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split by double newlines
    blocks = re.split(r'\n\n+', content.strip())
    segments = []
    
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 3:
            try:
                idx = int(lines[0].strip())
                time_code = lines[1].strip()
                text = '\n'.join(lines[2:])
                segments.append({'id': idx, 'time': time_code, 'text': text})
            except ValueError:
                continue
    return segments

def write_srt(segments, output_path):
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments):
            f.write(f"{i+1}\n")
            f.write(f"{seg['time']}\n")
            f.write(f"{seg['text']}\n\n")

def main():
    base_path = Path("/Users/haukur/SubtitleWorkflow/4_FINAL_OUTPUT")
    input_path = base_path / "2HOP_2913_INT57_RUVBOX.srt"
    if not input_path.exists():
        input_path = base_path / "DONE_2HOP_2913_INT57_RUVBOX.srt"
    
    output_path = base_path / "2HOP_2913_INT57_RUVBOX.srt"
    
    print(f"Reading {input_path}...")
    segments = parse_srt(input_path)
    print(f"Total segments: {len(segments)}")
    
    # Filter out:
    # 1. Intro (1-2)
    # 2. First Choir (37-45) - NEW
    # 3. Second Choir (84-167)
    # 4. Noise/Hallucinations "Ha?" (146-147, 161-162) - NEW
    
    noise_ids = [146, 147, 161, 162]
    
    clean_segments = []
    for s in segments:
        sid = s['id']
        if 1 <= sid <= 2: continue
        if 37 <= sid <= 45: continue
        if 84 <= sid <= 167: continue
        if sid in noise_ids: continue
        clean_segments.append(s)
    
    print(f"Removed {len(segments) - len(clean_segments)} segments.")
    print(f"Remaining segments: {len(clean_segments)}")
    
    print(f"Writing to {output_path}...")
    write_srt(clean_segments, output_path)
    print("Done!")

if __name__ == "__main__":
    main()
