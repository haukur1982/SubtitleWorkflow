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
            meta TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def update(file_stem, stage=None, status=None, progress=None, meta=None):
    """Update job status in the database."""
    if not DB_PATH.exists():
        init_db()
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    now = datetime.now().isoformat()
    
    # Check if exists
    c.execute("SELECT * FROM jobs WHERE file_stem=?", (file_stem,))
    exists = c.fetchone()
    
    if exists:
        # Decode existing meta for merging
        try:
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
            
        fields.append("updated_at=?")
        values.append(now)
        
        values.append(file_stem) # For WHERE clause
        
        query = f"UPDATE jobs SET {', '.join(fields)} WHERE file_stem=?"
        c.execute(query, tuple(values))
        
    else:
        # Insert new
        c.execute('''
            INSERT INTO jobs (file_stem, stage, status, progress, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (file_stem, stage or "QUEUED", status or "Initialized", progress or 0.0, now, json.dumps(meta or {})))
        
    conn.commit()
    conn.close()

def get_job(file_stem):
    """Get a single job."""
    if not DB_PATH.exists(): return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM jobs WHERE file_stem=?", (file_stem,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    try:
        data["meta"] = json.loads(data.get("meta") or "{}")
    except Exception:
        data["meta"] = {}
    return data

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
