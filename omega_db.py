import sqlite3
import json
import time
from pathlib import Path
from datetime import datetime

# CONFIG
DB_PATH = Path("production.db")

def init_db():
    """Initialize the database if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create JOBS table
    c.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            file_stem TEXT PRIMARY KEY,
            stage TEXT,
            status TEXT,
            progress REAL,
            updated_at TIMESTAMP,
            meta TEXT,
            target_language TEXT DEFAULT 'is',
            program_profile TEXT DEFAULT 'standard',
            subtitle_style TEXT DEFAULT 'Classic',
            editor_report TEXT
        )
    ''')
    
    # Enable Write-Ahead Logging (WAL) for concurrency
    c.execute("PRAGMA journal_mode=WAL;")
    
    conn.commit()
    conn.close()

def migrate_schema():
    """Ensure the schema is up to date."""
    if not DB_PATH.exists():
        init_db()
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check if target_language exists
    c.execute("PRAGMA table_info(jobs)")
    columns = [info[1] for info in c.fetchall()]
    
    if "target_language" not in columns:
        print("⚠️ Migrating DB: Adding target_language column...")
        c.execute("ALTER TABLE jobs ADD COLUMN target_language TEXT DEFAULT 'is'")
        conn.commit()
        
    if "program_profile" not in columns:
        print("⚠️ Migrating DB: Adding program_profile column...")
        c.execute("ALTER TABLE jobs ADD COLUMN program_profile TEXT DEFAULT 'standard'")
        conn.commit()
        
    if "subtitle_style" not in columns:
        print("⚠️ Migrating DB: Adding subtitle_style column...")
        c.execute("ALTER TABLE jobs ADD COLUMN subtitle_style TEXT DEFAULT 'Classic'")
        
    if "editor_report" not in columns:
        print("⚠️ Migrating DB: Adding editor_report column...")
        c.execute("ALTER TABLE jobs ADD COLUMN editor_report TEXT")
        conn.commit()
        
    print("✅ Migration Complete.")
    
    conn.close()

def update(file_stem, stage=None, status=None, progress=None, meta=None, target_language=None, program_profile=None, subtitle_style=None):
    """Update job status in the database."""
    if not DB_PATH.exists():
        init_db()
        
    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None # Enable manual transaction control
    c = conn.cursor()
    
    try:
        c.execute("BEGIN IMMEDIATE")
        
        now = datetime.now().isoformat()
        
        # Check if exists
        c.execute("SELECT * FROM jobs WHERE file_stem=?", (file_stem,))
        exists = c.fetchone()
        
        if exists:
            # Decode existing meta for merging
            try:
                # Meta is at index 5
                existing_meta = json.loads(exists[5]) if exists[5] else {}
            except Exception:
                existing_meta = {}
                
            # Build dynamic update query
            fields = []
            values = []
            if stage is not None:
                fields.append("stage=?")
                values.append(stage)
            if status is not None:
                fields.append("status=?")
                values.append(status)
            if progress is not None:
                fields.append("progress=?")
                values.append(progress)
            if meta is not None:
                # Merge meta dicts so we don't lose previously stored fields
                merged_meta = {**existing_meta, **meta}
                fields.append("meta=?")
                values.append(json.dumps(merged_meta))
            if target_language is not None:
                fields.append("target_language=?")
                values.append(target_language)
            if program_profile is not None:
                fields.append("program_profile=?")
                values.append(program_profile)
            if subtitle_style is not None:
                fields.append("subtitle_style=?")
                values.append(subtitle_style)
                
            fields.append("updated_at=?")
            values.append(now)
            
            values.append(file_stem) # For WHERE clause
            
            query = f"UPDATE jobs SET {', '.join(fields)} WHERE file_stem=?"
            c.execute(query, tuple(values))
            
        else:
            # Insert new
            c.execute('''
                INSERT INTO jobs (file_stem, stage, status, progress, updated_at, meta, target_language, program_profile, subtitle_style)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (file_stem, stage or "QUEUED", status or "Initialized", progress or 0.0, now, json.dumps(meta or {}), target_language or 'is', program_profile or 'standard', subtitle_style or 'Classic'))
            
        c.execute("COMMIT")
        
    except Exception as e:
        print(f"❌ DB Update Failed: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def delete(file_stem):
    """Delete a job from the database."""
    if not DB_PATH.exists():
        return
        
    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None
    c = conn.cursor()
    
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM jobs WHERE file_stem=?", (file_stem,))
        conn.commit()
    except Exception as e:
        print(f"❌ DB Delete Failed: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    migrate_schema()

def get_job(file_stem):
    """Fetch a single job."""
    if not DB_PATH.exists(): return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM jobs WHERE file_stem=?", (file_stem,))
    row = c.fetchone()
    conn.close()
    
    if row:
        job = dict(row)
        try:
            job["meta"] = json.loads(job["meta"]) if job["meta"] else {}
        except:
            job["meta"] = {}
        return job
    return None

def get_all_jobs():
    """Get all jobs sorted by update time."""
    if not DB_PATH.exists(): return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM jobs ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        data = dict(row)
        try:
            data["meta"] = json.loads(data.get("meta") or "{}")
        except Exception:
            data["meta"] = {}
        result.append(data)
    return result

def delete_job(file_stem):
    """Remove a job (e.g. if file deleted)."""
    if not DB_PATH.exists(): return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM jobs WHERE file_stem=?", (file_stem,))
    conn.commit()
    conn.close()
