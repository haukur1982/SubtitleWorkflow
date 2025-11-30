import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path("production.db")

def cleanup_stale_jobs():
    if not DB_PATH.exists():
        print("Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Define stale threshold (e.g., 2 hours)
    # SQLite datetime is ISO string
    # We'll fetch all active jobs and check python side for easier parsing
    
    c.execute("SELECT file_stem, updated_at, stage, status FROM jobs WHERE stage != 'COMPLETED' AND status NOT LIKE '%Error%'")
    rows = c.fetchall()
    
    now = datetime.datetime.now()
    
    stale_count = 0
    
    print(f"Checking {len(rows)} active jobs for staleness...")
    
    for row in rows:
        stem, updated_at, stage, status = row
        try:
            # Parse ISO format (might have microseconds or not)
            last_update = datetime.datetime.fromisoformat(updated_at)
            diff = now - last_update
            
            # If older than 2 hours
            if diff.total_seconds() > 7200:
                print(f"⚠️ Stale Job Found: {stem} (Last updated: {updated_at}, {diff})")
                
                # Mark as Stalled
                c.execute("UPDATE jobs SET status=?, stage=? WHERE file_stem=?", 
                          (f"Stalled (Last active: {updated_at})", "FAILED", stem))
                stale_count += 1
                
        except Exception as e:
            print(f"Error parsing date for {stem}: {e}")
            continue
            
    conn.commit()
    conn.close()
    
    print(f"✅ Cleanup Complete. Marked {stale_count} jobs as Stalled/Failed.")

if __name__ == "__main__":
    cleanup_stale_jobs()
