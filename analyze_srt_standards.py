import re
import sys
from datetime import datetime, timedelta

def parse_time(t_str):
    # 00:00:08,468
    return datetime.strptime(t_str.strip().replace(',', '.'), "%H:%M:%S.%f")

def analyze_srt(file_path):
    print(f"📊 Analyzing: {file_path}")
    print("="*50)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    blocks = content.strip().split('\n\n')
    
    violations = {
        "cps_high": 0,
        "line_too_long": 0,
        "duration_too_short": 0,
        "duration_too_long": 0,
        "gap_too_short": 0,
        "dangling_words": 0,
        "lines_too_many": 0
    }
    
    orphans = ["ég", "og", "en", "sem", "að", "því", "er", "var"]
    
    last_end = None
    
    for i, block in enumerate(blocks):
        lines = block.split('\n')
        if len(lines) < 3: continue
        
        idx = lines[0]
        time_line = lines[1]
        text_lines = lines[2:]
        full_text = " ".join(text_lines)
        
        # Parse Time
        start_str, end_str = time_line.split(' --> ')
        start = parse_time(start_str)
        end = parse_time(end_str)
        duration = (end - start).total_seconds()
        
        # 1. Check Duration
        if duration < 0.8: # BBC min is ~0.8s (20 frames)
            violations["duration_too_short"] += 1
            # print(f"#{idx} Too Short: {duration}s")
            
        if duration > 7.0: # Netflix max is usually 6-7s
            violations["duration_too_long"] += 1
            
        # 2. Check Gap
        if last_end:
            gap = (start - last_end).total_seconds()
            if gap < 0.08 and gap > 0: # Min 2 frames (approx 80ms)
                violations["gap_too_short"] += 1
        last_end = end
        
        # 3. Check Line Count
        if len(text_lines) > 2:
            violations["lines_too_many"] += 1
            
        # 4. Check Line Length & CPS
        char_count = len(full_text)
        cps = char_count / duration if duration > 0 else 0
        
        if cps > 17: # Netflix max 17, BBC 15
            violations["cps_high"] += 1
            # print(f"#{idx} High CPS: {cps:.1f}")
            
        for line in text_lines:
            if len(line) > 42: # Netflix 42, BBC 37
                violations["line_too_long"] += 1
                
        # 5. Check Dangling Words (Orphans)
        # Check if the LAST line ends with a connector
        last_word = text_lines[-1].split()[-1].lower().strip(".,:;?!\"")
        if last_word in orphans:
            violations["dangling_words"] += 1
            print(f"#{idx} Dangling: '{text_lines[-1]}'")

    print("\n🚨 VIOLATION REPORT")
    print("-" * 30)
    print(f"High CPS (>17):        {violations['cps_high']}")
    print(f"Lines Too Long (>42):  {violations['line_too_long']}")
    print(f"Too Short (<0.8s):     {violations['duration_too_short']}")
    print(f"Too Long (>7s):        {violations['duration_too_long']}")
    print(f"Gap Too Short (<2fr):  {violations['gap_too_short']}")
    print(f"Too Many Lines (>2):   {violations['lines_too_many']}")
    print(f"Dangling Words:        {violations['dangling_words']}")
    print("-" * 30)
    print(f"Total Blocks: {len(blocks)}")

if __name__ == "__main__":
    analyze_srt("4_DELIVERY/SRT/S6 EP. 1 DOAN (RALEY).srt")
