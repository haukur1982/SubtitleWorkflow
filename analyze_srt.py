import re
from pathlib import Path

def parse_time(time_str):
    h, m, s_ms = time_str.split(':')
    s, ms = s_ms.split(',')
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

def analyze_srt(file_path):
    print(f"Analyzing {file_path}...")
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read().strip()

    blocks = re.split(r'\n\n+', content)
    events = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3: continue
        if '-->' not in lines[1]: continue
        
        start_str, end_str = lines[1].split(' --> ')
        start = parse_time(start_str)
        end = parse_time(end_str)
        text = "\n".join(lines[2:])
        
        events.append({'start': start, 'end': end, 'text': text, 'id': lines[0]})

    overlaps = 0
    close_calls = 0 # < 100ms gap
    
    for i in range(len(events) - 1):
        curr = events[i]
        next_event = events[i+1]
        
        gap = next_event['start'] - curr['end']
        
        if gap < 0:
            print(f"❌ OVERLAP detected between #{curr['id']} and #{next_event['id']}")
            print(f"   #{curr['id']}: {curr['end']} ms | #{next_event['id']}: {next_event['start']} ms")
            print(f"   Overlap: {abs(gap)} ms")
            overlaps += 1
        elif gap < 100:
            print(f"⚠️  CLOSE CALL (<100ms) between #{curr['id']} and #{next_event['id']}")
            print(f"   Gap: {gap} ms")
            close_calls += 1
            
    print(f"\nAnalysis Complete.")
    print(f"Total Overlaps: {overlaps}")
    print(f"Total Close Calls (<100ms): {close_calls}")

if __name__ == "__main__":
    analyze_srt("/Users/haukur/SubtitleWorkflow/4_FINAL_OUTPUT/DONE_I2248_Gospel_RUVBOX.srt")
