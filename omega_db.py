import sqlite3
import json
import time
from pathlib import Path
from datetime import datetime

# CONFIG
DB_PATH = Path("production.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 60000;")  # 60s timeout for heavy load
        conn.execute("PRAGMA synchronous = NORMAL;")    # Reduce I/O contention
    except Exception:
        pass
    return conn

def init_db():
    """Initialize the database if it doesn't exist."""
    conn = _connect()
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
            editor_report TEXT,
            client TEXT DEFAULT 'unknown',
            due_date DATE
        )
    ''')
    
    # Create DELIVERIES table
    c.execute('''
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_stem TEXT,
            client TEXT,
            delivered_at TIMESTAMP,
            method TEXT,
            notes TEXT
        )
    ''')

    # Create SYSTEM_STATE table for global versioning
    c.execute('''
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    c.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('db_version', '0')")
    
    # =========================================================================
    # LOCALIZATION PLATFORM TABLES (Phase 0)
    # =========================================================================
    
    # PROGRAMS table: groups jobs by source video
    c.execute('''
        CREATE TABLE IF NOT EXISTS programs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            original_filename TEXT,
            video_path TEXT,
            thumbnail_path TEXT,
            duration_seconds REAL,
            client TEXT,
            due_date TEXT,
            default_style TEXT DEFAULT 'Classic',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT
        )
    ''')
    
    # TRACKS table: each language output for a program
    c.execute('''
        CREATE TABLE IF NOT EXISTS tracks (
            id TEXT PRIMARY KEY,
            program_id TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'subtitle',
            language_code TEXT NOT NULL,
            language_name TEXT,
            stage TEXT DEFAULT 'QUEUED',
            status TEXT DEFAULT 'Pending',
            progress REAL DEFAULT 0.0,
            job_id TEXT,
            voice_id TEXT,
            rating REAL,
            output_path TEXT,
            depends_on TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT,
            FOREIGN KEY (program_id) REFERENCES programs(id),
            FOREIGN KEY (depends_on) REFERENCES tracks(id)
        )
    ''')
    
    # TRACK_DELIVERIES table: where track outputs went
    c.execute('''
        CREATE TABLE IF NOT EXISTS track_deliveries (
            id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL,
            destination TEXT,
            recipient TEXT,
            delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
    ''')
    
    # Create indexes for common queries
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_program ON tracks(program_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_stage ON tracks(stage)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_programs_client ON programs(client)')
    
    # Enable Write-Ahead Logging (WAL) for better concurrency
    c.execute("PRAGMA journal_mode=WAL;")
    # Increase busy timeout to reduce lock errors
    c.execute("PRAGMA busy_timeout=5000;")
    
    conn.commit()
    conn.close()

def migrate_schema():
    """Ensure the schema is up to date."""
    if not DB_PATH.exists():
        init_db()
        return

    conn = _connect()
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

    if "client" not in columns:
        print("⚠️ Migrating DB: Adding client column...")
        c.execute("ALTER TABLE jobs ADD COLUMN client TEXT DEFAULT 'unknown'")
        conn.commit()

    if "due_date" not in columns:
        print("⚠️ Migrating DB: Adding due_date column...")
        c.execute("ALTER TABLE jobs ADD COLUMN due_date DATE")
        conn.commit()
    
    # Check if deliveries table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='deliveries'")
    if not c.fetchone():
        print("⚠️ Migrating DB: Creating deliveries table...")
        c.execute('''
            CREATE TABLE deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_stem TEXT,
                client TEXT,
                delivered_at TIMESTAMP,
                method TEXT,
                notes TEXT
            )
        ''')
        conn.commit()
        
    print("✅ Migration Complete.")
    
    conn.close()

def _increment_version(cursor):
    """Bumps the db_version so the frontend knows to refetch."""
    try:
        cursor.execute("UPDATE system_state SET value = CAST(value AS INTEGER) + 1 WHERE key = 'db_version'")
    except Exception:
        pass

def update(
    file_stem,
    stage=None,
    status=None,
    progress=None,
    meta=None,
    target_language=None,
    program_profile=None,
    subtitle_style=None,
    editor_report=None,
    client=None,
    due_date=None,
):
    """Update job status in the database."""
    if not DB_PATH.exists():
        init_db()
        
    conn = _connect()
    conn.isolation_level = None # Enable manual transaction control
    c = conn.cursor()
    
    try:
        c.execute("BEGIN IMMEDIATE")
        
        now = datetime.now().isoformat()

        def _normalize_timeline(value):
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            return []

        def _append_stage_timeline(timeline, stage_value, now_value):
            if not stage_value:
                return timeline, False
            timeline = list(timeline)
            last = timeline[-1] if timeline else None
            if last and last.get("stage") == stage_value and not last.get("ended_at"):
                return timeline, False
            if last and not last.get("ended_at"):
                last["ended_at"] = now_value
            timeline.append({"stage": stage_value, "started_at": now_value, "ended_at": None})
            return timeline, True

        def _append_status_timeline(timeline, status_value, now_value, max_items=50):
            if not status_value:
                return timeline, False
            timeline = list(timeline)
            last = timeline[-1] if timeline else None
            if last and last.get("status") == status_value:
                return timeline, False
            timeline.append({"status": status_value, "at": now_value})
            if max_items and len(timeline) > max_items:
                timeline = timeline[-max_items:]
            return timeline, True
        
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
            existing_stage = exists[1]
            existing_status = exists[2]
                
            # Build dynamic update query
            fields = []
            values = []
            meta_changed = False

            incoming_meta = meta if isinstance(meta, dict) else {}
            merged_meta = {**existing_meta, **incoming_meta}

            if stage is not None:
                timeline = _normalize_timeline(merged_meta.get("stage_timeline"))
                timeline, changed = _append_stage_timeline(timeline, stage, now)
                if changed or (timeline and timeline != merged_meta.get("stage_timeline")):
                    merged_meta["stage_timeline"] = timeline
                    meta_changed = True

            if status is not None:
                timeline = _normalize_timeline(merged_meta.get("status_timeline"))
                if not timeline and existing_status:
                    timeline.append({"status": existing_status, "at": now})
                timeline, changed = _append_status_timeline(timeline, status, now)
                if changed or (timeline and timeline != merged_meta.get("status_timeline")):
                    merged_meta["status_timeline"] = timeline
                    meta_changed = True

            incoming_cloud_stage = incoming_meta.get("cloud_stage") if incoming_meta else None
            if incoming_cloud_stage:
                timeline = _normalize_timeline(merged_meta.get("cloud_stage_timeline"))
                timeline, changed = _append_stage_timeline(timeline, str(incoming_cloud_stage), now)
                if changed or (timeline and timeline != merged_meta.get("cloud_stage_timeline")):
                    merged_meta["cloud_stage_timeline"] = timeline
                    meta_changed = True

            if stage is not None:
                fields.append("stage=?")
                values.append(stage)
            if status is not None:
                fields.append("status=?")
                values.append(status)
            if progress is not None:
                fields.append("progress=?")
                values.append(progress)
            if meta is not None or meta_changed:
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
            if editor_report is not None:
                fields.append("editor_report=?")
                values.append(editor_report)
            if client is not None:
                fields.append("client=?")
                values.append(client)
            if due_date is not None:
                fields.append("due_date=?")
                values.append(due_date)
                
            fields.append("updated_at=?")
            values.append(now)
            
            values.append(file_stem) # For WHERE clause
            
            query = f"UPDATE jobs SET {', '.join(fields)} WHERE file_stem=?"
            c.execute(query, tuple(values))
            
        else:
            # Insert new
            new_meta = meta if isinstance(meta, dict) else {}
            stage_value = stage or "QUEUED"
            timeline = _normalize_timeline(new_meta.get("stage_timeline"))
            timeline, _ = _append_stage_timeline(timeline, stage_value, now)
            new_meta["stage_timeline"] = timeline

            status_value = status or "Initialized"
            timeline = _normalize_timeline(new_meta.get("status_timeline"))
            timeline, _ = _append_status_timeline(timeline, status_value, now)
            new_meta["status_timeline"] = timeline
            incoming_cloud_stage = new_meta.get("cloud_stage")
            if incoming_cloud_stage:
                timeline = _normalize_timeline(new_meta.get("cloud_stage_timeline"))
                timeline, _ = _append_stage_timeline(timeline, str(incoming_cloud_stage), now)
                new_meta["cloud_stage_timeline"] = timeline
            c.execute('''
                INSERT INTO jobs (file_stem, stage, status, progress, updated_at, meta, target_language, program_profile, subtitle_style, editor_report, client, due_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (file_stem, stage or "QUEUED", status or "Initialized", progress or 0.0, now, json.dumps(new_meta), target_language or 'is', program_profile or 'standard', subtitle_style or 'Classic', editor_report, client or 'unknown', due_date))
            
        _increment_version(c)  # Single increment per update
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
        
    conn = _connect()
    conn.isolation_level = None
    c = conn.cursor()
    
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM jobs WHERE file_stem=?", (file_stem,))
        _increment_version(c)
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
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM jobs WHERE file_stem=?", (file_stem,))
    row = c.fetchone()
    conn.close()
    
    if row:
        job = dict(row)
        try:
            meta = json.loads(job.get("meta") or "{}")
        except Exception:
            meta = {}
        job["meta"] = meta if isinstance(meta, dict) else {}
        return job
    return None

def get_all_jobs():
    """Get all jobs sorted by update time."""
    if not DB_PATH.exists(): return []
    conn = _connect()
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
    conn = _connect()
    c = conn.cursor()
    c.execute("DELETE FROM jobs WHERE file_stem=?", (file_stem,))
    conn.commit()
    conn.close()


def get_job(file_stem):
    """Get a single job by file_stem."""
    if not DB_PATH.exists():
        return None
    conn = _connect()
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


def log_delivery(job_stem, client, delivered_at, method, notes=""):
    """Log a delivery in the deliveries table."""
    if not DB_PATH.exists():
        init_db()
    conn = _connect()
    c = conn.cursor()
    c.execute('''
        INSERT INTO deliveries (job_stem, client, delivered_at, method, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (job_stem, client, delivered_at, method, notes))
    conn.commit()
    conn.close()


def get_deliveries(job_stem=None):
    """Get delivery history. If job_stem provided, filter to that job."""
    if not DB_PATH.exists():
        return []
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if job_stem:
        c.execute("SELECT * FROM deliveries WHERE job_stem=? ORDER BY delivered_at DESC", (job_stem,))
    else:
        c.execute("SELECT * FROM deliveries ORDER BY delivered_at DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_jobs_since(last_version_processed):
    """Placeholder for delta-fetching logic if needed later."""
    # For now, we use the version to trigger a full fetch of only metadata
    # but the goal is to only fetch the delta.
    return get_all_jobs()


# =============================================================================
# PROGRAMS & TRACKS (Localization Platform API)
# =============================================================================

import uuid

def _generate_id() -> str:
    """Generate a UUID for new records."""
    return str(uuid.uuid4())

# --- PROGRAMS ---

def create_program(
    title: str,
    original_filename: str = None,
    video_path: str = None,
    thumbnail_path: str = None,
    duration_seconds: float = None,
    client: str = None,
    due_date: str = None,
    default_style: str = 'Classic',
    meta: dict = None
) -> str:
    """Create a new program. Returns program ID."""
    program_id = _generate_id()
    now = datetime.now().isoformat()
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO programs (id, title, original_filename, video_path, thumbnail_path,
                                  duration_seconds, client, due_date, default_style, 
                                  created_at, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (program_id, title, original_filename, video_path, thumbnail_path,
              duration_seconds, client, due_date, default_style,
              now, now, json.dumps(meta or {})))
        conn.commit()
    finally:
        conn.close()
    
    return program_id


def get_program(program_id: str) -> dict:
    """Get a program by ID. Returns None if not found."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM programs WHERE id=?", (program_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except:
            result['meta'] = {}
    return result


def update_program(program_id: str, **kwargs) -> bool:
    """Update program fields. Returns True if updated."""
    if not kwargs:
        return False
    
    # Handle meta specially
    if 'meta' in kwargs and isinstance(kwargs['meta'], dict):
        # Merge with existing meta
        existing = get_program(program_id)
        if existing:
            existing_meta = existing.get('meta', {}) or {}
            existing_meta.update(kwargs['meta'])
            kwargs['meta'] = json.dumps(existing_meta)
    
    kwargs['updated_at'] = datetime.now().isoformat()
    
    set_clause = ', '.join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values()) + [program_id]
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute(f"UPDATE programs SET {set_clause} WHERE id=?", values)
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def get_all_programs(client: str = None, limit: int = 100) -> list:
    """Get all programs, optionally filtered by client."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    if client:
        c.execute("SELECT * FROM programs WHERE client=? ORDER BY created_at DESC LIMIT ?", (client, limit))
    else:
        c.execute("SELECT * FROM programs ORDER BY created_at DESC LIMIT ?", (limit,))
    
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except:
                r['meta'] = {}
        results.append(r)
    return results


def get_program_by_video(video_path: str) -> dict:
    """Find program by video path. Returns None if not found."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM programs WHERE video_path=?", (video_path,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except:
            result['meta'] = {}
    return result


# --- TRACKS ---

def create_track(
    program_id: str,
    type: str = 'subtitle',
    language_code: str = 'is',
    language_name: str = None,
    stage: str = 'QUEUED',
    status: str = 'Pending',
    job_id: str = None,
    voice_id: str = None,
    depends_on: str = None,
    meta: dict = None
) -> str:
    """Create a new track for a program. Returns track ID."""
    track_id = _generate_id()
    now = datetime.now().isoformat()
    
    # Auto-fill language name from profiles if not provided
    if not language_name:
        from profiles import LANGUAGES
        lang_info = LANGUAGES.get(language_code, {})
        language_name = lang_info.get('name', language_code.upper())
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO tracks (id, program_id, type, language_code, language_name,
                               stage, status, progress, job_id, voice_id, depends_on,
                               created_at, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?)
        ''', (track_id, program_id, type, language_code, language_name,
              stage, status, job_id, voice_id, depends_on,
              now, now, json.dumps(meta or {})))
        conn.commit()
    finally:
        conn.close()
    
    return track_id


def get_track(track_id: str) -> dict:
    """Get a track by ID."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except:
            result['meta'] = {}
    return result


def get_tracks_for_program(program_id: str) -> list:
    """Get all tracks for a program."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tracks WHERE program_id=? ORDER BY created_at", (program_id,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except:
                r['meta'] = {}
        results.append(r)
    return results


def update_track(track_id: str, **kwargs) -> bool:
    """Update track fields."""
    if not kwargs:
        return False
    
    # Handle meta specially
    if 'meta' in kwargs and isinstance(kwargs['meta'], dict):
        existing = get_track(track_id)
        if existing:
            existing_meta = existing.get('meta', {}) or {}
            existing_meta.update(kwargs['meta'])
            kwargs['meta'] = json.dumps(existing_meta)
    
    kwargs['updated_at'] = datetime.now().isoformat()
    
    set_clause = ', '.join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values()) + [track_id]
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute(f"UPDATE tracks SET {set_clause} WHERE id=?", values)
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def get_track_by_job(job_id: str) -> dict:
    """Find track by linked job ID."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tracks WHERE job_id=?", (job_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except:
            result['meta'] = {}
    return result


def get_active_tracks(limit: int = 50) -> list:
    """Get all tracks that are in progress (not COMPLETE or DELIVERED)."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT t.*, p.title as program_title 
        FROM tracks t 
        JOIN programs p ON t.program_id = p.id
        WHERE t.stage NOT IN ('COMPLETE', 'DELIVERED')
        ORDER BY t.updated_at DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except:
                r['meta'] = {}
        results.append(r)
    return results


# --- TRACK DELIVERIES ---

def record_track_delivery(
    track_id: str,
    destination: str,
    recipient: str = None,
    notes: str = None
) -> str:
    """Record that a track was delivered. Returns delivery ID."""
    delivery_id = _generate_id()
    now = datetime.now().isoformat()
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO track_deliveries (id, track_id, destination, recipient, delivered_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (delivery_id, track_id, destination, recipient, now, notes))
        
        # Update track stage to DELIVERED
        c.execute("UPDATE tracks SET stage='DELIVERED', updated_at=? WHERE id=?", (now, track_id))
        
        conn.commit()
    finally:
        conn.close()
    
    return delivery_id


def get_deliveries_for_track(track_id: str) -> list:
    """Get all delivery records for a track."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM track_deliveries WHERE track_id=? ORDER BY delivered_at DESC", (track_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_deliveries(days: int = 7, limit: int = 100) -> list:
    """Get recent deliveries with program/track info."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT d.*, t.language_code, t.type as track_type, p.title as program_title
        FROM track_deliveries d
        JOIN tracks t ON d.track_id = t.id
        JOIN programs p ON t.program_id = p.id
        WHERE d.delivered_at >= datetime('now', ?)
        ORDER BY d.delivered_at DESC
        LIMIT ?
    ''', (f'-{days} days', limit))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]
