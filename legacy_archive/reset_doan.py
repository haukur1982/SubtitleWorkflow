import sqlite3
import config

def reset_job(partial_name):
    conn = sqlite3.connect("production.db")
    c = conn.cursor()
    
    # Find the job
    c.execute("SELECT file_stem FROM jobs WHERE file_stem LIKE ?", (f"%{partial_name}%",))
    job = c.fetchone()
    
    if job:
        file_stem = job[0]
        print(f"Found job: {file_stem}")
        # Reset to TRANSCRIBED so it gets picked up by Translator
        c.execute("UPDATE jobs SET stage = 'TRANSCRIBED', status = 'Reset for V2' WHERE file_stem = ?", (file_stem,))
        conn.commit()
        print("✅ Job reset to TRANSCRIBED.")
    else:
        print("❌ Job not found.")
        
    conn.close()

if __name__ == "__main__":
    reset_job("S6 EP. 1 DOAN")
