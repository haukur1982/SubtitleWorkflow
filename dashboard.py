from flask import Flask, render_template, jsonify, request
import sqlite3
import json
import shutil
import time
from pathlib import Path
from werkzeug.utils import secure_filename
import omega_db
import subprocess
import config
import logging

# Configure Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

app = Flask(__name__)

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
        # Reset to FINALIZED stage (triggers Publisher)
        omega_db.update(file_stem, stage="FINALIZED", status="Manual Burn", progress=80.0)
        return jsonify({"success": True, "message": f"Triggered Burn for {file_stem}"})
        
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
