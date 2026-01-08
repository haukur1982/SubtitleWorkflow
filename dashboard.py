
from flask import Flask, render_template, jsonify, request, send_file, Response
import os
import sqlite3
import json
from workers.dubber import Dubber
import shutil
import time
import threading
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename
import omega_db
import subprocess
import sys
import config
import logging
import secrets
from functools import wraps
from typing import Optional

# Configure Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Omega-Admin-Token"
    return response

_force_burn_lock = threading.Lock()
_force_burn_inflight = set()

_ADMIN_TOKEN_ENV = "OMEGA_ADMIN_TOKEN"
_MANAGER_RESTART_FLAG = config.BASE_DIR / "heartbeats" / "omega_manager.restart"

@app.before_request
def _dashboard_heartbeat():
    try:
        beat_dir = config.BASE_DIR / "heartbeats"
        beat_dir.mkdir(exist_ok=True)
        (beat_dir / "dashboard.beat").touch()
    except Exception:
        pass

def _heartbeat_loop():
    while True:
        try:
            beat_dir = config.BASE_DIR / "heartbeats"
            beat_dir.mkdir(exist_ok=True)
            (beat_dir / "dashboard.beat").touch()
        except Exception:
            pass
        time.sleep(10)

def _start_heartbeat_thread():
    thread = threading.Thread(target=_heartbeat_loop, name="dashboard-heartbeat", daemon=True)
    thread.start()


def _is_loopback(addr: Optional[str]) -> bool:
    if not addr:
        return False
    if addr == "::1":
        return True
    if addr.startswith("127."):
        return True
    return addr == "localhost"


def _get_request_admin_token() -> Optional[str]:
    token = request.headers.get("X-Omega-Admin-Token")
    if token:
        return token.strip()

    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()

    token = request.args.get("admin_token")
    if token:
        return str(token).strip()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        token = payload.get("admin_token")
        if token:
            return str(token).strip()
    return None


def _is_admin_request() -> bool:
    """
    Admin policy:
    - Always allow loopback requests (127.0.0.1 / ::1).
    - For non-loopback requests, require OMEGA_ADMIN_TOKEN to be set and supplied.
    """
    remote = request.remote_addr
    if _is_loopback(remote):
        return True

    configured = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    if not configured:
        return False

    provided = _get_request_admin_token()
    return bool(provided) and secrets.compare_digest(provided, configured)


def admin_required(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        if not _is_admin_request():
            return jsonify({"error": "Admin access required"}), 403
        return fn(*args, **kwargs)

    return _wrapped


def _tail_lines(path: Path, line_count: int = 200, max_bytes: int = 200_000) -> list[str]:
    if line_count <= 0:
        return []
    try:
        if not path.exists():
            return []
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return lines[-line_count:]
    except Exception:
        return []


def _heartbeat_age_seconds(process_name: str) -> Optional[float]:
    try:
        beat = config.BASE_DIR / "heartbeats" / f"{process_name}.beat"
        if not beat.exists():
            return None
        return max(0.0, time.time() - beat.stat().st_mtime)
    except Exception:
        return None


def _disk_free_gb(path: Path) -> Optional[float]:
    try:
        if not path.exists():
            return None
        total, used, free = shutil.disk_usage(str(path))
        return free / (2**30)
    except Exception:
        return None


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
        stem = (job.get("file_stem") or "").strip()
        if stem.startswith("._"):
            continue
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

        # Resolve SRT (prefer delivery; otherwise copy from vault; check DONE_ prefix; legacy fallback to SRT_DIR)
        srt_path = None
        delivery_srt = delivery_dir / f"{file_stem}.srt"
        done_delivery_srt = delivery_dir / f"DONE_{file_stem}.srt"  # Completed jobs have DONE_ prefix
        
        if delivery_srt.exists():
            srt_path = delivery_srt
        elif done_delivery_srt.exists():
            # Restore DONE_ prefixed SRT back to original name for re-burn
            shutil.copy2(done_delivery_srt, delivery_srt)
            srt_path = delivery_srt
            logger.info(f"   ♻️ Restored DONE_ SRT: {done_delivery_srt} -> {delivery_srt}")
        elif vault_dir is not None:
            vault_srt = vault_dir / f"{file_stem}.srt"
            if vault_srt.exists():
                shutil.copy2(vault_srt, delivery_srt)
                srt_path = delivery_srt
        
        if srt_path is None:
            legacy_srt = config.SRT_DIR / f"{file_stem}.srt"
            done_legacy_srt = config.SRT_DIR / f"DONE_{file_stem}.srt"
            if legacy_srt.exists():
                srt_path = legacy_srt
            elif done_legacy_srt.exists():
                # Restore DONE_ prefixed SRT
                shutil.copy2(done_legacy_srt, legacy_srt)
                srt_path = legacy_srt
                logger.info(f"   ♻️ Restored DONE_ SRT: {done_legacy_srt} -> {legacy_srt}")
        
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

        # Backup existing output so publisher will re-encode
        existing_output = delivery_dir / f"{file_stem}_SUBBED.mp4"
        if existing_output.exists():
            backup_name = f"{file_stem}_SUBBED.bak_{int(time.time())}.mp4"
            backup_path = delivery_dir / backup_name
            shutil.move(str(existing_output), str(backup_path))
            logger.info(f"   📦 Backed up existing output: {backup_name}")

        from workers import publisher
        output_path = publisher.publish(video_path, srt_path, subtitle_style=subtitle_style)
        logger.info(f"✅ Force burn complete: {output_path}")

        completed_at = datetime.now().isoformat()
        omega_db.update(
            file_stem,
            stage="COMPLETED",
            status="Done",
            progress=100.0,
            meta={
                "burn_completed_at": completed_at,
                "burn_end_time": completed_at,
                "final_output": str(output_path),
            },
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

@app.route('/api/jobs_grouped')
def api_jobs_grouped():
    """Get jobs grouped by client with urgent section."""
    from datetime import datetime, timedelta
    
    jobs = get_all_jobs()
    now = datetime.now()
    urgent_threshold = now + timedelta(days=2)
    
    urgent = []
    by_client = {}
    
    for job in jobs:
        # Check if urgent (due within 48 hours)
        due_date_str = job.get("due_date")
        is_urgent = False
        if due_date_str:
            try:
                due_date = datetime.fromisoformat(due_date_str)
                is_urgent = due_date <= urgent_threshold
            except:
                pass
        
        if is_urgent:
            urgent.append(job)
        
        # Group by client
        client = job.get("client", "unknown")
        if client not in by_client:
            by_client[client] = []
        by_client[client].append(job)
    
    return jsonify({
        "urgent": urgent,
        "by_client": by_client,
        "client_names": sorted(by_client.keys())
    })

@app.route('/api/mark_delivered', methods=['POST'])
def api_mark_delivered():
    """Mark a job as delivered using delivery templates."""
    try:
        from delivery_actions import mark_delivered
        
        data = request.json
        job_stem = data.get("job_stem")
        notes = data.get("notes", "")
        
        if not job_stem:
            return jsonify({"success": False, "error": "job_stem required"}), 400
        
        result = mark_delivered(job_stem, notes)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Mark delivered failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/deliveries')
def api_get_deliveries():
    """Fetch delivery history for analytics."""
    try:
        limit = int(request.args.get("limit", 100))
        deliveries = omega_db.get_deliveries() # It defaults to 100 limit, checking args if I modify omega_db
        # Actually omega_db.get_deliveries() hardcodes LIMIT 100.
        return jsonify(deliveries)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health")
def api_health():
    jobs = get_all_jobs()
    stage_counts: dict[str, int] = {}
    halted_count = 0
    dead_count = 0
    for job in jobs:
        stage = (job.get("stage") or "UNKNOWN").upper()
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        meta = job.get("meta") or {}
        if isinstance(meta, dict) and meta.get("halted"):
            halted_count += 1
        if stage == "DEAD":
            dead_count += 1

    storage_ready = False
    try:
        storage_ready = bool(config.critical_paths_ready(require_write=True))
    except Exception:
        storage_ready = False

    manager_age = _heartbeat_age_seconds("omega_manager")
    dashboard_age = _heartbeat_age_seconds("dashboard")

    return jsonify(
        {
            "time": datetime.now().isoformat(),
            "storage_ready": storage_ready,
            "disk_free_gb": _disk_free_gb(config.DELIVERY_DIR),
            "heartbeats": {
                "omega_manager_age_seconds": manager_age,
                "dashboard_age_seconds": dashboard_age,
            },
            "jobs": {
                "total": len(jobs),
                "stages": stage_counts,
                "halted": halted_count,
                "dead": dead_count,
            },
            "publish": {
                "video_bitrate": getattr(config, "PUBLISH_VIDEO_BITRATE", None),
                "video_maxrate": getattr(config, "PUBLISH_VIDEO_MAXRATE", None),
                "video_bufsize": getattr(config, "PUBLISH_VIDEO_BUFSIZE", None),
                "x264_preset": getattr(config, "PUBLISH_X264_PRESET", None),
                "audio_codec": getattr(config, "PUBLISH_AUDIO_CODEC", None),
            },
        }
    )


@app.route("/api/encoding_status")
def api_encoding_status():
    """
    Returns the status of any currently encoding (BURNING stage) jobs.
    Used by the dashboard to display the encoding progress banner.
    """
    jobs = get_all_jobs()
    encoding_jobs = []
    
    for job in jobs:
        stage = (job.get("stage") or "").upper()
        if stage != "BURNING":
            continue
            
        meta = job.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}
        
        # Get encoding info
        stem = job.get("file_stem", "Unknown")
        status = job.get("status", "Encoding...")
        delivery_profile = job.get("delivery_profile") or "broadcast_hevc"
        
        # Get profile display name
        profile_info = config.DELIVERY_PROFILES.get(delivery_profile, {})
        profile_name = profile_info.get("name", delivery_profile)
        
        # Calculate elapsed time if we have burn_started_at
        burn_started = meta.get("burn_started_at")
        elapsed_seconds = None
        if burn_started:
            try:
                start_time = datetime.fromisoformat(burn_started)
                elapsed_seconds = (datetime.now() - start_time).total_seconds()
            except:
                pass
        
        encoding_jobs.append({
            "stem": stem,
            "status": status,
            "profile": profile_name,
            "profile_key": delivery_profile,
            "started_at": burn_started,
            "elapsed_seconds": elapsed_seconds,
            "progress": job.get("progress", 95.0),
        })
    
    return jsonify({
        "encoding": len(encoding_jobs) > 0,
        "jobs": encoding_jobs,
        "count": len(encoding_jobs),
    })


@app.route("/api/logs")
@admin_required
def api_logs():
    name = (request.args.get("name") or "").strip().lower()
    try:
        lines = int(request.args.get("lines") or 200)
    except Exception:
        lines = 200
    lines = max(1, min(lines, 2000))

    log_map = {
        "manager": config.BASE_DIR / "logs" / "manager.log",
        "dashboard": config.BASE_DIR / "logs" / "dashboard.log",
    }
    path = log_map.get(name)
    if not path:
        return jsonify({"error": "Invalid log name"}), 400
    return jsonify({"name": name, "path": str(path), "lines": _tail_lines(path, line_count=lines)})


@app.route("/api/output/<stem>")
@admin_required
def api_output(stem: str):
    job = omega_db.get_job(stem) or {}
    meta = job.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    candidates: list[Path] = []
    final_output = meta.get("final_output")
    if final_output:
        candidates.append(Path(str(final_output)))

    vault_path = job.get("vault_path") or meta.get("vault_path")
    if vault_path:
        try:
            delivery_dir = _derive_delivery_dir_from_vault(Path(str(vault_path)))
            candidates.append(delivery_dir / f"{stem}_SUBBED.mp4")
        except Exception:
            pass

    candidates.append(config.VIDEO_DIR / f"{stem}_SUBBED.mp4")

    output_path = next((p for p in candidates if p.exists()), None)
    if not output_path:
        return jsonify({"error": "Output not found"}), 404

    try:
        resolved = output_path.resolve(strict=False)
        delivery_root = config.DELIVERY_DIR.resolve(strict=False)
        try:
            resolved.relative_to(delivery_root)
        except Exception:
            return jsonify({"error": "Refusing to serve path outside delivery"}), 403
    except Exception:
        return jsonify({"error": "Invalid output path"}), 400

    return send_file(str(output_path), mimetype="video/mp4", as_attachment=True, download_name=output_path.name)


@app.route("/metrics")
def metrics():
    jobs = get_all_jobs()
    stage_counts: dict[str, int] = {}
    for job in jobs:
        stage = (job.get("stage") or "UNKNOWN").upper()
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    manager_age = _heartbeat_age_seconds("omega_manager")
    storage_ready = 0
    try:
        storage_ready = 1 if config.critical_paths_ready(require_write=True) else 0
    except Exception:
        storage_ready = 0

    lines: list[str] = []
    lines.append("# HELP omega_storage_ready Storage paths ready/writable (1/0)")
    lines.append("# TYPE omega_storage_ready gauge")
    lines.append(f"omega_storage_ready {storage_ready}")
    lines.append("# HELP omega_jobs_total Total jobs in DB")
    lines.append("# TYPE omega_jobs_total gauge")
    lines.append(f"omega_jobs_total {len(jobs)}")
    lines.append("# HELP omega_jobs_stage_total Jobs by stage")
    lines.append("# TYPE omega_jobs_stage_total gauge")
    for stage, count in sorted(stage_counts.items()):
        lines.append(f'omega_jobs_stage_total{{stage="{stage}"}} {count}')
    if manager_age is not None:
        lines.append("# HELP omega_manager_heartbeat_age_seconds Seconds since manager heartbeat")
        lines.append("# TYPE omega_manager_heartbeat_age_seconds gauge")
        lines.append(f"omega_manager_heartbeat_age_seconds {manager_age:.3f}")

    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; version=0.0.4"}

from workers.forker import Forker

@app.route('/api/action/fork', methods=['POST'])
@admin_required
def api_fork():
    """Fork a job into multiple target languages."""
    try:
        data = request.json
        job_id = data.get("jobId")
        languages = data.get("languages", [])
        
        if not job_id: return jsonify({"error": "No jobId"}), 400
        if not languages: return jsonify({"error": "No languages provided"}), 400
        
        logger.info(f"Forking {job_id} into {languages}")
        
        forker = Forker(job_id)
        children = forker.fork(languages)
        
        return jsonify({
            "success": True, 
            "message": f"Created {len(children)} child jobs",
            "children": children
        })
    except Exception as e:
        logger.error(f"Fork failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/action/dub', methods=['POST'])
def api_dub():
    """Trigger AI Dubbing for a job."""
    try:
        data = request.json
        job_id = data.get("jobId") # file_stem
        voice = data.get("voice", "alloy")
        
        if not job_id:
            return jsonify({"error": "No jobId provided"}), 400
            
        # Get Job from DB
        job = omega_db.get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
            
        # We assume job structure: jobs/<file_stem>
        # Ensure we use absolute path
        job_dir = (Path("jobs") / job_id).resolve()
        
        def run_dubbing():
            try:
                logger.info(f"Starting dubbing for {job_id}")
                omega_db.update(job_id, status=f"Dubbing ({voice})")
                
                # Update Dubber to support voice selection if needed
                # For now, Dubber uses OpenAITTSProvider default
                dubber = Dubber(job_id, job_dir)
                dubber.run()
                
                omega_db.update(job_id, status="Dubbing Complete")
            except Exception as e:
                logger.error(f"Dubbing failed: {e}")
                omega_db.update(job_id, status="Dubbing Failed")

        thread = threading.Thread(target=run_dubbing)
        thread.start()

        return jsonify({"success": True, "message": "Dubbing started"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/action', methods=['POST'])
@admin_required
def api_action():
    """Handle surgical actions."""
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    file_stem = data.get('file_stem')
    logger.info(f"👉 API ACTION RECEIVED: {action} for {file_stem}")

    if action == "restart_manager":
        try:
            _MANAGER_RESTART_FLAG.parent.mkdir(exist_ok=True)
            _MANAGER_RESTART_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        except Exception as e:
            return jsonify({"error": f"Failed to write restart flag: {e}"}), 500

        # If the manager looks down, attempt to start it.
        started = False
        age = _heartbeat_age_seconds("omega_manager")
        if age is None or age > 30:
            try:
                mgr_path = config.BASE_DIR / "omega_manager.py"
                python_bin = os.environ.get("OMEGA_PYTHON") or sys.executable
                subprocess.Popen(
                    [python_bin, str(mgr_path)],
                    cwd=str(config.BASE_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                started = True
            except Exception as e:
                return jsonify({"error": f"Failed to start manager: {e}"}), 500

        msg = "Restart requested; manager will restart when idle."
        if started:
            msg = "Manager started and restart requested."
        return jsonify({"success": True, "message": msg})
    
    if not file_stem:
        return jsonify({"error": "Missing file_stem"}), 400

    if action == "reset_review":
        # Reset to REVIEWED stage (triggers Finalizer)
        omega_db.update(file_stem, stage="REVIEWED", status="Manual Reset", progress=70.0)
        return jsonify({"success": True, "message": f"Reset {file_stem} to Review"})

    elif action == "retry_translate":
        skel = config.VAULT_DATA / f"{file_stem}_SKELETON.json"
        skel_done = config.VAULT_DATA / f"{file_stem}_SKELETON_DONE.json"
        if not skel.exists():
            if skel_done.exists():
                shutil.copy2(skel_done, skel)
            else:
                return jsonify({"error": f"Skeleton not found for {file_stem}"}), 404

        omega_db.update(
            file_stem,
            stage="TRANSCRIBED",
            status="Manual Retry: Translation",
            progress=30.0,
            meta={"halted": False, "manual_retry_translate_at": datetime.now().isoformat()},
        )
        return jsonify({"success": True, "message": f"Retry translate queued for {file_stem}"})

    elif action == "retry_review":
        job = omega_db.get_job(file_stem) or {}
        lang = (job.get("target_language") or "is").lower()
        trans_path = config.EDITOR_DIR / f"{file_stem}_{lang.upper()}.json"

        if not trans_path.exists():
            # Reconstruct a review input file from the best available artifacts.
            src_path = config.VAULT_DATA / f"{file_stem}_SKELETON_DONE.json"
            if not src_path.exists():
                src_path = config.VAULT_DATA / f"{file_stem}_SKELETON.json"
            if not src_path.exists():
                return jsonify({"error": f"Source skeleton not found for {file_stem}"}), 404

            with open(src_path, "r", encoding="utf-8") as f:
                src_wrapper = json.load(f)
            source_data = src_wrapper.get("segments", src_wrapper) if isinstance(src_wrapper, dict) else src_wrapper
            if not isinstance(source_data, list):
                return jsonify({"error": f"Invalid skeleton format for {file_stem}"}), 400

            approved_path = config.TRANSLATED_DONE_DIR / f"{file_stem}_APPROVED.json"
            if not approved_path.exists():
                return jsonify({"error": f"Approved file not found for {file_stem}"}), 404

            with open(approved_path, "r", encoding="utf-8") as f:
                approved_wrapper = json.load(f)
            translated_data = (
                approved_wrapper.get("segments", approved_wrapper)
                if isinstance(approved_wrapper, dict)
                else approved_wrapper
            )
            if not isinstance(translated_data, list):
                return jsonify({"error": f"Invalid approved format for {file_stem}"}), 400

            payload = {"source_data": source_data, "translated_data": translated_data}
            trans_path.parent.mkdir(parents=True, exist_ok=True)
            with open(trans_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        omega_db.update(
            file_stem,
            stage="TRANSLATED",
            status="Manual Retry: Review",
            progress=55.0,
            meta={"halted": False, "translation_path": str(trans_path), "manual_retry_review_at": datetime.now().isoformat()},
        )
        return jsonify({"success": True, "message": f"Retry review queued for {file_stem}"})

    elif action == "unhalt_job":
        job = omega_db.get_job(file_stem)
        if not job:
            return jsonify({"error": f"Job not found: {file_stem}"}), 404

        meta = job.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        now = datetime.now().isoformat()

        # 1) Completed output exists?
        output_candidates: list[Path] = []
        final_output = meta.get("final_output")
        if final_output:
            output_candidates.append(Path(str(final_output)))

        vault_path = job.get("vault_path") or meta.get("vault_path")
        if vault_path:
            try:
                delivery_dir = _derive_delivery_dir_from_vault(Path(str(vault_path)))
                output_candidates.append(delivery_dir / f"{file_stem}_SUBBED.mp4")
            except Exception:
                pass
        output_candidates.append(config.VIDEO_DIR / f"{file_stem}_SUBBED.mp4")

        output_path = next((p for p in output_candidates if p.exists()), None)
        if output_path:
            omega_db.update(
                file_stem,
                stage="COMPLETED",
                status="Done",
                progress=100.0,
                meta={"halted": False, "unhalted_at": now, "final_output": str(output_path)},
            )
            return jsonify({"success": True, "message": f"Unhalted {file_stem} (already completed)"})

        # 2) SRT exists -> ready to burn
        srt_path = config.SRT_DIR / f"{file_stem}.srt"
        if srt_path.exists():
            omega_db.update(
                file_stem,
                stage="FINALIZED",
                status="Ready to Burn",
                progress=90.0,
                meta={"halted": False, "unhalted_at": now},
            )
            return jsonify({"success": True, "message": f"Unhalted {file_stem} (resume at FINALIZED)"})

        # 3) Approved exists -> ready to finalize
        approved = config.TRANSLATED_DONE_DIR / f"{file_stem}_APPROVED.json"
        if approved.exists():
            omega_db.update(
                file_stem,
                stage="REVIEWED",
                status="Editor Approved",
                progress=70.0,
                meta={"halted": False, "unhalted_at": now},
            )
            return jsonify({"success": True, "message": f"Unhalted {file_stem} (resume at REVIEWED)"})

        # 4) Skeleton exists -> ready to translate
        skel = config.VAULT_DATA / f"{file_stem}_SKELETON.json"
        skel_done = config.VAULT_DATA / f"{file_stem}_SKELETON_DONE.json"
        if not skel.exists() and skel_done.exists():
            try:
                shutil.copy2(skel_done, skel)
            except Exception:
                pass

        omega_db.update(
            file_stem,
            stage="TRANSCRIBED",
            status="Ready for Translation",
            progress=30.0,
            meta={"halted": False, "unhalted_at": now},
        )
        return jsonify({"success": True, "message": f"Unhalted {file_stem} (resume at TRANSCRIBED)"})
        
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
        omega_db.update(
            file_stem,
            status="Approved for Burn",
            meta={
                "burn_approved": True,
                "burn_approved_at": datetime.now().isoformat(),
            },
        )
        return jsonify({"success": True, "message": f"Approved Burn for {file_stem}"})

    elif action == "set_mode":
        mode = data.get('mode')
        if mode not in ["AUTO", "REVIEW"]:
             return jsonify({"error": "Invalid mode"}), 400
        omega_db.update(
            file_stem,
            meta={
                "mode": mode,
                "review_required": mode == "REVIEW",
                "mode_set_at": datetime.now().isoformat(),
            },
        )
        return jsonify({"success": True, "message": f"Mode set to {mode}"})

    elif action == "delete_job":
        omega_db.delete(file_stem)
        return jsonify({"success": True, "message": f"Deleted job {file_stem}"})

    elif action == "re_burn":
        logger.info(f"🔄 Re-Burn Triggered for {file_stem}")
        
        # Extract delivery profile from request (optional)
        delivery_profile = data.get('delivery_profile')
        if delivery_profile:
            logger.info(f"   📦 Delivery Profile: {delivery_profile}")
        
        # 1. Backup existing output (so manager doesn't auto-complete it)
        try:
            output_path = config.VIDEO_DIR / f"{file_stem}_SUBBED.mp4"
            logger.info(f"   Checking Video: {output_path} (Exists: {output_path.exists()})")
            if output_path.exists():
                backup_name = f"{file_stem}_SUBBED.bak_{int(time.time())}.mp4"
                backup_path = config.VIDEO_DIR / backup_name
                shutil.move(str(output_path), str(backup_path))
                logger.info(f"   📦 Backed up old video to {backup_name}")
        except Exception as e:
            logger.error(f"   ❌ Backup failed: {e}")
            return jsonify({"error": f"Failed to backup output: {e}"}), 500

        # 2. Restore SRT (if it was moved to DONE_)
        srt_path = config.SRT_DIR / f"{file_stem}.srt"
        done_srt_path = config.SRT_DIR / f"DONE_{file_stem}.srt"
        logger.info(f"   Checking SRT: {srt_path} (Exists: {srt_path.exists()})")
        logger.info(f"   Checking DONE_SRT: {done_srt_path} (Exists: {done_srt_path.exists()})")
        
        if not srt_path.exists() and done_srt_path.exists():
            try:
                shutil.move(str(done_srt_path), str(srt_path))
                logger.info(f"   ♻️ Restored SRT for {file_stem}")
            except Exception as e:
                logger.error(f"   ❌ Restore failed: {e}")
                return jsonify({"error": f"Failed to restore SRT: {e}"}), 500
        
        # 3. Reset DB Status to FINALIZED (Ready to Burn)
        # Also save delivery_profile if provided
        logger.info(f"   📝 Updating DB for {file_stem}...")
        try:
            update_kwargs = {
                "stage": "FINALIZED",
                "status": "Queued for Re-Burn",
                "progress": 90.0,
                "meta": {
                    "burn_completed_at": None,
                    "final_output": None,
                    "burn_approved": True,
                    "reburn_requested_at": datetime.now().isoformat(),
                    "halted": False 
                }
            }
            # Save delivery_profile to job record
            if delivery_profile:
                update_kwargs["delivery_profile"] = delivery_profile
            
            omega_db.update(file_stem, **update_kwargs)
            logger.info("   ✅ DB Updated successfully")
        except Exception as e:
            logger.error(f"   ❌ DB Update failed: {e}")
            return jsonify({"error": f"DB Update failed: {e}"}), 500

        return jsonify({"success": True, "message": f"Queued {file_stem} for Re-Burn"})

    return jsonify({"error": "Invalid action"}), 400

@app.route('/api/upload', methods=['POST', 'OPTIONS'])
@admin_required
def upload_file():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'})

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if file:
        filename = secure_filename(file.filename)
        # Save to INBOX (Auto Pilot)
        save_path = config.INBOX_DIR / "01_AUTO_PILOT" / "Classic" / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(save_path)
        logger.info(f"📥 Uploaded file: {filename}")
        return jsonify({"success": True, "filename": filename})

@app.route('/api/smart_upload', methods=['POST'])
@admin_required
def smart_upload():
    """
    Smart file upload that handles multiple file combinations:
    - full_pipeline: Video only → Transcribe → Translate → Burn
    - quick_burn: Video + SRT → Skip to Finalize → Burn
    - skip_transcription: Video + Transcript → Translate → Burn
    - srt_update: SRT only → Update existing job → Re-Burn
    """
    mode = request.form.get('mode', 'full_pipeline')
    logger.info(f"📥 Smart Upload: mode={mode}")
    
    # Collect all uploaded files
    files = []
    for key in request.files:
        if key.startswith('file_'):
            files.append(request.files[key])
    
    if not files:
        return jsonify({"error": "No files uploaded"}), 400
    
    VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.wmv'}
    SRT_EXTS = {'.srt'}
    TRANSCRIPT_EXTS = {'.txt', '.json'}
    
    video_files = [f for f in files if any(f.filename.lower().endswith(ext) for ext in VIDEO_EXTS)]
    srt_files = [f for f in files if any(f.filename.lower().endswith(ext) for ext in SRT_EXTS)]
    transcript_files = [f for f in files if any(f.filename.lower().endswith(ext) for ext in TRANSCRIPT_EXTS)]
    
    try:
        if mode == 'quick_burn' and video_files and srt_files:
            # Video + SRT → Skip to burn
            video_file = video_files[0]
            srt_file = srt_files[0]
            
            video_filename = secure_filename(video_file.filename)
            srt_filename = secure_filename(srt_file.filename)
            stem = Path(video_filename).stem
            
            # Save video to vault
            video_path = config.VAULT_VIDEOS / video_filename
            config.VAULT_VIDEOS.mkdir(parents=True, exist_ok=True)
            video_file.save(video_path)
            logger.info(f"   Saved video: {video_path}")
            
            # Save SRT to delivery folder (ready for burn)
            srt_dest = config.SRT_DIR / f"{stem}.srt"
            config.SRT_DIR.mkdir(parents=True, exist_ok=True)
            srt_file.save(srt_dest)
            logger.info(f"   Saved SRT: {srt_dest}")
            
            # Create job at FINALIZED stage
            omega_db.upsert(
                stem,
                stage="FINALIZED",
                status="Quick Burn (Video+SRT)",
                progress=90.0,
                target_language="is",
                subtitle_style="Classic",
                meta={
                    "original_filename": video_filename,
                    "quick_burn": True,
                    "external_srt": True,
                    "uploaded_at": datetime.now().isoformat()
                }
            )
            
            return jsonify({
                "success": True, 
                "message": f"Quick Burn queued: {stem}",
                "stem": stem,
                "mode": "quick_burn"
            })
            
        elif mode == 'srt_update' and srt_files:
            # SRT only → Update existing job
            srt_file = srt_files[0]
            srt_filename = secure_filename(srt_file.filename)
            stem = Path(srt_filename).stem
            
            # Check if job exists
            existing = omega_db.get_job(stem)
            if not existing:
                return jsonify({"error": f"No existing job found for {stem}"}), 404
            
            # Backup old SRT
            old_srt = config.SRT_DIR / f"{stem}.srt"
            if old_srt.exists():
                backup = config.SRT_DIR / f"{stem}.srt.bak_{int(time.time())}"
                shutil.copy2(old_srt, backup)
            
            # Save new SRT
            srt_file.save(old_srt)
            logger.info(f"   Updated SRT: {old_srt}")
            
            # Update job to re-burn
            omega_db.update(
                stem,
                stage="FINALIZED",
                status="SRT Updated - Re-Burn",
                progress=90.0,
                meta={
                    "srt_updated_at": datetime.now().isoformat(),
                    "final_output": None  # Clear to trigger re-burn
                }
            )
            
            return jsonify({
                "success": True,
                "message": f"SRT updated for {stem}, queued for re-burn",
                "stem": stem,
                "mode": "srt_update"
            })
            
        elif mode == 'skip_transcription' and video_files and transcript_files:
            # Video + Transcript → Skip transcription
            video_file = video_files[0]
            transcript_file = transcript_files[0]
            
            video_filename = secure_filename(video_file.filename)
            transcript_filename = secure_filename(transcript_file.filename)
            stem = Path(video_filename).stem
            
            # Save video
            video_path = config.VAULT_VIDEOS / video_filename
            config.VAULT_VIDEOS.mkdir(parents=True, exist_ok=True)
            video_file.save(video_path)
            
            # Save transcript as skeleton
            skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
            config.VAULT_DATA.mkdir(parents=True, exist_ok=True)
            
            # Read and convert transcript
            transcript_content = transcript_file.read().decode('utf-8')
            if transcript_filename.endswith('.json'):
                # Assume already in skeleton format
                with open(skeleton_path, 'w', encoding='utf-8') as f:
                    f.write(transcript_content)
            else:
                # Plain text - wrap in skeleton format
                skeleton = {
                    "meta": {"stem": stem, "source": "external_transcript"},
                    "segments": [{"id": 1, "start": 0, "end": 0, "text": transcript_content}]
                }
                with open(skeleton_path, 'w', encoding='utf-8') as f:
                    json.dump(skeleton, f, ensure_ascii=False, indent=2)
            
            # Create job at TRANSCRIBED stage
            omega_db.upsert(
                stem,
                stage="TRANSCRIBED",
                status="External Transcript - Ready to Translate",
                progress=30.0,
                target_language="is",
                subtitle_style="Classic",
                meta={
                    "original_filename": video_filename,
                    "external_transcript": True,
                    "uploaded_at": datetime.now().isoformat()
                }
            )
            
            return jsonify({
                "success": True,
                "message": f"Transcript imported for {stem}, ready for translation",
                "stem": stem,
                "mode": "skip_transcription"
            })
            
        else:
            # Full pipeline - save video to INBOX
            video_file = video_files[0] if video_files else files[0]
            video_filename = secure_filename(video_file.filename)
            
            save_path = config.INBOX_DIR / "01_AUTO_PILOT" / "Classic" / video_filename
            save_path.parent.mkdir(parents=True, exist_ok=True)
            video_file.save(save_path)
            
            logger.info(f"   Full pipeline: {save_path}")
            
            return jsonify({
                "success": True,
                "message": f"Full pipeline started: {video_filename}",
                "mode": "full_pipeline"
            })
            
    except Exception as e:
        logger.error(f"Smart upload error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/surgical/segments', methods=['GET'])
@admin_required
def get_segments():
    stem = request.args.get('stem')
    if not stem: return jsonify({"error": "Missing stem"}), 400
    
    # Try APPROVED first, then TRANSLATED
    paths = [
        config.SRT_DIR / f"{stem}_normalized.json", # Finalized output (Best)
        config.SRT_DIR / f"DONE_{stem}_normalized.json", # Completed output
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
                        elif "events" in data:
                             # Transform 'events' (start, end, lines) to 'segments' (id, start, end, text)
                             raw_events = data["events"]
                             data = []
                             for idx, ev in enumerate(raw_events):
                                 data.append({
                                     "id": idx + 1,
                                     "start": ev["start"],
                                     "end": ev["end"],
                                     "text": "\n".join(ev.get("lines", []))
                                 })
                    
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
@admin_required
def save_segments():
    data = request.json
    stem = data.get('stem')
    segments = data.get('segments')
    
    if not stem or not segments:
        return jsonify({"error": "Missing data"}), 400
        
    try:
        # 1. Save back to the canonical approved file
        output_path = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
        
        # 2. Backup if exists
        if output_path.exists():
            backup_path = output_path.with_suffix(f".json.bak_{int(time.time())}")
            shutil.copy(output_path, backup_path)
            
        # 3. Save New Content
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({"segments": segments, "meta": {"edited_via": "dashboard", "edited_at": datetime.now().isoformat()}}, f, indent=2, ensure_ascii=False)
            
        # 4. Auto-Finalize
        from workers import finalizer
        
        # Get language from DB
        job = omega_db.get_job(stem)
        lang = job.get('target_language', 'is') if job else 'is'
        
        srt_path, normalized_path = finalizer.finalize(output_path, target_language=lang)
        omega_db.update(
            stem,
            stage="FINALIZED",
            status="Ready to Burn",
            progress=90.0,
            meta={
                "surgical_edit_at": datetime.now().isoformat(),
                "srt_path": str(srt_path),
                "normalized_path": str(normalized_path),
            },
        )
        
        return jsonify({"success": True})
        
    except Exception as e:
        logger.error(f"Surgical Save Failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/assistant/chat', methods=['POST'])
@admin_required
def api_assistant_chat():
    """
    Interact with the Omega Assistant (Gemini).
    """
    try:
        data = request.json
        job_id = data.get('job_id')
        message = data.get('message')
        history = data.get('history', [])
        
        if not job_id or not message:
            return jsonify({"error": "Missing job_id or message"}), 400
            
        from workers import assistant
        
        # Run Assistant
        # TODO: Move to thread if slow, but text-only is usually fast enough (2-5s)
        # for Flash model.
        result = assistant.chat_with_job(job_id, message, history)
        
        if result.get("edits_performed"):
            # If AI modified the file, we must re-finalize to update SRT/Preview
            from workers import finalizer
            stem = job_id
            
            # Find the file that was edited (Assistant edits APPROVED or SKELETON)
            # We assume APPROVED for finalized jobs
            approved_path = config.VAULT_DATA / f"{stem}_APPROVED.json"
            
            if approved_path.exists():
                 # Get language from DB
                job = omega_db.get_job(stem)
                lang = job.get('target_language', 'is') if job else 'is'
                
                # Re-finalize
                srt_path, normalized_path = finalizer.finalize(approved_path, target_language=lang)
                
                omega_db.update(
                    stem,
                    stage="FINALIZED",
                    status="AI Edited",
                    progress=90.0,
                    meta={
                        "ai_edit_at": datetime.now().isoformat(),
                        "srt_path": str(srt_path),
                        "normalized_path": str(normalized_path),
                    },
                )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Assistant Endpoint Failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/editor/<job_id>', methods=['GET', 'POST'])
@admin_required
def api_editor(job_id):
    """
    GET: Retrieve full segments for the editor.
    POST: Save updated segments and re-finalize.
    """
    try:
        from workers import assistant
        # Reuse the file loading logic from assistant (it knows the priority APPROVED > SKELETON)
        file_path, data = assistant._load_job_file(job_id)
        
        if request.method == 'GET':
            if not file_path or not data:
                return jsonify({"error": "Job file not found"}), 404
                
            segments = []
            if isinstance(data, dict):
                # Handle APPROVED format vs SKELETON format vs NORMALIZED
                if "segments" in data:
                    segments = data["segments"]
                elif "events" in data:
                    segments = data["events"]
                elif "translated_data" in data:
                    segments = data.get("translated_data", [])
            elif isinstance(data, list):
                segments = data
            
            # Normalize: Convert 'lines' array to 'text' string
            for seg in segments:
                if "lines" in seg and not "text" in seg:
                    seg["text"] = "\n".join(seg["lines"])
            
            # Populate "source_text" from Skeleton if missing
            # This ensures "Original Text" column is always filled
            try:
                skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON_DONE.json"
                if not skeleton_path.exists():
                    skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"
                
                if skeleton_path.exists():
                    with open(skeleton_path, "r", encoding="utf-8") as f:
                        skel_data = json.load(f)
                        skel_segs = skel_data.get("segments", [])
                        
                    # Create timing map (start_time -> text)
                    skel_map = {}
                    for s in skel_segs:
                        # Use loose timing match (1 decimal place)
                        key = round(float(s.get("start", 0)), 1)
                        skel_map[key] = s.get("text", "")
                        
                    for seg in segments:
                        if not seg.get("source_text"):
                            start_key = round(float(seg.get("start", 0)), 1)
                            if start_key in skel_map:
                                seg["source_text"] = skel_map[start_key]
            except Exception as e:
                logger.warning(f"Failed to populate source_text from skeleton: {e}")

            return jsonify({
                "job_id": job_id,
                "file_path": str(file_path),
                "segments": segments,
                "graphic_zones": data.get("graphic_zones", []) if isinstance(data, dict) else [],
                "history": data.get("history", []) if isinstance(data, dict) else []
            })

        elif request.method == 'POST':
            if not file_path:
                return jsonify({"error": "Original file not found, cannot save"}), 404
            
            payload = request.json
            new_segments = payload.get("segments")
            if not isinstance(new_segments, list):
                return jsonify({"error": "Invalid segments format"}), 400
            
            # 1. Backup
            assistant._backup_file(file_path)
            
            # 2. Save
            with open(file_path, "r", encoding="utf-8") as f:
                current_full_data = json.load(f)

            original_segments = []
            if isinstance(current_full_data, dict):
                original_segments = current_full_data.get("segments") or []
            elif isinstance(current_full_data, list):
                original_segments = current_full_data

            def _timing_match(left, right, tol=0.001):
                try:
                    return (
                        abs(float(left.get("start", 0.0)) - float(right.get("start", 0.0))) <= tol
                        and abs(float(left.get("end", 0.0)) - float(right.get("end", 0.0))) <= tol
                    )
                except Exception:
                    return False

            def _maybe_copy_fields(target, source):
                if not isinstance(source, dict) or not isinstance(target, dict):
                    return
                if (not isinstance(target.get("words"), list) or not target.get("words")) and isinstance(source.get("words"), list):
                    if _timing_match(target, source):
                        target["words"] = source.get("words")
                if not target.get("source_text") and source.get("source_text"):
                    if _timing_match(target, source):
                        target["source_text"] = source.get("source_text")

            def _timing_key(segment, precision=3):
                try:
                    return (
                        round(float(segment.get("start", 0.0)), precision),
                        round(float(segment.get("end", 0.0)), precision),
                    )
                except Exception:
                    return None

            timing_map = {}
            for seg in original_segments:
                if not isinstance(seg, dict):
                    continue
                key = _timing_key(seg)
                if key is not None:
                    timing_map[key] = seg

            for seg in new_segments:
                if not isinstance(seg, dict):
                    continue
                key = _timing_key(seg)
                source = timing_map.get(key) if key is not None else None
                if source:
                    _maybe_copy_fields(seg, source)
                else:
                    # If timing changed, drop word timing to avoid stale alignment.
                    seg.pop("words", None)
                    seg.pop("source_text", None)
            
            if isinstance(current_full_data, dict):
                current_full_data["segments"] = new_segments
                current_full_data["graphic_zones"] = payload.get("graphic_zones", [])
                current_full_data["history"] = payload.get("history", [])
                # Update meta
                if "meta" not in current_full_data: current_full_data["meta"] = {}
                current_full_data["meta"]["last_manual_edit"] = datetime.now().isoformat()
            else:
                # Upgrade list format to dict format to support metadata
                current_full_data = {
                    "segments": new_segments,
                    "graphic_zones": payload.get("graphic_zones", []),
                    "history": payload.get("history", []),
                    "meta": {"last_manual_edit": datetime.now().isoformat()}
                }
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(current_full_data, f, ensure_ascii=False, indent=2)
            
            # 3. Re-finalize
            from workers import finalizer
            job = omega_db.get_job(job_id)
            lang = job.get('target_language', 'is') if job else 'is'
            srt_path, normalized_path = finalizer.finalize(file_path, target_language=lang)
            
            # 4. Update DB
            omega_db.update(
                job_id,
                stage="FINALIZED",
                status="Manual Edit Saved",
                progress=90.0, 
                meta={
                    "manual_edit_at": datetime.now().isoformat(),
                    "srt_path": str(srt_path),
                    "normalized_path": str(normalized_path)
                }
            )
            
            return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Editor API Failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/stream/<job_id>')
def api_stream_video(job_id):
    """
    Stream the video file for a job.
    Supports Range requests via Flask's send_file.
    
    Priority order for finding video:
    1. meta.vault_path (after ingest)
    2. meta.source_path (original location)
    3. VAULT_VIDEOS / original_filename (fallback lookup)
    """
    try:
        job = omega_db.get_job(job_id)
        # if not job:
        #    return jsonify({"error": "Job not found"}), 404
            
        meta = job.get("meta", {}) if job else {}
        video_path = None
        
        # Priority 0: PROXY File (Always prefer web-ready proxy if available)
        candidates_proxies = []
        candidates_proxies.append(config.PROXIES_DIR / f"{job_id}_PROXY.mp4")
        
        # Also check original_stem (e.g. CBNJD..._PROXY.mp4)
        if meta and meta.get("original_stem"):
             candidates_proxies.append(config.PROXIES_DIR / f"{meta.get('original_stem')}_PROXY.mp4")
        
        # Fallback: Try to derive original stem from job_id (remove timestamp suffix)
        try:
            # Assumes format: STEM-TIMESTAMP
            derived_stem = job_id.rsplit('-', 1)[0].upper()
            candidates_proxies.append(config.PROXIES_DIR / f"{derived_stem}_PROXY.mp4")
        except Exception:
            pass
             
        # logger.info(f"Stream Candidates for {job_id}: {[str(p) for p in candidates_proxies]}")
        
        # DEBUG: Log all candidates
        logger.info(f"DEBUG STREAM {job_id}: Checking candidates: {[str(p) for p in candidates_proxies]}")
        
        proxy_path = next((p for p in candidates_proxies if p.exists()), None)
        if proxy_path:
            # logger.info(f"Found proxy: {proxy_path}")
            video_path = proxy_path
            
        # Priority 1: Check vault_path (set after ingest moves file) (Fallback)
        if not video_path:
            vault_path = meta.get("vault_path")
            if vault_path:
                candidate = Path(vault_path)
                if candidate.exists():
                    video_path = candidate
                
        # Priority 2: Check source_path
        if not video_path:
            source_path = meta.get("source_path")
            if source_path:
                candidate = Path(source_path)
                if candidate.exists():
                    video_path = candidate
        
        # Priority 3: Fallback - search VAULT_VIDEOS by original_filename
        if meta.get("original_stem"):
             candidates_proxies.append(config.PROXIES_DIR / f"{meta.get('original_stem')}_PROXY.mp4")

        # 3. Add case-insensitive candidates (explicit upper/lower) to handle Linux case sensitivity
        # This handles when frontend requests lowercase ID but file is UPPERCASE
        candidates_proxies.append(config.PROXIES_DIR / f"{job_id.upper()}_PROXY.mp4")
        if meta.get("original_stem"):
             candidates_proxies.append(config.PROXIES_DIR / f"{meta.get('original_stem').upper()}_PROXY.mp4")

        logger.info(f"Stream Candidates for {job_id}: {[str(p) for p in candidates_proxies]}")
        
        proxy_path = next((p for p in candidates_proxies if p.exists()), None)
        if proxy_path:
            logger.info(f"Found proxy: {proxy_path}")
            video_path = proxy_path
                    
        # Priority 4: Fallback - search VAULT_VIDEOS by job_id pattern
        if not video_path:
            for ext in [".mp4", ".mov", ".mkv", ".avi", ".m4v"]:
                candidate = config.VAULT_VIDEOS / f"{job_id}{ext}"
                if candidate.exists():
                    video_path = candidate
                    break
                    
        if not video_path:
            return jsonify({"error": "Video file not found", "checked": [
                str(vault_path) if vault_path else None,
                str(meta.get("source_path")) if meta.get("source_path") else None,
                str(config.VAULT_VIDEOS)
            ]}), 404
            
        return send_file(video_path, as_attachment=False, conditional=True)

    except Exception as e:
        logger.error(f"Stream Failed: {e}")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# API V2: Programs, Tracks, Deliveries (Localization Platform)
# =============================================================================

@app.route('/api/v2/programs', methods=['GET'])
def api_v2_get_programs():
    """Get all programs with their tracks."""
    client = request.args.get('client')
    limit = int(request.args.get('limit', 100))
    
    programs = omega_db.get_all_programs(client=client, limit=limit)
    
    # Enrich with tracks
    for program in programs:
        program['tracks'] = omega_db.get_tracks_for_program(program['id'])
        
        # Calculate completion stats
        total_tracks = len(program['tracks'])
        complete_tracks = sum(1 for t in program['tracks'] if t['stage'] in ('COMPLETE', 'DELIVERED'))
        program['track_completion'] = f"{complete_tracks}/{total_tracks}" if total_tracks > 0 else "0/0"
        program['needs_attention'] = any(
            t['stage'] in ('AWAITING_REVIEW', 'FAILED') for t in program['tracks']
        )
    
    return jsonify(programs)


@app.route('/api/v2/programs/<program_id>', methods=['GET'])
def api_v2_get_program(program_id):
    """Get a single program with all details."""
    program = omega_db.get_program(program_id)
    if not program:
        return jsonify({"error": "Program not found"}), 404
    
    program['tracks'] = omega_db.get_tracks_for_program(program_id)
    program['deliveries'] = []
    
    # Get deliveries for each track
    for track in program['tracks']:
        track_deliveries = omega_db.get_deliveries_for_track(track['id'])
        program['deliveries'].extend(track_deliveries)
    
    return jsonify(program)


@app.route('/api/v2/programs', methods=['POST'])
@admin_required
def api_v2_create_program():
    """Create a new program manually."""
    data = request.json
    
    program_id = omega_db.create_program(
        title=data.get('title', 'Untitled'),
        original_filename=data.get('original_filename'),
        video_path=data.get('video_path'),
        client=data.get('client'),
        due_date=data.get('due_date'),
        default_style=data.get('default_style', 'Classic'),
        meta=data.get('meta', {})
    )
    
    return jsonify({"success": True, "program_id": program_id})


@app.route('/api/v2/programs/<program_id>/tracks', methods=['GET'])
def api_v2_get_program_tracks(program_id):
    """Get all tracks for a program."""
    tracks = omega_db.get_tracks_for_program(program_id)
    return jsonify(tracks)


@app.route('/api/v2/programs/<program_id>/tracks', methods=['POST'])
@admin_required
def api_v2_add_track(program_id):
    """Add a new track (subtitle or dub) to a program."""
    data = request.json
    
    program = omega_db.get_program(program_id)
    if not program:
        return jsonify({"error": "Program not found"}), 404
    
    track_type = data.get('type', 'subtitle')
    language_code = data.get('language_code', 'is')
    voice_id = data.get('voice_id')  # For dub tracks
    depends_on = data.get('depends_on')  # For dub tracks, the subtitle track to use
    
    # For dub tracks, require a completed subtitle track
    if track_type == 'dub':
        if not depends_on:
            # Find a completed subtitle track in the same language
            existing_tracks = omega_db.get_tracks_for_program(program_id)
            subtitle_track = next(
                (t for t in existing_tracks 
                 if t['type'] == 'subtitle' 
                 and t['language_code'] == language_code 
                 and t['stage'] in ('COMPLETE', 'DELIVERED')),
                None
            )
            if subtitle_track:
                depends_on = subtitle_track['id']
            else:
                return jsonify({
                    "error": f"No completed subtitle track in {language_code} to base dubbing on"
                }), 400
    
    track_id = omega_db.create_track(
        program_id=program_id,
        type=track_type,
        language_code=language_code,
        stage='QUEUED',
        status='Pending',
        voice_id=voice_id,
        depends_on=depends_on,
        meta=data.get('meta', {})
    )
    
    return jsonify({"success": True, "track_id": track_id})


@app.route('/api/v2/tracks/<track_id>', methods=['GET'])
def api_v2_get_track(track_id):
    """Get a single track with details."""
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    # Include program info
    program = omega_db.get_program(track['program_id'])
    track['program'] = program
    
    # Include deliveries
    track['deliveries'] = omega_db.get_deliveries_for_track(track_id)
    
    # Compute file paths based on job_id
    job_id = track.get('job_id')
    if job_id:
        srt_path = config.SRT_DIR / f"{job_id}.srt"
        video_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"
        
        track['srt_path'] = str(srt_path) if srt_path.exists() else None
        track['video_path'] = str(video_path) if video_path.exists() else None
        track['files_ready'] = srt_path.exists() and video_path.exists()
        
        # Auto-correct stuck BURNING status
        if track.get('stage') == 'BURNING' and video_path.exists():
            logger.info(f"Auto-correcting stuck BURNING status for {job_id}")
            omega_db.update_track(track_id, stage="COMPLETE", status="Done", progress=100.0)
            track['stage'] = "COMPLETE"
            track['status'] = "Done"
            track['progress'] = 100.0
    else:
        track['srt_path'] = None
        track['video_path'] = None
        track['files_ready'] = False
    
    return jsonify(track)


@app.route('/api/v2/tracks/<track_id>', methods=['PUT'])
@admin_required
def api_v2_update_track(track_id):
    """Update track fields."""
    data = request.json
    
    # Filter allowed fields
    allowed = {'stage', 'status', 'progress', 'rating', 'voice_id', 'output_path', 'meta'}
    updates = {k: v for k, v in data.items() if k in allowed}
    
    if omega_db.update_track(track_id, **updates):
        return jsonify({"success": True})
    return jsonify({"error": "Track not found"}), 404


@app.route('/api/v2/tracks/active', methods=['GET'])
def api_v2_get_active_tracks():
    """Get all tracks that are currently in progress."""
    limit = int(request.args.get('limit', 50))
    tracks = omega_db.get_active_tracks(limit=limit)
    return jsonify(tracks)


@app.route('/api/v2/tracks/<track_id>/reveal', methods=['POST'])
@admin_required
def api_v2_reveal_track_file(track_id):
    """Open the track's output file in Finder (macOS)."""
    data = request.json or {}
    file_type = data.get('type', 'video')  # 'video' or 'srt'
    
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    job_id = track.get('job_id')
    if not job_id:
        return jsonify({"error": "No job_id for track"}), 400
    
    if file_type == 'srt':
        file_path = config.SRT_DIR / f"{job_id}.srt"
    else:
        file_path = config.VIDEO_DIR / f"{job_id}_SUBBED.mp4"
    
    if not file_path.exists():
        return jsonify({"error": f"File not found: {file_path.name}"}), 404
    
    # macOS: reveal in Finder
    import subprocess
    subprocess.run(['open', '-R', str(file_path)], check=False)
    
    return jsonify({"success": True, "path": str(file_path)})


@app.route('/api/v2/tracks/<track_id>/deliver', methods=['POST'])
@admin_required
def api_v2_deliver_track(track_id):
    """Record that a track was delivered."""
    data = request.json
    
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    delivery_id = omega_db.record_track_delivery(
        track_id=track_id,
        destination=data.get('destination', 'Unknown'),
        recipient=data.get('recipient'),
        notes=data.get('notes')
    )
    
    return jsonify({"success": True, "delivery_id": delivery_id})


@app.route('/api/v2/deliveries', methods=['GET'])
def api_v2_get_deliveries():
    """Get recent deliveries."""
    days = int(request.args.get('days', 7))
    limit = int(request.args.get('limit', 100))
    
    deliveries = omega_db.get_recent_deliveries(days=days, limit=limit)
    return jsonify(deliveries)


@app.route('/api/v2/thumbnails/<program_id>')
def api_v2_thumbnail(program_id):
    """Serve program thumbnail."""
    program = omega_db.get_program(program_id)
    if not program:
        return jsonify({"error": "Program not found"}), 404
    
    thumbnail_path = program.get('thumbnail_path')
    if thumbnail_path and Path(thumbnail_path).exists():
        return send_file(thumbnail_path, mimetype='image/jpeg')
    
    # Return placeholder
    return jsonify({"error": "No thumbnail"}), 404


# =============================================================================
# API V2: Track Actions (for Program Detail redesign)
# =============================================================================

@app.route('/api/v2/tracks/<track_id>/send-to-review', methods=['POST'])
@admin_required
def api_v2_send_to_review(track_id):
    """Send a track to review stage."""
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    # Update track stage
    omega_db.update_track(track_id, stage='AWAITING_REVIEW', status='Sent for review')
    
    # Also update the linked job if exists
    if track.get('job_id'):
        omega_db.update(track['job_id'], stage='AWAITING_REVIEW', status='Sent for review')
    
    return jsonify({"success": True, "stage": "AWAITING_REVIEW"})


@app.route('/api/v2/tracks/<track_id>/approve', methods=['POST'])
@admin_required
def api_v2_approve_track(track_id):
    """Approve a track and move to next stage (BURNING for subtitles, DUBBING for dubs)."""
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    # Determine next stage based on track type
    if track['type'] == 'dub':
        next_stage = 'DUBBING'
        next_status = 'Generating audio'
    else:
        next_stage = 'BURNING'
        next_status = 'Approved - queued for burn'
    
    # Update track
    omega_db.update_track(track_id, stage=next_stage, status=next_status)
    
    # Also update the linked job if exists
    if track.get('job_id'):
        job_id = track['job_id']
        # Clear halted and set burn_approved to allow re-burn
        omega_db.update(
            job_id, 
            stage=next_stage, 
            status=next_status, 
            progress=90.0,
            meta={
                "halted": False,
                "burn_approved": True,
                "last_error": "",
                "failed_at": "",
            }
        )
        
        # RESET FILES FOR RE-BURN
        if next_stage == 'BURNING':
            try:
                # 1. Reset SRT (DONE_stem.srt -> stem.srt)
                stem = job_id
                srt_path = config.SRT_DIR / f"{stem}.srt"
                done_srt_path = config.SRT_DIR / f"DONE_{stem}.srt"
                if not srt_path.exists() and done_srt_path.exists():
                    shutil.move(str(done_srt_path), str(srt_path))
                    
                # 2. Backup existing video to force re-burn
                 # If video exists, manager thinks it's done.
                video_path = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"
                if video_path.exists():
                     backup_path = config.VIDEO_DIR / f"{stem}_SUBBED_BACKUP_{int(time.time())}.mp4"
                     shutil.move(str(video_path), str(backup_path))
            except Exception as e:
                logger.error(f"Failed to reset files for re-burn {job_id}: {e}")
    
    return jsonify({"success": True, "stage": next_stage})


@app.route('/api/v2/tracks/<track_id>/send-review', methods=['POST'])
@admin_required
def api_v2_send_for_review(track_id):
    """
    Send a track for remote review.
    Generates proxy, uploads to Bunny Stream, sends email.
    """
    from workers import remote_review
    
    data = request.json or {}
    reviewer_email = data.get("email")
    
    if not reviewer_email:
        return jsonify({"error": "Email address required"}), 400
    
    # Get track and job info
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "No linked job for this track"}), 400
    
    # Check if already sent
    job = omega_db.get_job(job_id)
    meta = job.get("meta", {}) if job else {}
    if meta.get("bunny_video_id"):
        # Already uploaded, just resend email
        review_url = review_notifier.build_review_url(job_id)
        review_notifier.send_review_notification(
            job_id=job_id,
            program_name=meta.get("original_filename", job_id),
            target_language=job.get("target_language", "Icelandic"),
            reviewer_email=reviewer_email
        )
        return jsonify({
            "success": True,
            "message": "Review link resent",
            "review_url": review_url,
            "bunny_embed_url": meta.get("bunny_embed_url")
        })
    
    # Queue the remote review job (runs in background)
    try:
        executor.submit(remote_review.send_for_remote_review, job_id, reviewer_email)
        logger.info(f"Queued remote review for {job_id} to {reviewer_email}")
        return jsonify({
            "success": True,
            "message": "Review being prepared. Email will be sent when ready."
        })
    except Exception as e:
        logger.error(f"Failed to queue remote review: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/v2/tracks/<track_id>/review-status', methods=['GET'])
@admin_required
def api_v2_get_review_status(track_id):
    """Get the remote review status for a track."""
    from workers import remote_review
    
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    job_id = track.get("job_id")
    if not job_id:
        return jsonify({"error": "No linked job"}), 400
    
    status = remote_review.get_review_status(job_id)
    return jsonify(status)

@app.route('/api/v2/tracks/<track_id>/open-editor', methods=['GET'])
def api_v2_open_editor(track_id):
    """Get the editor URL for a track."""
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    # The editor uses job_id
    job_id = track.get('job_id')
    if not job_id:
        return jsonify({"error": "No linked job for this track"}), 400
    
    editor_url = f"/editor/{job_id}"
    return jsonify({"editor_url": editor_url, "job_id": job_id})


@app.route('/api/v2/tracks/<track_id>/start-dub', methods=['POST'])
@admin_required
def api_v2_start_dub(track_id):
    """Start dubbing for a dub track."""
    from workers.dubber import Dubber
    
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    if track['type'] != 'dub':
        return jsonify({"error": "Track is not a dub type"}), 400
    
    voice_id = track.get('voice_id', 'alloy')
    
    # Get the source subtitle track (depends_on)
    source_track_id = track.get('depends_on')
    if not source_track_id:
        # Try to find a completed subtitle track in same language
        program_id = track['program_id']
        all_tracks = omega_db.get_tracks_for_program(program_id)
        subtitle_tracks = [t for t in all_tracks if t['type'] == 'subtitle' and t['stage'] in ('COMPLETE', 'DELIVERED')]
        if subtitle_tracks:
            source_track_id = subtitle_tracks[0]['id']
    
    if not source_track_id:
        return jsonify({"error": "No source subtitle track available"}), 400
    
    source_track = omega_db.get_track(source_track_id)
    if not source_track or not source_track.get('job_id'):
        return jsonify({"error": "Source track missing job data"}), 400
    
    job_id = source_track['job_id']
    job_dir = (Path("jobs") / job_id).resolve()
    
    def run_dubbing():
        try:
            logger.info(f"Starting dubbing for track {track_id} with voice {voice_id}")
            omega_db.update_track(track_id, stage='DUBBING', status=f'Generating audio ({voice_id})', progress=10)
            
            dubber = Dubber(job_id, job_dir)
            # TODO: Pass voice_id to dubber when we enhance it
            dubber.run()
            
            omega_db.update_track(track_id, stage='COMPLETE', status='Dubbing complete', progress=100)
            logger.info(f"Dubbing complete for track {track_id}")
        except Exception as e:
            logger.error(f"Dubbing failed for track {track_id}: {e}")
            omega_db.update_track(track_id, stage='FAILED', status=f'Dubbing failed: {str(e)[:50]}')
    
    thread = threading.Thread(target=run_dubbing)
    thread.start()
    
    return jsonify({"success": True, "message": "Dubbing started", "track_id": track_id})




@app.route('/api/v2/tracks/<track_id>/reject', methods=['POST'])
@admin_required
def api_v2_reject_track(track_id):
    """Reject a track and send back for rework."""
    data = request.json or {}
    reason = data.get('reason', 'Needs rework')
    
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    
    # Send back to translation stage
    omega_db.update_track(track_id, stage='TRANSLATING', status=f'Rejected: {reason}')
    
    if track.get('job_id'):
        omega_db.update(track['job_id'], stage='TRANSLATING', status=f'Rejected: {reason}')
    
    return jsonify({"success": True, "stage": "TRANSLATING"})


@app.route('/api/v2/tracks/<track_id>/retry', methods=['POST'])
@admin_required
def api_v2_retry_track(track_id):
    """Retry a failed track by resetting to the best available checkpoint."""
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404

    job_id = track.get("job_id")
    now = datetime.now().isoformat()

    job = omega_db.get_job(job_id) if job_id else None
    job_meta = (job.get("meta") if isinstance(job, dict) else {}) or {}
    retry_count = int(job_meta.get("retry_count") or 0)
    if retry_count >= 3:
        omega_db.update_track(
            track_id,
            status="Retry limit reached",
            meta={"retry_blocked_at": now, "retry_count": retry_count},
        )
        if job_id:
            omega_db.update(
                job_id,
                status="Retry limit reached",
                meta={"retry_blocked_at": now, "retry_count": retry_count},
            )
        return jsonify({"error": "Retry limit reached. Manual intervention required."}), 409

    next_retry_count = retry_count + 1

    job_stage = "QUEUED"
    if job_id:
        srt_path = config.SRT_DIR / f"{job_id}.srt"
        approved_path = config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json"
        skeleton_done_path = config.VAULT_DATA / f"{job_id}_SKELETON_DONE.json"
        skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"

        if srt_path.exists():
            job_stage = "FINALIZED"
        elif approved_path.exists():
            job_stage = "REVIEWED"
        elif skeleton_done_path.exists() or skeleton_path.exists():
            job_stage = "TRANSCRIBED"

    omega_db.update_track(
        track_id,
        stage="QUEUED",
        status="Retry requested",
        progress=0,
        meta={
            "last_error": "",
            "failed_at": "",
            "retry_requested_at": now,
            "retry_count": next_retry_count,
        },
    )

    if job_id:
        omega_db.update(
            job_id,
            stage=job_stage,
            status="Retry requested",
            progress=0,
            meta={
                "last_error": "",
                "failed_at": "",
                "cloud_stage": "",
                "cloud_progress": {},
                "retry_requested_at": now,
                "retry_count": next_retry_count,
            },
        )

    return jsonify({"success": True, "stage": "QUEUED", "job_stage": job_stage})


@app.route('/api/v2/pipeline/stats', methods=['GET'])
def api_v2_pipeline_stats():
    """Get pipeline statistics for dashboard headers."""
    # Get all tracks to compute stats
    all_tracks = omega_db.get_active_tracks(limit=1000)
    
    # Count by stage
    stage_counts = {}
    blocked_count = 0
    active_count = 0
    
    for track in all_tracks:
        stage = track.get('stage', 'UNKNOWN')
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        
        # Blocked = stuck in review or failed
        if stage in ('AWAITING_REVIEW', 'AWAITING_APPROVAL', 'FAILED'):
            blocked_count += 1
        elif stage not in ('COMPLETE', 'DELIVERED'):
            active_count += 1
    
    # Get recent completions for throughput
    completed_today = sum(1 for t in all_tracks if t.get('stage') == 'COMPLETE')
    
    # Needs attention = awaiting review + failed
    needs_attention = stage_counts.get('AWAITING_REVIEW', 0) + stage_counts.get('FAILED', 0)
    
    return jsonify({
        "total_active": len(all_tracks),
        "blocked": blocked_count,
        "active": active_count,
        "needs_attention": needs_attention,
        "stage_counts": stage_counts,
        "stages": [
            {"name": "INGESTING", "count": stage_counts.get("INGESTING", 0)},
            {"name": "TRANSCRIBING", "count": stage_counts.get("TRANSCRIBING", 0)},
            {"name": "TRANSLATING", "count": stage_counts.get("TRANSLATING", 0) + stage_counts.get("CLOUD_TRANSLATING", 0)},
            {"name": "REVIEWING", "count": stage_counts.get("AWAITING_REVIEW", 0) + stage_counts.get("AWAITING_APPROVAL", 0)},
            {"name": "BURNING", "count": stage_counts.get("BURNING", 0) + stage_counts.get("FINALIZING", 0)},
            {"name": "DUBBING", "count": stage_counts.get("DUBBING", 0)},
            {"name": "COMPLETE", "count": stage_counts.get("COMPLETE", 0)},
            {"name": "FAILED", "count": stage_counts.get("FAILED", 0)},
        ]
    })


@app.route('/api/v2/languages', methods=['GET'])
def api_v2_languages():
    """Get available languages for track creation."""
    from profiles import LANGUAGES, LANGUAGE_POLICIES
    
    languages = []
    for code, lang in LANGUAGES.items():
        policy = LANGUAGE_POLICIES.get(code, {"mode": "sub", "voice": "alloy"})
        languages.append({
            "code": code,
            "name": lang["name"],
            "default_mode": policy["mode"],  # 'sub' or 'dub'
            "default_voice": policy["voice"],
        })
    
    # Sort by name
    languages.sort(key=lambda x: x["name"])
    
    return jsonify({"languages": languages})


@app.route('/api/v2/voices', methods=['GET'])
def api_v2_voices():
    """Get available voices for dubbing."""
    # OpenAI TTS voices
    voices = [
        {"id": "alloy", "name": "Alloy", "description": "Neutral, balanced voice"},
        {"id": "echo", "name": "Echo", "description": "Male, clear and articulate"},
        {"id": "fable", "name": "Fable", "description": "Warm, expressive British accent"},
        {"id": "onyx", "name": "Onyx", "description": "Deep, authoritative male voice"},
        {"id": "nova", "name": "Nova", "description": "Friendly, conversational female"},
        {"id": "shimmer", "name": "Shimmer", "description": "Soft, gentle female voice"},
    ]
    
    return jsonify({"voices": voices})

# =============================================================================
# Server-Sent Events (SSE) for Real-Time Updates
# =============================================================================

import queue
from typing import Generator

# Global event queue for SSE connections
_sse_connections: list[queue.Queue] = []
_sse_lock = threading.Lock()
_last_known_jobs: dict = {}  # Cache to detect changes


def _broadcast_event(event_type: str, data: dict):
    """Broadcast an event to all connected SSE clients."""
    message = json.dumps({"type": event_type, "data": data, "timestamp": datetime.now().isoformat()})
    with _sse_lock:
        dead_queues = []
        for q in _sse_connections:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead_queues.append(q)
        for q in dead_queues:
            _sse_connections.remove(q)


def _check_for_changes():
    """Check for job changes and broadcast events."""
    global _last_known_jobs
    
    try:
        current_jobs = {job["file_stem"]: job for job in get_all_jobs()}
        
        # Detect new jobs
        for stem, job in current_jobs.items():
            if stem not in _last_known_jobs:
                _broadcast_event("job.created", job)
            else:
                # Check if job changed (compare updated_at and key fields)
                old_job = _last_known_jobs[stem]
                if (job.get("updated_at") != old_job.get("updated_at") or
                    job.get("stage") != old_job.get("stage") or
                    job.get("status") != old_job.get("status") or
                    job.get("progress") != old_job.get("progress")):
                    _broadcast_event("job.updated", job)
        
        # Detect deleted jobs
        for stem in _last_known_jobs:
            if stem not in current_jobs:
                _broadcast_event("job.deleted", {"file_stem": stem})
        
        _last_known_jobs = current_jobs
        
    except Exception as e:
        logger.error(f"SSE change detection error: {e}")


def _sse_monitor_loop():
    """Background thread to monitor for changes and broadcast events."""
    global _last_known_jobs
    
    # Initial load
    try:
        _last_known_jobs = {job["file_stem"]: job for job in get_all_jobs()}
    except Exception:
        pass
    
    while True:
        try:
            if _sse_connections:  # Only check if clients connected
                _check_for_changes()
                
                # Also broadcast health updates periodically
                health_data = {
                    "storage_ready": config.critical_paths_ready(),
                    "disk_free_gb": _disk_free_gb(config.DELIVERY_DIR),
                    "heartbeats": {
                        "omega_manager_age_seconds": _heartbeat_age_seconds("omega_manager"),
                        "dashboard_age_seconds": _heartbeat_age_seconds("dashboard"),
                    },
                }
                _broadcast_event("health.updated", health_data)
                
        except Exception as e:
            logger.error(f"SSE monitor error: {e}")
        
        time.sleep(2)  # Check every 2 seconds for changes


def _start_sse_monitor_thread():
    """Start the SSE monitor thread."""
    thread = threading.Thread(target=_sse_monitor_loop, name="sse-monitor", daemon=True)
    thread.start()


@app.route("/api/events")
def api_events():
    """
    Server-Sent Events endpoint for real-time updates.
    
    Events:
    - job.created: New job added
    - job.updated: Job changed (stage, status, progress)
    - job.deleted: Job removed
    - health.updated: System health changed
    """
    def event_stream() -> Generator[str, None, None]:
        client_queue: queue.Queue = queue.Queue(maxsize=100)
        
        with _sse_lock:
            _sse_connections.append(client_queue)
        
        try:
            # Send initial jobs snapshot
            try:
                jobs = get_all_jobs()
                health_data = {
                    "storage_ready": config.critical_paths_ready(),
                    "disk_free_gb": _disk_free_gb(config.DELIVERY_DIR),
                    "heartbeats": {
                        "omega_manager_age_seconds": _heartbeat_age_seconds("omega_manager"),
                        "dashboard_age_seconds": _heartbeat_age_seconds("dashboard"),
                    },
                }
                init_message = json.dumps({
                    "type": "init",
                    "data": {"jobs": jobs, "health": health_data},
                    "timestamp": datetime.now().isoformat()
                })
                yield f"data: {init_message}\n\n"
                logger.info("SSE Init sent")
            except Exception as e:
                logger.error(f"SSE init error: {e}")
            
            # Stream events
            logger.info("SSE entering loop")
            while True:
                try:
                    message = client_queue.get(timeout=5)
                    yield f"data: {message}\n\n"
                except queue.Empty:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
                    
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if client_queue in _sse_connections:
                    _sse_connections.remove(client_queue)
    
    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


if __name__ == '__main__':
    # Ensure DB exists
    # omega_db.init_db() # Skipped to prevent startup lock contention
    _start_heartbeat_thread()
    _start_sse_monitor_thread()  # Start SSE real-time updates
    # Run server (Disable reloader to prevent zombie processes)
    host = os.environ.get("OMEGA_DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("OMEGA_DASH_PORT", "8080"))
    debug = (os.environ.get("OMEGA_DASH_DEBUG") or "").strip().lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)

@app.route('/api/v2/deliveries', methods=['POST'])
@admin_required
def api_v2_create_delivery():
    """Create a delivery for a track."""
    data = request.json
    track_id = data.get('track_id')
    method = data.get('method', 'folder')
    recipient = data.get('recipient', '')
    notes = data.get('notes', '')
    
    if not track_id:
        return jsonify({"error": "Missing track_id"}), 400
        
    track = omega_db.get_track(track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
        
    # In a real impl, this would copy files. For now we just log it.
    delivery_id = str(uuid.uuid4())
    
    # Update track status
    omega_db.update_track(track_id, stage='DELIVERED', status=f'Delivered via {method}')
    
    # Create delivery record (we'd add a track_deliveries table in real life)
    # For now, just logging to main deliveries table if job_id exists
    if track.get('job_id'):
        omega_db.log_delivery(track['job_id'], "Manual", datetime.now().isoformat(), method, notes)
        
    return jsonify({
        "success": True, 
        "delivery_id": delivery_id,
        "message": f"Track {track_id} delivered"
    })
