from flask import Flask, render_template, jsonify, request, send_file
import os
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


@app.route('/api/action', methods=['POST'])
@admin_required
def api_action():
    """Handle surgical actions."""
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    file_stem = data.get('file_stem')

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
                subprocess.Popen(
                    [sys.executable, str(mgr_path)],
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
@admin_required
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
@admin_required
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
@admin_required
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

if __name__ == '__main__':
    # Ensure DB exists
    omega_db.init_db()
    # Run server (Disable reloader to prevent zombie processes)
    host = os.environ.get("OMEGA_DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("OMEGA_DASH_PORT", "8080"))
    debug = (os.environ.get("OMEGA_DASH_DEBUG") or "").strip().lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, use_reloader=False)
