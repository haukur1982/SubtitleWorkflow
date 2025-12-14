from flask import Flask, render_template, jsonify, request, send_file
import sqlite3
import json
import shutil
import time
import threading
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename
import omega_db
import subprocess
import config
import logging

# Configure Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

app = Flask(__name__)

_force_burn_lock = threading.Lock()
_force_burn_inflight = set()

def get_all_jobs():
    """Fetch all jobs from the database."""
    conn = sqlite3.connect(omega_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM jobs ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()
    
    jobs = []
    for row in rows:
        job = dict(row)
        try:
            job["meta"] = json.loads(job["meta"])
        except:
            job["meta"] = {}
        jobs.append(job)
    return jobs

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".mpg", ".mpeg", ".m4v", ".wmv", ".flv"}

def _resolve_vault_video(vault_dir: Path, stem: str) -> Path:
    candidates = []
    for path in vault_dir.glob(f"{stem}.*"):
        if path.name.startswith("._"):
            continue
        if path.suffix.lower() in _VIDEO_EXTS:
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"Video not found in vault for {stem}: {vault_dir}")
    # Prefer mp4 when multiple candidates exist.
    candidates.sort(key=lambda p: (p.suffix.lower() != ".mp4", p.name.lower()))
    return candidates[0]


def _derive_delivery_dir_from_vault(vault_dir: Path) -> Path:
    """
    Map `2_VAULT/<client>/<year>/<stem>` → `4_DELIVERY/<client>/<year>/<stem>`.
    Falls back to `4_DELIVERY/VIDEO` if the vault dir isn't under `config.VAULT_DIR`.
    """
    try:
        rel = vault_dir.resolve(strict=False).relative_to(config.VAULT_DIR.resolve(strict=False))
        return config.DELIVERY_DIR / rel
    except Exception:
        return config.DELIVERY_DIR / "VIDEO"


def _run_force_burn(file_stem: str):
    """
    Background burn task kicked off by the dashboard.
    Uses the job's `vault_path` (queue-system layout) when available.
    """
    try:
        logger.info(f"🔥 Force burn started: {file_stem}")
        job = omega_db.get_job(file_stem) or {}

        meta = job.get("meta", {}) or {}

        vault_path = job.get("vault_path") or meta.get("vault_path")
        vault_dir = Path(vault_path) if vault_path else None

        delivery_dir = _derive_delivery_dir_from_vault(vault_dir) if vault_dir else config.VIDEO_DIR
        delivery_dir.mkdir(parents=True, exist_ok=True)

        # Resolve SRT (prefer delivery; otherwise copy from vault; legacy fallback to SRT_DIR)
        srt_path = None
        delivery_srt = delivery_dir / f"{file_stem}.srt"
        if delivery_srt.exists():
            srt_path = delivery_srt
        if srt_path is None and vault_dir is not None:
            vault_srt = vault_dir / f"{file_stem}.srt"
            if vault_srt.exists():
                shutil.copy2(vault_srt, delivery_srt)
                srt_path = delivery_srt
        if srt_path is None:
            legacy_srt = config.SRT_DIR / f"{file_stem}.srt"
            if legacy_srt.exists():
                srt_path = legacy_srt
        if srt_path is None:
            raise FileNotFoundError(f"SRT not found for {file_stem}")

        # Resolve video (prefer vault_path; legacy fallback to VAULT_VIDEOS)
        video_path = _resolve_vault_video(vault_dir, file_stem) if vault_dir else None
        if video_path is None:
            original_filename = meta.get("original_filename")
            if original_filename:
                candidate = config.VAULT_VIDEOS / original_filename
                if candidate.exists():
                    video_path = candidate
        if video_path is None:
            for candidate in config.VAULT_VIDEOS.glob(f"{file_stem}.*"):
                if candidate.name.startswith("._"):
                    continue
                if candidate.suffix.lower() in _VIDEO_EXTS:
                    video_path = candidate
                    break
        if video_path is None:
            source_path = meta.get("source_path")
            raise FileNotFoundError(f"Video not found for {file_stem} (source_path={source_path})")

        subtitle_style = job.get("subtitle_style") or meta.get("subtitle_style") or "Classic"
        logger.info(f"   Video: {video_path}")
        logger.info(f"   SRT: {srt_path}")
        logger.info(f"   Style: {subtitle_style}")
        logger.info(f"   Output dir: {delivery_dir}")

        omega_db.update(
            file_stem,
            stage="BURNING",
            status="Burning",
            progress=95.0,
            meta={"burn_started_at": datetime.now().isoformat(), "burn_requested_via": "dashboard"},
        )

        from workers import publisher
        output_path = publisher.publish(video_path, srt_path, subtitle_style=subtitle_style, output_dir=delivery_dir)
        logger.info(f"✅ Force burn complete: {output_path}")

        omega_db.update(
            file_stem,
            stage="COMPLETED",
            status="Done",
            progress=100.0,
            meta={"burn_completed_at": datetime.now().isoformat(), "final_output": str(output_path)},
        )
    except Exception as e:
        logger.error(f"Force burn failed for {file_stem}: {e}", exc_info=True)
        omega_db.update(
            file_stem,
            stage="FINALIZED",
            status=f"Burn Failed: {e}",
            progress=90.0,
            meta={"burn_failed_at": datetime.now().isoformat()},
        )
    finally:
        with _force_burn_lock:
            _force_burn_inflight.discard(file_stem)

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/jobs')
def api_jobs():
    jobs = get_all_jobs()
    return jsonify(jobs)

@app.route('/api/action', methods=['POST'])
def api_action():
    """Handle surgical actions."""
    data = request.json
    action = data.get('action')
    file_stem = data.get('file_stem')
    
    if not file_stem:
        return jsonify({"error": "Missing file_stem"}), 400

    if action == "reset_review":
        # Reset to REVIEWED stage (triggers Finalizer)
        omega_db.update(file_stem, stage="REVIEWED", status="Manual Reset", progress=50.0)
        return jsonify({"success": True, "message": f"Reset {file_stem} to Review"})
        
    elif action == "force_burn":
        with _force_burn_lock:
            if file_stem in _force_burn_inflight:
                return jsonify({"success": True, "message": f"Burn already running for {file_stem}"}), 200
            _force_burn_inflight.add(file_stem)

        omega_db.update(
            file_stem,
            stage="FINALIZED",
            status="Manual Burn (Queued)",
            progress=90.0,
            meta={"burn_requested_at": datetime.now().isoformat()},
        )

        t = threading.Thread(target=_run_force_burn, args=(file_stem,), name=f"force_burn_{file_stem}", daemon=True)
        t.start()
        return jsonify({"success": True, "message": f"Queued burn for {file_stem}"})
        
    elif action == "remove_lyrics":
        return jsonify({"success": False, "message": "Not implemented yet"}), 501

    elif action == "set_language":
        target_language = data.get('target_language')
        if not target_language:
            return jsonify({"error": "Missing target_language"}), 400
        omega_db.update(file_stem, target_language=target_language)
        return jsonify({"success": True, "message": f"Language set to {target_language}"})

    elif action == "set_profile":
        program_profile = data.get('program_profile')
        if not program_profile:
            return jsonify({"error": "Missing program_profile"}), 400
        omega_db.update(file_stem, program_profile=program_profile)
        return jsonify({"success": True, "message": f"Profile set to {program_profile}"})

    elif action == "set_style":
        subtitle_style = data.get('subtitle_style')
        if not subtitle_style:
            return jsonify({"error": "Missing subtitle_style"}), 400
        omega_db.update(file_stem, subtitle_style=subtitle_style)
        return jsonify({"success": True, "message": f"Style set to {subtitle_style}"})

    elif action == "approve_burn":
        omega_db.update(file_stem, status="Approved for Burn")
        return jsonify({"success": True, "message": f"Approved Burn for {file_stem}"})

    elif action == "set_mode":
        mode = data.get('mode')
        if mode not in ["AUTO", "REVIEW"]:
             return jsonify({"error": "Invalid mode"}), 400
        omega_db.update(file_stem, meta={"mode": mode})
        return jsonify({"success": True, "message": f"Mode set to {mode}"})

    elif action == "delete_job":
        omega_db.delete(file_stem)
        return jsonify({"success": True, "message": f"Deleted job {file_stem}"})

    return jsonify({"error": "Invalid action"}), 400

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if file:
        filename = secure_filename(file.filename)
        # Save to INBOX (Auto Pilot)
        save_path = config.INBOX_DIR / "01_AUTO_PILOT" / "Modern_Look" / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(save_path)
        logger.info(f"📥 Uploaded file: {filename}")
        return jsonify({"success": True, "filename": filename})

@app.route('/api/surgical/segments', methods=['GET'])
def get_segments():
    stem = request.args.get('stem')
    if not stem: return jsonify({"error": "Missing stem"}), 400
    
    # Try APPROVED first, then TRANSLATED
    paths = [
        config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json",
        config.TRANSLATED_DONE_DIR / f"{stem}_ICELANDIC.json", # Legacy
        config.TRANSLATED_DONE_DIR / f"{stem}_is.json" # New standard
    ]
    
    for p in paths:
        if p.exists():
            try:
                with open(p, 'r') as f:
                    data = json.load(f)
                    # Normalize if it's the old format with "translated_data"
                    if isinstance(data, dict):
                        if "translated_data" in data:
                            data = data["translated_data"]
                        elif "segments" in data:
                            data = data["segments"]
                    
                    # FETCH SOURCE TEXT
                    try:
                        source_path = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
                        if not source_path.exists():
                            source_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
                        
                        if source_path.exists():
                            with open(source_path, 'r') as f:
                                source_data = json.load(f)
                                # Handle wrapper
                                if isinstance(source_data, dict) and "segments" in source_data:
                                    source_data = source_data["segments"]
                                
                                source_map = {s['id']: s['text'] for s in source_data if 'id' in s}
                                
                                # Merge
                                for seg in data:
                                    if 'id' in seg and seg['id'] in source_map:
                                        seg['source_text'] = source_map[seg['id']]
                    except Exception as e:
                        logger.warning(f"Failed to load source text for {stem}: {e}")

                    return jsonify({"segments": data, "source": p.name})
            except Exception as e:
                return jsonify({"error": str(e)}), 500
                
    return jsonify({"error": "No editable file found"}), 404

@app.route('/api/stream/<stem>')
def stream_proxy(stem):
    """Streams the proxy video if it exists."""
    proxy_path = config.PROXIES_DIR / f"{stem}_PROXY.mp4"
    if not proxy_path.exists():
        return "Proxy not found", 404
        
    # Simple file serving for now (Flask handles range requests automatically with send_file usually, 
    # but for true seeking we might need a more robust solution later. 
    # For local dev, send_file is often enough).
    return send_file(proxy_path, mimetype='video/mp4')

@app.route('/api/surgical/save', methods=['POST'])
def save_segments():
    data = request.json
    stem = data.get('stem')
    segments = data.get('segments')
    
    if not stem or not segments:
        return jsonify({"error": "Missing data"}), 400
        
    try:
        # 1. Determine Path (Always save to APPROVED for finalizer)
        output_path = config.EDITOR_DIR / f"{stem}_APPROVED.json"
        
        # 2. Backup if exists
        if output_path.exists():
            backup_path = output_path.with_suffix(f".json.bak_{int(time.time())}")
            shutil.copy(output_path, backup_path)
            
        # 3. Save New Content
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(segments, f, indent=2, ensure_ascii=False)
            
        # 4. Auto-Finalize
        from workers import finalizer
        
        # Get language from DB
        job = omega_db.get_job(stem)
        lang = job.get('target_language', 'is') if job else 'is'
        
        finalizer.finalize(output_path, target_language=lang)
        
        return jsonify({"success": True})
        
    except Exception as e:
        logger.error(f"Surgical Save Failed: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Ensure DB exists
    omega_db.init_db()
    # Run server (Disable reloader to prevent zombie processes)
    app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)
