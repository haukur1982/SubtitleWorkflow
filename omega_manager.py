import time
import os
import sys
import json
import logging
import shutil
import subprocess
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import config
import omega_db
import system_health
from gcp_auth import ensure_google_application_credentials
from gcs_jobs import GcsJobPaths, new_job_id, upload_json, download_json, blob_exists
from email_utils import send_email
from cloud_run_jobs import run_cloud_run_job
from lock_manager import ProcessLock
from concurrent.futures import ThreadPoolExecutor
from google.cloud import storage

# Import Workers
from workers import transcriber, translator, editor, finalizer, publisher
from workers import audio_clipper, review_notifier

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/manager.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("OmegaManager")

# Active Task Registry to prevent duplicate submissions
active_tasks = set()

# Failure tracking for backoff
failure_counts = {}

# Thread lock for concurrent access to active_tasks and failure_counts
import threading
_task_lock = threading.Lock()

MAX_TASK_FAILURES = 5

# --- Thread-safe helpers for active_tasks ---
def _is_task_active(stem: str) -> bool:
    """Thread-safe check if a task is currently active."""
    with _task_lock:
        return stem in active_tasks

def _add_task(stem: str) -> bool:
    """Thread-safe add to active_tasks. Returns True if added, False if already present."""
    with _task_lock:
        if stem in active_tasks:
            return False
        active_tasks.add(stem)
        return True

def _remove_task(stem: str):
    """Thread-safe remove from active_tasks."""
    with _task_lock:
        active_tasks.discard(stem)

def _is_in_cooldown(stem: str) -> bool:
    """Thread-safe cooldown check."""
    with _task_lock:
        if stem not in failure_counts:
            return False
        count, last_fail = failure_counts[stem]
        backoff = min(2 ** count, 60)
        return time.time() - last_fail < backoff

def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default

INGEST_STALL_SECONDS = _safe_float_env("OMEGA_INGEST_STALL_SECONDS", 1800.0)
INGEST_STABILITY_CHECKS = int(os.environ.get("OMEGA_INGEST_STABILITY_CHECKS", "3") or 3)
INGEST_STABILITY_DELAY = _safe_float_env("OMEGA_INGEST_STABILITY_DELAY", 1.0)
INGEST_MIN_AGE_SECONDS = _safe_float_env("OMEGA_INGEST_MIN_AGE", 3.0)
RESTART_FLAG = config.BASE_DIR / "heartbeats" / "omega_manager.restart"
RESTART_FORCE_FLAG = config.BASE_DIR / "heartbeats" / "omega_manager.restart.force"

STAGE_STALL_THRESHOLDS = {
    "TRANSLATING": _safe_float_env("OMEGA_STALL_TRANSLATING", 5400.0),
    "TRANSLATING_CLOUD_SUBMITTED": _safe_float_env("OMEGA_STALL_CLOUD_SUBMITTED", 5400.0),
    "CLOUD_TRANSLATING": _safe_float_env("OMEGA_STALL_CLOUD", 5400.0),
    "CLOUD_REVIEWING": _safe_float_env("OMEGA_STALL_CLOUD_REVIEWING", 7200.0),
    "REVIEWING": _safe_float_env("OMEGA_STALL_REVIEWING", 10800.0),
    "FINALIZING": _safe_float_env("OMEGA_STALL_FINALIZING", 10800.0),
    "BURNING": _safe_float_env("OMEGA_STALL_BURNING", 21600.0),
}


def _cloud_pipeline_enabled() -> bool:
    return str(os.environ.get("OMEGA_CLOUD_PIPELINE", "")).strip().lower() in {"1", "true", "yes", "on"}

def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def _polish_pass_enabled(meta: dict) -> bool:
    mode = str(getattr(config, "OMEGA_CLOUD_POLISH_MODE", "review") or "review").strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return False
    if mode in {"1", "true", "yes", "on", "all"}:
        return True
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("review_required")) or str(meta.get("mode") or "").upper() == "REVIEW"

def _review_portal_url() -> str:
    return str(os.environ.get("OMEGA_REVIEW_PORTAL_URL", "") or "").strip()

def _reviewer_emails(meta: dict) -> list[str]:
    if isinstance(meta, dict):
        value = meta.get("reviewer_email")
        if value:
            return [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]
    value = os.environ.get("OMEGA_REVIEWER_EMAIL", "")
    return [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]

def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Convert to naive UTC for comparison with datetime.now()
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None

def _stage_started_at(meta: dict, stage: str) -> Optional[datetime]:
    if not isinstance(meta, dict) or not stage:
        return None
    timeline = meta.get("stage_timeline")
    if not isinstance(timeline, list):
        return None
    for item in reversed(timeline):
        if not isinstance(item, dict):
            continue
        if str(item.get("stage", "")).upper() != stage.upper():
            continue
        started_at = item.get("started_at")
        parsed = _parse_iso(started_at) if isinstance(started_at, str) else None
        if parsed:
            return parsed
    return None

def _status_is_blocked(status: str) -> bool:
    if not status:
        return False
    lowered = status.lower()
    if "waiting" in lowered:
        return True
    if "blocked" in lowered:
        return True
    if "paused" in lowered:
        return True
    return False

def _request_manager_restart(force: bool = False) -> None:
    try:
        RESTART_FLAG.parent.mkdir(exist_ok=True)
        RESTART_FLAG.touch()
        if force:
            RESTART_FORCE_FLAG.touch()
    except Exception:
        pass

def _find_vault_video(stem: str) -> Optional[Path]:
    try:
        candidates = sorted(config.VAULT_VIDEOS.glob(f"{stem}.*"))
    except Exception:
        candidates = []
    for candidate in candidates:
        if candidate.is_file() and not candidate.name.startswith("._"):
            return candidate
    return None


def _is_stable_file(path: Path) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    if INGEST_MIN_AGE_SECONDS and (time.time() - stat.st_mtime) < INGEST_MIN_AGE_SECONDS:
        return False
    size = stat.st_size
    checks = max(1, int(INGEST_STABILITY_CHECKS))
    for _ in range(checks - 1):
        time.sleep(max(0.1, INGEST_STABILITY_DELAY))
        try:
            if path.stat().st_size != size:
                return False
        except FileNotFoundError:
            return False
    return True

def _cloud_job_paths(meta: dict) -> tuple[Optional[GcsJobPaths], Optional[str], Optional[str]]:
    if not isinstance(meta, dict):
        return None, None, None
    cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
    if not cloud_job_id:
        return None, None, None
    bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
    prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
    return GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=str(cloud_job_id)), bucket_name, prefix

def _trigger_review_portal(stem: str, meta: dict, job: dict) -> bool:
    """
    Trigger human review portal workflow if enabled.
    
    Generates audio clips, uploads to GCS, and sends email notification.
    Returns True if review was triggered (job should wait for approval).
    """
    # Check if review portal is enabled
    if not _is_truthy(os.environ.get("OMEGA_REVIEW_PORTAL_ENABLED", "0")):
        return False
    
    # Check if this job requires human review (from source path)
    source_path = str(meta.get("source_path") or "").lower()
    if "/02_human_review/" not in source_path:
        return False
    
    # Already in review?
    if meta.get("review_notification_sent"):
        return True  # Wait for approval
    
    logger.info(f"🔍 Triggering human review for: {stem}")
    
    # Get necessary paths (prefer stored vault path / original filename)
    video_path = None
    vault_path = meta.get("vault_path") if isinstance(meta, dict) else None
    if vault_path:
        candidate = Path(str(vault_path))
        if candidate.exists():
            video_path = candidate
    if video_path is None:
        original_filename = meta.get("original_filename") if isinstance(meta, dict) else None
        if original_filename:
            candidate = config.VAULT_VIDEOS / original_filename
            if candidate.exists():
                video_path = candidate
    if video_path is None:
        original_stem = meta.get("original_stem") if isinstance(meta, dict) else None
        video_path = _find_vault_video(original_stem or stem)
    skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
    if not skeleton_path.exists():
        skeleton_path = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
    
    # Get cloud job info
    paths, bucket_name, prefix = _cloud_job_paths(meta)
    cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
    
    if not all([video_path, skeleton_path.exists(), cloud_job_id]):
        logger.warning(f"   ⚠️ Missing files for review: video={video_path}, skeleton={skeleton_path.exists()}")
        return False
    
    try:
        # Generate and upload audio clips
        audio_clipper.prepare_review_clips(
            video_path=video_path,
            skeleton_path=skeleton_path,
            bucket_name=bucket_name,
            job_prefix=prefix,
            job_id=cloud_job_id
        )
        
        # Get reviewer email
        target_lang = str(job.get("target_language") or meta.get("target_language") or "is").upper()
        reviewer_email = review_notifier.get_reviewer_for_language(target_lang)
        
        if reviewer_email:
            # Get quality rating from editor report
            report = job.get("editor_report")
            quality_rating = None
            if report:
                try:
                    report_data = json.loads(report) if isinstance(report, str) else report
                    quality_rating = report_data.get("rating")
                except Exception:
                    pass
            
            # Send notification
            review_notifier.send_review_notification(
                job_id=cloud_job_id,
                program_name=stem,
                target_language=target_lang,
                reviewer_email=reviewer_email,
                quality_rating=quality_rating
            )
        
        # Update job status
        omega_db.update(
            stem,
            status="Awaiting Human Review",
            meta={
                "review_notification_sent": True,
                "review_portal_job_id": cloud_job_id,
                "review_requested_at": datetime.now().isoformat(),
            }
        )
        
        logger.info(f"   ✅ Review portal triggered for {stem}")
        return True
        
    except Exception as e:
        logger.error(f"   ❌ Failed to trigger review portal: {e}")
        return False


def _build_review_payload(
    *,
    stem: str,
    approved_path: Path,
    target_language: str,
    program_profile: str,
) -> dict:
    with open(approved_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", data) if isinstance(data, dict) else data
    payload_segments = []
    for seg in segments or []:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        payload_segments.append(
            {
                "id": seg_id,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "source": seg.get("source_text") or seg.get("source") or "",
                "translation": seg.get("text") or "",
            }
        )
    return {
        "stem": stem,
        "target_language": target_language,
        "program_profile": program_profile,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "segments": payload_segments,
    }

def _send_review_email(*, stem: str, review_url: str, recipients: list[str]) -> bool:
    subject = f"Omega Review Needed: {stem}"
    body = (
        f"Hello!\\n\\n"
        f"A translation is ready for your review. Please open the link below, edit any lines that need fixing, and click Submit.\\n\\n"
        f"{review_url}\\n\\n"
        f"Thank you!\\n"
    )
    return send_email(subject=subject, body=body, to_addrs=recipients)

def _apply_remote_corrections(*, approved_path: Path, corrections: list[dict]) -> tuple[int, int]:
    if not corrections:
        return 0, 0
    with open(approved_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", data) if isinstance(data, dict) else data
    if not isinstance(segments, list):
        return 0, 0

    correction_map: dict[int, str] = {}
    comment_count = 0
    for item in corrections:
        if not isinstance(item, dict):
            continue
        try:
            seg_id = int(item.get("id"))
        except Exception:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            correction_map[seg_id] = text.strip()
        comment = item.get("comment")
        if isinstance(comment, str) and comment.strip():
            comment_count += 1

    applied = 0
    for seg in segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        if seg_id in correction_map:
            seg["text"] = correction_map[seg_id]
            applied += 1

    if isinstance(data, dict):
        data["segments"] = segments

    with open(approved_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return applied, comment_count

def _is_hidden_artifact(path: Path) -> bool:
    name = path.name
    return name.startswith("._") or name.startswith(".")

def task_wrapper(stem, task_name, func, *args, **kwargs):
    """
    Wraps a worker function to handle active_tasks cleanup, error logging, and backoff.
    """
    try:
        logger.info(f"🚀 Starting Async Task: {task_name} for {stem}")
        func(*args, **kwargs)
        
        # Success: Reset failure count
        with _task_lock:
            if stem in failure_counts:
                del failure_counts[stem]
            
    except Exception as e:
        logger.error(f"❌ Async Task Failed ({task_name}): {e}")
        
        # Increment failure count
        with _task_lock:
            count = failure_counts.get(stem, (0, 0))[0] + 1
            failure_counts[stem] = (count, time.time())
        
        # Calculate backoff (2^count, max 60s)
        backoff = min(2 ** count, 60)
        
        error_str = str(e)
        short_error = error_str if len(error_str) <= 180 else error_str[:177] + "..."

        if count > MAX_TASK_FAILURES:
            logger.error(f"🛑 Job {stem} failed {count} times. Halting until manual intervention.")
            omega_db.update(
                stem,
                stage="DEAD",
                status=f"DEAD: {short_error}",
                progress=0,
                meta={
                    "halted": True,
                    "halt_reason": short_error,
                    "last_error": short_error,
                    "failed_at": datetime.now().isoformat(),
                },
            )
            # Remove from active tasks to allow manual retry later if needed
            # But for now, we keep it in failure_counts to prevent immediate retry
        else:
            logger.warning(f"⚠️ Job {stem} failed {count} times. Backing off for {backoff}s...")
            omega_db.update(
                stem,
                status=f"Error (Retry {count}/{MAX_TASK_FAILURES}): {short_error}",
                progress=0,
                meta={"last_error": short_error, "failed_at": datetime.now().isoformat()},
            )
            # We don't sleep here because it blocks the thread. 
            # Instead, we rely on the main loop to re-submit it later.
            # But we need a way to prevent immediate re-submission.
            # For now, let's just update DB status so user sees it.
            # The main loop checks active_tasks, so we remove it from there.
            # But if we remove it, main loop picks it up immediately.
            # We need a "cooldown" set.
            
    finally:
        logger.info(f"🏁 Finished Async Task: {task_name} for {stem}")
        _remove_task(stem)

def ingest_new_files(executor):
    """
    Scans INBOX for new video files.
    """
    EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".mov", ".mkv", ".mpg", ".mpeg", ".moc", ".mxf"}
    
    WATCH_MAP = {
        # Auto Pilot
        config.INBOX_DIR / "01_AUTO_PILOT" / "Classic": ("AUTO", "Classic"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Modern_Look": ("AUTO", "Modern"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Apple_TV": ("AUTO", "Apple"),
        # Manual Review
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Classic": ("REVIEW", "Classic"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Modern_Look": ("REVIEW", "Modern"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Apple_TV": ("REVIEW", "Apple"),
        # Remote Review (email reviewer)
        config.INBOX_DIR / "03_REMOTE_REVIEW" / "Classic": ("REMOTE_REVIEW", "Classic"),
        config.INBOX_DIR / "03_REMOTE_REVIEW" / "Modern_Look": ("REMOTE_REVIEW", "Modern"),
        config.INBOX_DIR / "03_REMOTE_REVIEW" / "Apple_TV": ("REMOTE_REVIEW", "Apple"),
    }
    
    for folder, (mode, style) in WATCH_MAP.items():
        if not folder.exists(): continue
        
        for file_path in folder.iterdir():
            if file_path.name.startswith("."): continue
            if file_path.suffix.lower() in EXTENSIONS:
                # Stability Check
                if not _is_stable_file(file_path):
                    continue

                stem = file_path.stem
                if _is_task_active(stem):
                    logger.debug(f"⚠️ Skipping {stem}: Already active")
                    continue 

                logger.info(f"📥 Found Candidate: {file_path.name} in {folder}")
                
                # Mark as active (thread-safe)
                if not _add_task(stem):
                    continue  # Already added by another thread
                
                # Submit to ThreadPool
                executor.submit(task_wrapper, stem, "Ingest", _run_ingest, file_path, mode, style)

def _detect_client(filename: str) -> str:
    """Detect client from filename using CLIENT_PATTERNS from config."""
    name_lower = filename.lower()
    for pattern, client_name in getattr(config, "CLIENT_PATTERNS", {}).items():
        if pattern in name_lower:
            return client_name
    return "unknown"


def _run_ingest(file_path, mode, style):
    original_stem = file_path.stem
    # Generate unique job ID using same pattern as cloud pipeline
    # Format: {slugified_stem}-{timestamp} e.g., "episode1-20241228T121500Z"
    job_id = new_job_id(original_stem)
    
    # Auto-detect client from filename (original name)
    client = _detect_client(original_stem)
    
    # Calculate due date based on client defaults
    import datetime
    client_defaults = getattr(config, "CLIENT_DEFAULTS", {})
    client_config = client_defaults.get(client, client_defaults.get("unknown", {}))
    due_days = client_config.get("due_date_days", 7)
    due_date = (datetime.datetime.now() + datetime.timedelta(days=due_days)).strftime("%Y-%m-%d")
    
    try:
        source_path = str(file_path)
        review_required = (mode in {"REVIEW", "REMOTE_REVIEW"}) or ("/02_human_review/" in source_path.lower())
        remote_review_required = (mode == "REMOTE_REVIEW") or ("/03_remote_review/" in source_path.lower())
        
        vault_path = str(config.VAULT_VIDEOS / file_path.name)
        
        # 1. Init DB with Job ID as key (legacy jobs table)
        meta = {
            "original_filename": file_path.name,
            "original_stem": original_stem,  # For display purposes
            "vault_path": vault_path,
            "mode": "REVIEW" if review_required else "AUTO",
            "style": style,
            "source_path": source_path,
            "review_required": review_required,
            "remote_review_required": remote_review_required,
            "ingest_time": publisher.iso_now()
        }
        
        omega_db.update(job_id, stage="INGEST", status="Processing Audio", progress=10.0, meta=meta, subtitle_style=style, client=client, due_date=due_date)
        
        # 2. Run Transcriber with Job ID (returns video_path, audio_path, thumbnail_path)
        skeleton_path = transcriber.run(file_path, job_id=job_id)
        
        # Get thumbnail path (generated during ingest)
        thumbnail_dir = config.VAULT_DIR / "Thumbnails"
        thumbnail_path = thumbnail_dir / f"{file_path.stem}.jpg"
        
        # Get video duration
        duration = transcriber.get_audio_duration(config.VAULT_DIR / "Audio" / f"{file_path.stem}.wav")
        
        # 3. Create Program record (if not exists)
        existing_program = omega_db.get_program_by_video(vault_path)
        if existing_program:
            program_id = existing_program['id']
            logger.info(f"📚 Using existing program: {program_id}")
        else:
            program_id = omega_db.create_program(
                title=original_stem,
                original_filename=file_path.name,
                video_path=vault_path,
                thumbnail_path=str(thumbnail_path) if thumbnail_path and thumbnail_path.exists() else None,
                duration_seconds=duration,
                client=client,
                due_date=due_date,
                default_style=style,
                meta={
                    "ingest_time": publisher.iso_now(),
                    "review_required": review_required,
                    "remote_review_required": remote_review_required,
                }
            )
            logger.info(f"📚 Created program: {program_id}")
        
        # 4. Create Track record (links to legacy job)
        target_lang = getattr(config, "OMEGA_TARGET_LANGUAGE", "is")
        track_id = omega_db.create_track(
            program_id=program_id,
            type='subtitle',
            language_code=target_lang,
            stage='TRANSCRIBED',
            status='Ready for Translation',
            job_id=job_id,
            meta={
                "skeleton_path": str(skeleton_path) if skeleton_path else None,
            }
        )
        logger.info(f"🎬 Created track: {track_id} ({target_lang} subtitle)")
        
        # 5. Update job meta with program/track IDs
        omega_db.update(job_id, stage="TRANSCRIBED", status="Ready for Translation", progress=30.0, 
                       meta={"program_id": program_id, "track_id": track_id})
        
    except Exception as e:
        raise e # Handled by wrapper

def _run_ingest_recovery(stem: str, video_path: Path):
    """
    Recovers a stalled ingest job where the video is already in the Vault.
    Re-runs transcription ensuring Job ID consistency.
    """
    logger.info(f"🔄 Recovering Ingest for {stem}")
    try:
        # Re-run transcriber
        # input: video_path (in Vault)
        # job_id: stem (Critical for file naming)
        transcriber.run(video_path, job_id=stem)
        
        omega_db.update(stem, stage="TRANSCRIBED", status="Ready for Translation", progress=30.0)
    except Exception as e:
        logger.error(f"❌ Recovery failed for {stem}: {e}")
        omega_db.update(stem, stage="FAILED", status=f"Recovery Failed: {str(e)}", progress=0.0)
        raise e

def process_jobs(executor):
    """
    Polls DB/Files for jobs in intermediate stages.
    """
    def is_in_cooldown(stem):
        if stem not in failure_counts: return False
        count, last_fail = failure_counts[stem]
        backoff = min(2 ** count, 60)
        if time.time() - last_fail < backoff:
            return True
        return False

    def _job_meta(job: dict) -> dict:
        meta = job.get("meta") or {}
        return meta if isinstance(meta, dict) else {}

    def _final_output_path(job: dict) -> Optional[Path]:
        meta = _job_meta(job)
        value = meta.get("final_output")
        if not value:
            return None
        try:
            return Path(str(value))
        except Exception:
            return None

    def _autocorrect_completed(stem: str, job: dict) -> bool:
        final_path = _final_output_path(job)
        if final_path and final_path.exists():
            stage_upper = (job.get("stage") or "").upper()
            if stage_upper not in {"COMPLETED", "DELIVERED"}:
                logger.info(f"✅ Auto-correcting {stem}: output exists at {final_path}")
                omega_db.update(
                    stem,
                    stage="COMPLETED",
                    status="Done",
                    progress=100.0,
                    meta={"last_error": "", "failed_at": ""},
                )
            return True
        return False

    jobs = omega_db.get_all_jobs()
    jobs_by_stem = {j.get("file_stem"): j for j in jobs if j.get("file_stem")}

    # 0.5 Detect stalled stages and trigger recovery/restart
    now = datetime.now()
    for stem, job in jobs_by_stem.items():
        if not stem:
            continue
        stage = str(job.get("stage") or "").upper()
        threshold = STAGE_STALL_THRESHOLDS.get(stage)
        if not threshold:
            continue
        status = str(job.get("status") or "")
        if _status_is_blocked(status):
            continue
        meta = _job_meta(job)
        if meta.get("halted"):
            continue

        started_at = _stage_started_at(meta, stage)
        if not started_at:
            started_at = _parse_iso(job.get("updated_at"))
        cloud_progress_at = None
        if stage in {"TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}:
            cloud_progress = meta.get("cloud_progress") if isinstance(meta.get("cloud_progress"), dict) else {}
            cloud_progress_at = _parse_iso(cloud_progress.get("updated_at")) if isinstance(cloud_progress, dict) else None
        elapsed = None
        if cloud_progress_at:
            elapsed = (now - cloud_progress_at).total_seconds()
        elif started_at:
            elapsed = (now - started_at).total_seconds()
        if elapsed is None:
            continue
        if elapsed < threshold:
            continue

        stall_count = int(meta.get("stall_restart_count") or 0)
        if stall_count >= MAX_TASK_FAILURES:
            omega_db.update(
                stem,
                stage="DEAD",
                status=f"DEAD: stalled in {stage}",
                progress=0,
                meta={
                    "halted": True,
                    "halted_at": datetime.now().isoformat(),
                    "halt_reason": f"stalled in {stage}",
                    "stall_detected_at": datetime.now().isoformat(),
                },
            )
            continue

        if stage in {"TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}:
            omega_db.update(
                stem,
                stage="TRANSLATING_CLOUD_SUBMITTED",
                status="Cloud stalled; re-triggering",
                progress=40.0,
                meta={
                    "cloud_run_execution": "",
                    "cloud_trigger_last_attempt": 0,
                    "cloud_trigger_attempts": int(meta.get("cloud_trigger_attempts") or 0) + 1,
                    "cloud_stall_detected_at": datetime.now().isoformat(),
                    "stall_restart_count": stall_count + 1,
                },
            )
            continue

        omega_db.update(
            stem,
            status=f"Stalled in {stage}; restarting manager",
            progress=0,
            meta={
                "stall_stage": stage,
                "stall_detected_at": datetime.now().isoformat(),
                "stall_restart_count": stall_count + 1,
            },
        )
        _request_manager_restart(force=True)

    # 1. Recover stalled ingest jobs (video already moved to Vault)
    now = datetime.now()
    for job in jobs:
        stem = job.get("file_stem")
        if not stem or stem in active_tasks:
            continue
        if (job.get("stage") or "").upper() != "INGEST":
            continue
        if is_in_cooldown(stem):
            continue
        meta = _job_meta(job)
        if meta.get("halted"):
            continue
        updated_at = _parse_iso(job.get("updated_at"))
        if not updated_at or (now - updated_at).total_seconds() < INGEST_STALL_SECONDS:
            continue

        skel_path = config.VAULT_DATA / f"{stem}_SKELETON.json"
        if skel_path.exists():
            logger.warning("⚠️ Ingest stalled for %s but skeleton exists. Advancing stage.", stem)
            omega_db.update(stem, stage="TRANSCRIBED", status="Ready for Translation", progress=30.0)
            continue
        
        # Check if video already in vault (prefer stored vault path / original filename)
        video_vault = None
        vault_path = meta.get("vault_path")
        if vault_path:
            candidate = Path(str(vault_path))
            if candidate.exists():
                video_vault = candidate
        if video_vault is None:
            original_filename = meta.get("original_filename")
            if original_filename:
                candidate = config.VAULT_VIDEOS / original_filename
                if candidate.exists():
                    video_vault = candidate
        if video_vault is None:
            original_stem = meta.get("original_stem")
            if original_stem:
                candidate = _find_vault_video(original_stem)
                if candidate and candidate.exists():
                    video_vault = candidate

        if video_vault and video_vault.exists():
            logger.warning("⚠️ Ingest stalled for %s but video exists in Vault. Retrying ingest.", stem)
            _add_task(stem)
            executor.submit(task_wrapper, stem, "IngestRecovery", _run_ingest_recovery, stem, video_vault)
            continue

    # 2. TRANSCRIBED -> TRANSLATING (submit to Cloud Run or local worker)
    # Calculate initial translating count for concurrency gate
    MAX_CONCURRENT_TRANSLATIONS = int(os.environ.get("OMEGA_MAX_CONCURRENT_TRANSLATIONS", "2"))
    translating_stages = {"TRANSLATING", "TRANSLATING_CLOUD_SUBMITTED", "CLOUD_TRANSLATING", "CLOUD_REVIEWING"}
    currently_translating = sum(
        1 for _, j in jobs_by_stem.items()
        if (j.get("stage") or "").upper() in translating_stages
    )

    # 1. TRANSCRIBED -> TRANSLATING (submit to Cloud Run or local worker)
    for job in jobs:
        stem = job.get("file_stem")
        if not stem or stem in active_tasks:
            continue
        if is_in_cooldown(stem):
            continue
        meta = _job_meta(job)
        if meta.get("halted"):
            continue
        
        # Skeleton check
        skel = config.VAULT_DATA / f"{stem}_SKELETON.json"
        if not skel.exists():
            continue

        if _autocorrect_completed(stem, job):
            # Stop re-triggering from stale skeletons.
            done_skel = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
            if skel.exists():
                if done_skel.exists():
                    done_skel = config.VAULT_DATA / f"{stem}_SKELETON_DONE.bak_{int(time.time())}.json"
                try:
                    shutil.move(str(skel), str(done_skel))
                except Exception:
                    pass
            continue

        stage = (job.get("stage") or "").upper()
        if stage in {"QUEUED", "INGEST", ""}:
            omega_db.update(stem, stage="TRANSCRIBED", status="Ready for Translation", progress=30.0)
            stage = "TRANSCRIBED"
        if stage not in {"TRANSCRIBED", "TRANSLATING"}:
            continue

        if not _cloud_pipeline_enabled():
            # Preflight: local translator requires audio; don't thrash retries if it's missing.
            audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
            if not audio_path.exists():
                omega_db.update(stem, status="Blocked: Missing audio", progress=30.0, meta={"blocked_reason": "missing_audio"})
                continue
        
        target_language = job.get("target_language", "is")
        
        # Concurrency gate: max 2 translations at a time to prevent API quota issues
        if currently_translating >= MAX_CONCURRENT_TRANSLATIONS:
            # Skip this job for now; it will be picked up in the next cycle
            logger.debug(f"⏳ Waiting to translate {stem}: {currently_translating} jobs already translating (max {MAX_CONCURRENT_TRANSLATIONS})")
            continue
        
        _add_task(stem)
        currently_translating += 1 # Local increment for this loop
        
        if _cloud_pipeline_enabled():
            executor.submit(task_wrapper, stem, "Translate (Cloud)", _run_translate_cloud, skel, stem, target_language)
        else:
            executor.submit(task_wrapper, stem, "Translate", _run_translate, skel, stem, target_language)

    # 1b. CLOUD TRANSLATION/REVIEW -> REVIEWED (download approved.json)
    if _cloud_pipeline_enabled():
        ensure_google_application_credentials()
        try:
            storage_client = storage.Client()
        except Exception as e:
            logger.error("❌ Failed to initialize GCS client: %s", e)
            storage_client = None

        if storage_client:
            for job_entry in jobs:
                stem = job_entry.get("file_stem")
                if not stem:
                    continue

                stage = (job_entry.get("stage") or "").upper()
                if stage not in {
                    "TRANSLATING_CLOUD_SUBMITTED",
                    "CLOUD_TRANSLATING",
                    "CLOUD_REVIEWING",
                }:
                    continue

                meta = _job_meta(job_entry)
                if meta.get("halted"):
                    continue

                cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
                if not cloud_job_id:
                    continue

                bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=str(cloud_job_id))

                # If Cloud Run auto-trigger is configured, retry triggering any submitted jobs
                # that don't have an execution recorded yet (e.g., first-time setup).
                cloud_run_job = getattr(config, "OMEGA_CLOUD_RUN_JOB", "").strip()
                cloud_run_region = getattr(config, "OMEGA_CLOUD_RUN_REGION", "us-central1").strip() or "us-central1"
                cloud_run_project = getattr(config, "OMEGA_CLOUD_PROJECT", "").strip() or None
                if (
                    cloud_run_job
                    and stage == "TRANSLATING_CLOUD_SUBMITTED"
                    and not meta.get("cloud_run_execution")
                ):
                    now = time.time()
                    attempts = int(meta.get("cloud_trigger_attempts") or 0)
                    last_attempt = float(meta.get("cloud_trigger_last_attempt") or 0.0)
                    backoff = min(2 ** max(0, attempts), 300.0)
                    if now - last_attempt >= backoff:
                        omega_db.update(stem, status="Triggering cloud worker…")
                        args = [
                            "--job-id",
                            str(cloud_job_id),
                            "--bucket",
                            bucket_name,
                            "--prefix",
                            prefix,
                        ]
                        try:
                            resp = run_cloud_run_job(
                                job_name=cloud_run_job,
                                region=cloud_run_region,
                                project=cloud_run_project,
                                args=args,
                            )
                            omega_db.update(
                                stem,
                                status="Cloud worker started",
                                meta={
                                    "cloud_run_execution": resp.get("name"),
                                    "cloud_triggered_at": datetime.now().isoformat(),
                                    "cloud_trigger_attempts": attempts,
                                    "cloud_trigger_last_attempt": now,
                                },
                            )
                        except Exception as e:
                            omega_db.update(
                                stem,
                                status=f"Cloud trigger failed: {e}",
                                meta={
                                    "cloud_trigger_error": str(e),
                                    "cloud_trigger_failed_at": datetime.now().isoformat(),
                                    "cloud_trigger_attempts": attempts + 1,
                                    "cloud_trigger_last_attempt": now,
                                },
                            )

                # Optional: reflect cloud progress into the dashboard.
                try:
                    if blob_exists(storage_client, bucket_name, paths.progress_json()):
                        progress_payload = download_json(
                            storage_client,
                            bucket=bucket_name,
                            blob_name=paths.progress_json(),
                        )
                        if isinstance(progress_payload, dict):
                            status = progress_payload.get("status")
                            progress = progress_payload.get("progress")
                            if status or progress is not None:
                                cloud_progress = {
                                    "stage": progress_payload.get("stage"),
                                    "status": status,
                                    "progress": progress,
                                    "updated_at": progress_payload.get("updated_at"),
                                    "meta": progress_payload.get("meta") if isinstance(progress_payload.get("meta"), dict) else {},
                                }
                                omega_db.update(
                                    stem,
                                    status=str(status) if status else None,
                                    progress=float(progress) if progress is not None else None,
                                    meta={
                                        "cloud_stage": progress_payload.get("stage"),
                                        "cloud_progress": cloud_progress,
                                        "cloud_last_poll_at": datetime.now().isoformat(),
                                    },
                                )
                except Exception:
                    pass

                # Pull editor report as soon as it's available.
                if not job_entry.get("editor_report"):
                    try:
                        if blob_exists(storage_client, bucket_name, paths.editor_report_json()):
                            report_payload = download_json(
                                storage_client,
                                bucket=bucket_name,
                                blob_name=paths.editor_report_json(),
                            )
                            omega_db.update(stem, editor_report=json.dumps(report_payload or {}))
                            logger.info("✅ Cloud editor report downloaded: %s", paths.editor_report_json())
                    except Exception as e:
                        logger.error("❌ Failed to download cloud editor report for %s: %s", stem, e)

                local_approved = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
                if local_approved.exists():
                    continue

                try:
                    if not blob_exists(storage_client, bucket_name, paths.approved_json()):
                        continue
                    approved_payload = download_json(
                        storage_client,
                        bucket=bucket_name,
                        blob_name=paths.approved_json(),
                    )
                    local_approved.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_approved, "w", encoding="utf-8") as f:
                        json.dump(approved_payload, f, indent=2, ensure_ascii=False)

                    omega_db.update(
                        stem,
                        stage="REVIEWED",
                        status="Editor Approved (Cloud)",
                        progress=70.0,
                        meta={
                            "cloud_job_id": str(cloud_job_id),
                            "cloud_bucket": bucket_name,
                            "cloud_prefix": prefix,
                            "cloud_approved_path": str(local_approved),
                        },
                    )
                    logger.info("✅ Cloud approved downloaded: %s", local_approved.name)
                    
                    # Check if human review is required
                    if _trigger_review_portal(stem, meta, job_entry):
                        # Job is waiting for human review - don't proceed to finalize yet
                        continue
                        
                except Exception as e:
                    logger.error("❌ Failed to download cloud approval for %s: %s", stem, e)

            # Backfill editor reports for cloud-completed jobs that already advanced stages.
            for job_entry in jobs:
                stem = job_entry.get("file_stem")
                if not stem or job_entry.get("editor_report"):
                    continue
                meta = _job_meta(job_entry)
                if meta.get("cloud_stage") != "CLOUD_DONE":
                    continue
                cloud_job_id = meta.get("cloud_job_id") or meta.get("gcs_job_id")
                if not cloud_job_id:
                    continue
                bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=str(cloud_job_id))
                try:
                    if not blob_exists(storage_client, bucket_name, paths.editor_report_json()):
                        continue
                    report_payload = download_json(
                        storage_client,
                        bucket=bucket_name,
                        blob_name=paths.editor_report_json(),
                    )
                    omega_db.update(stem, editor_report=json.dumps(report_payload or {}))
                    logger.info("✅ Cloud editor report backfilled: %s", paths.editor_report_json())
                except Exception as e:
                    logger.error("❌ Failed to backfill cloud editor report for %s: %s", stem, e)

            # 1c. HUMAN REVIEW PORTAL -> Check for reviewed translations
            for job_entry in jobs:
                stem = job_entry.get("file_stem")
                if not stem:
                    continue
                meta = _job_meta(job_entry)
                
                # Only check jobs waiting for human review
                if not meta.get("review_notification_sent"):
                    continue
                if meta.get("human_review_complete"):
                    continue
                
                # Check for reviewed.json in GCS
                cloud_job_id = meta.get("cloud_job_id") or meta.get("review_portal_job_id")
                if not cloud_job_id:
                    continue
                    
                bucket_name = str(meta.get("cloud_bucket") or config.OMEGA_JOBS_BUCKET).strip()
                prefix = str(meta.get("cloud_prefix") or config.OMEGA_JOBS_PREFIX).strip()
                reviewed_blob = f"{prefix}/{cloud_job_id}/{cloud_job_id}_REVIEWED.json"
                
                try:
                    if not blob_exists(storage_client, bucket_name, reviewed_blob):
                        continue
                    
                    # Download the reviewed translation
                    reviewed_payload = download_json(
                        storage_client,
                        bucket=bucket_name,
                        blob_name=reviewed_blob,
                    )
                    
                    # Save to local approved location
                    local_approved = config.TRANSLATED_DONE_DIR / f"{stem}_APPROVED.json"
                    with open(local_approved, "w", encoding="utf-8") as f:
                        json.dump(reviewed_payload.get("segments", reviewed_payload), f, indent=2, ensure_ascii=False)
                    
                    omega_db.update(
                        stem,
                        stage="REVIEWED",
                        status="Human Review Complete",
                        progress=72.0,
                        meta={
                            "human_review_complete": True,
                            "human_review_completed_at": datetime.now().isoformat(),
                            "human_reviewer": reviewed_payload.get("approved_by", "Reviewer"),
                        },
                    )
                    logger.info("✅ Human review complete: %s (by %s)", stem, reviewed_payload.get("approved_by", "Reviewer"))
                    
                except Exception as e:
                    logger.error("❌ Failed to check human review for %s: %s", stem, e)

    # 2. TRANSLATED -> REVIEWING (Editor)
    for trans in config.EDITOR_DIR.glob("*.json"):
        if _is_hidden_artifact(trans):
            continue
        if trans.name.endswith("_SKELETON.json"): continue
        if trans.name.endswith("_APPROVED.json"): continue
        
        parts = trans.stem.split("_")
        if len(parts) < 2: continue
        stem = "_".join(parts[:-1])
        
        if stem in active_tasks: continue
        if is_in_cooldown(stem): continue
        
        # Verify with DB
        job = jobs_by_stem.get(stem) or omega_db.get_job(stem)
        if not job: 
             if trans.name.endswith("_ICELANDIC.json"):
                 stem = trans.stem.replace("_ICELANDIC", "")
             else:
                 continue

        meta = _job_meta(job)
        if meta.get("halted"):
            continue
        if _autocorrect_completed(stem, job):
            continue

        stage = (job.get("stage") or "").upper()
        if stage in {"TRANSCRIBED", "TRANSLATING"}:
            omega_db.update(stem, stage="TRANSLATED", status="Ready for Review", progress=55.0, meta={"translation_path": str(trans)})
            stage = "TRANSLATED"
        if stage not in {"TRANSLATED", "REVIEWING"}:
            continue
        
        _add_task(stem)
        executor.submit(task_wrapper, stem, "Review", _run_review, trans, stem)

    # 3. REVIEWED -> FINALIZING (Finalizer)
    review_storage_client = None
    for approved in config.TRANSLATED_DONE_DIR.glob("*_APPROVED.json"):
        if _is_hidden_artifact(approved):
            continue
        stem = approved.stem.replace("_APPROVED", "")
        if stem in active_tasks: continue
        if is_in_cooldown(stem): continue
        
        job = jobs_by_stem.get(stem) or omega_db.get_job(stem)
        if job:
            meta = _job_meta(job)
            if meta.get("halted"):
                continue
            if _autocorrect_completed(stem, job):
                continue
            stage = (job.get("stage") or "").upper()
            if stage in {"TRANSLATED", "REVIEWING"}:
                omega_db.update(stem, stage="REVIEWED", status="Editor Approved", progress=70.0)
                stage = "REVIEWED"
            if stage not in {"REVIEWED", "FINALIZING"}:
                continue

            source_path = str(meta.get("source_path") or "")
            remote_review_required = bool(meta.get("remote_review_required")) or ("/03_remote_review/" in source_path.lower())
            if remote_review_required and not meta.get("remote_review_done"):
                paths, bucket_name, prefix = _cloud_job_paths(meta)
                if not paths or not bucket_name:
                    omega_db.update(
                        stem,
                        status="Blocked: Remote review missing cloud job",
                        meta={"remote_review_error": "missing_cloud_job"},
                    )
                    continue

                if review_storage_client is None:
                    try:
                        ensure_google_application_credentials()
                        review_storage_client = storage.Client()
                    except Exception as e:
                        omega_db.update(
                            stem,
                            status=f"Blocked: Remote review auth failed ({e})",
                            meta={"remote_review_error": str(e)},
                        )
                        continue

                portal_url = _review_portal_url()
                recipients = _reviewer_emails(meta)
                if not portal_url or not recipients:
                    omega_db.update(
                        stem,
                        status="Blocked: Remote review not configured",
                        meta={
                            "remote_review_error": "missing_portal_or_email",
                            "remote_review_portal": portal_url,
                        },
                    )
                    continue

                requested = bool(meta.get("remote_review_requested"))
                last_attempt = float(meta.get("remote_review_last_attempt") or 0.0)
                now = time.time()
                if not requested and (now - last_attempt) >= 300:
                    review_payload = _build_review_payload(
                        stem=stem,
                        approved_path=approved,
                        target_language=job.get("target_language", "is"),
                        program_profile=job.get("program_profile", "standard"),
                    )
                    token = secrets.token_urlsafe(32)
                    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"
                    try:
                        upload_json(review_storage_client, bucket=bucket_name, blob_name=paths.review_json(), payload=review_payload)
                        upload_json(
                            review_storage_client,
                            bucket=bucket_name,
                            blob_name=paths.review_token_json(),
                            payload={"token": token, "expires_at": expires_at},
                        )
                        review_url = f"{portal_url.rstrip('/')}/review/{paths.job_id}?token={token}"
                        email_sent = _send_review_email(stem=stem, review_url=review_url, recipients=recipients)
                        omega_db.update(
                            stem,
                            status="Waiting for Remote Review",
                            progress=70.0,
                            meta={
                                "remote_review_requested": bool(email_sent),
                                "remote_review_sent_at": datetime.utcnow().isoformat() + "Z" if email_sent else None,
                                "remote_review_last_attempt": now,
                                "remote_review_url": review_url,
                                "remote_review_expires_at": expires_at,
                            },
                        )
                    except Exception as e:
                        omega_db.update(
                            stem,
                            status=f"Remote review send failed: {e}",
                            meta={
                                "remote_review_last_attempt": now,
                                "remote_review_error": str(e),
                            },
                        )
                    continue

                # Check for review completion - try multiple patterns
                # Pattern 1: review_corrections.json (legacy)
                # Pattern 2: {job_id}_REVIEWED.json (review portal)
                # Pattern 3: review_status.json (approval status)
                review_blob_name = None
                if blob_exists(review_storage_client, bucket_name, paths.review_corrections_json()):
                    review_blob_name = paths.review_corrections_json()
                elif blob_exists(review_storage_client, bucket_name, paths.reviewed_json()):
                    review_blob_name = paths.reviewed_json()
                elif blob_exists(review_storage_client, bucket_name, paths.review_status_json()):
                    # If only status exists, check if approved
                    try:
                        status_data = download_json(review_storage_client, bucket=bucket_name, blob_name=paths.review_status_json())
                        if status_data.get("status") == "approved":
                            review_blob_name = paths.reviewed_json()  # Try to get segments from _REVIEWED.json
                    except Exception:
                        pass
                
                if review_blob_name and blob_exists(review_storage_client, bucket_name, review_blob_name):
                    try:
                        corrections_payload = download_json(
                            review_storage_client,
                            bucket=bucket_name,
                            blob_name=review_blob_name,
                        )
                        # Handle both formats: {"corrections": [...]} or {"segments": [...]}
                        if "corrections" in corrections_payload:
                            corrections = corrections_payload.get("corrections", [])
                            applied, comment_count = _apply_remote_corrections(
                                approved_path=approved,
                                corrections=corrections or [],
                            )
                        elif "segments" in corrections_payload:
                            # _REVIEWED.json from portal has full segments
                            # Replace entire approved file with reviewed segments
                            with open(approved, 'w', encoding='utf-8') as f:
                                json.dump(corrections_payload, f, indent=2, ensure_ascii=False)
                            applied = len(corrections_payload.get("segments", []))
                            comment_count = 0
                            logger.info(f"✅ Applied reviewed segments from portal for {stem}")
                        else:
                            corrections = []
                            applied, comment_count = 0, 0
                            
                        omega_db.update(
                            stem,
                            status="Remote Review Applied",
                            progress=70.0,
                            meta={
                                "remote_review_done": True,
                                "remote_review_applied": applied,
                                "remote_review_comment_count": comment_count,
                                "remote_review_received_at": datetime.utcnow().isoformat() + "Z",
                            },
                        )
                    except Exception as e:
                        omega_db.update(
                            stem,
                            status=f"Remote review apply failed: {e}",
                            meta={"remote_review_error": str(e)},
                        )
                    continue

                omega_db.update(stem, status="Waiting for Remote Review", progress=70.0)
                continue

        if (config.SRT_DIR / f"{stem}.srt").exists(): continue
        if (config.VIDEO_DIR / f"{stem}_SUBBED.mp4").exists(): continue
            
        _add_task(stem)
        executor.submit(task_wrapper, stem, "Finalize", _run_finalize, approved, stem)

    # 4. FINALIZED -> BURNING (Publisher)
    # 4. FINALIZED -> BURNING (Publisher)
    # Calculate initial burning count for concurrency gate (M2 Max optimized)
    MAX_CONCURRENT_BURNS = int(os.environ.get("OMEGA_MAX_CONCURRENT_BURNS", "2"))
    currently_burning = sum(
        1 for _, j in jobs_by_stem.items()
        if (j.get("stage") or "").upper() == "BURNING"
    )

    for srt in config.SRT_DIR.glob("*.srt"):
        if _is_hidden_artifact(srt):
            continue
        if srt.name.startswith("DONE_"): continue
        stem = srt.stem
        if stem in active_tasks:
            # logger.debug(f"Skipping {stem} (Active)")
            continue
        if is_in_cooldown(stem): continue
        
        job = jobs_by_stem.get(stem) or omega_db.get_job(stem)
        if job and _autocorrect_completed(stem, job):
            # Stop re-triggering from stale SRTs.
            done_srt = srt.parent / f"DONE_{srt.name}"
            try:
                shutil.move(str(srt), str(done_srt))
            except Exception:
                pass
            continue

        legacy_output = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"
        if legacy_output.exists():
            # Auto-Correction: If video exists but DB says otherwise, mark as DONE.
            if job and job.get("stage") != "COMPLETED":
                logger.info(f"✅ Auto-Correcting Status for {stem} (Video Exists)")
                omega_db.update(
                    stem,
                    stage="COMPLETED",
                    status="Done",
                    progress=100.0,
                    meta={"final_output": str(legacy_output), "last_error": "", "failed_at": ""},
                )
            # Stop re-triggering from stale SRTs.
            done_srt = srt.parent / f"DONE_{srt.name}"
            try:
                shutil.move(str(srt), str(done_srt))
            except Exception:
                pass
            continue

        # Pre-Burn Gate
        if not job:
            continue

        meta = _job_meta(job)
        if meta.get("halted"):
            continue

        stage = (job.get("stage") or "").upper()
        if stage in {"REVIEWED", "FINALIZING"}:
            omega_db.update(stem, stage="FINALIZED", status="Ready to Burn", progress=90.0)
            stage = "FINALIZED"
        if stage not in {"FINALIZED", "BURNING"}:
            continue

        status = job.get("status", "")
        source_path = str(meta.get("source_path") or "")
        review_required = bool(meta.get("review_required")) or ("/02_human_review/" in source_path.lower())
        burn_approved = bool(meta.get("burn_approved")) or (status == "Approved for Burn")

        if review_required and not burn_approved:
            if status != "Waiting for Burn Approval":
                omega_db.update(
                    stem,
                    status="Waiting for Burn Approval",
                    progress=90.0,
                    meta={"review_required": review_required},
                )
                logger.info(f"🛑 Pre-Burn Gate: Stopping {stem} (Waiting for Approval)")
            continue

        # Concurrency gate: max 2 burns at a time to prevent hardware contention (M2 Max)
        if currently_burning >= MAX_CONCURRENT_BURNS:
             logger.debug(f"⏳ Waiting to burn {stem}: {currently_burning} jobs already burning (max {MAX_CONCURRENT_BURNS})")
             continue

        logger.info(f"🔍 Found candidate for burning: {stem}")
        currently_burning += 1 # Local increment
             
        _add_task(stem)
        executor.submit(task_wrapper, stem, "Burn", _run_burn, srt, stem)

def _run_translate(skel, stem, target_language):
    logger.info(f"🧠 Translating: {stem} to {target_language}")
    omega_db.update(stem, stage="TRANSLATING", status=f"Translating ({target_language})", progress=40.0)
    
    job = omega_db.get_job(stem) or {}
    program_profile = (job.get("program_profile") or "standard").strip() or "standard"
    output_path = translator.translate(
        skel,
        target_language_code=target_language,
        program_profile=program_profile,
    )
    
    done_skel = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
    shutil.move(str(skel), str(done_skel))
    
    omega_db.update(stem, stage="TRANSLATED", status="Ready for Review", progress=55.0, meta={"translation_path": str(output_path)})

def _run_translate_cloud(skel, stem, target_language):
    """
    Cloud-first path: upload job artifacts to GCS and let the cloud worker do
    Translation + Chief Editor, writing approved.json back to GCS.

    The local manager polls and downloads the approved payload into
    TRANSLATED_DONE_DIR, so the existing finalize/burn stages remain unchanged.
    """
    logger.info("☁️ Submitting cloud translation: %s (%s)", stem, str(target_language).upper())

    ensure_google_application_credentials()

    bucket_name = config.OMEGA_JOBS_BUCKET
    prefix = config.OMEGA_JOBS_PREFIX
    job_id = stem
    paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)

    storage_client = storage.Client()

    with open(skel, "r", encoding="utf-8") as f:
        skeleton_payload = json.load(f)

    job = omega_db.get_job(stem) or {}
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    program_profile = (job.get("program_profile") or "standard").strip() or "standard"
    polish_pass = _polish_pass_enabled(meta)
    music_detect = _is_truthy(getattr(config, "OMEGA_CLOUD_MUSIC_DETECT", True))

    job_payload = {
        "project_id": config.OMEGA_CLOUD_PROJECT,
        "stem": stem,
        "job_id": stem,
        "target_language_code": str(target_language or "is").strip().lower() or "is",
        "program_profile": program_profile,
        "translator_model": config.MODEL_TRANSLATOR,
        "editor_model": config.MODEL_EDITOR,
        "polish_model": config.MODEL_POLISH,
        "music_detect": music_detect,
        "review_required": bool(meta.get("review_required")),
        "polish_pass": polish_pass,
        "created_at": datetime.now().isoformat(),
    }

    omega_db.update(
        stem,
        stage="TRANSLATING_CLOUD_SUBMITTED",
        status="Uploading to Cloud",
        progress=40.0,
        meta={"cloud_job_id": job_id, "cloud_bucket": bucket_name, "cloud_prefix": prefix},
    )

    upload_json(storage_client, bucket=bucket_name, blob_name=paths.job_json(), payload=job_payload)
    upload_json(storage_client, bucket=bucket_name, blob_name=paths.skeleton_json(), payload=skeleton_payload)
    upload_json(
        storage_client,
        bucket=bucket_name,
        blob_name=paths.progress_json(),
        payload={
            "stage": "TRANSLATING_CLOUD_SUBMITTED",
            "status": "Submitted",
            "progress": 40.0,
            "updated_at": datetime.now().isoformat(),
            "meta": job_payload,
        },
    )

    # Stop re-triggering from stale skeletons.
    done_skel = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
    shutil.move(str(skel), str(done_skel))

    omega_db.update(
        stem,
        stage="TRANSLATING_CLOUD_SUBMITTED",
        status="Submitted to Cloud",
        progress=40.0,
        meta={
            "cloud_job_id": job_id,
            "cloud_bucket": bucket_name,
            "cloud_prefix": prefix,
            "cloud_job_gcs": f"gs://{bucket_name}/{paths.job_json()}",
        },
    )

    cloud_run_job = getattr(config, "OMEGA_CLOUD_RUN_JOB", "").strip()
    cloud_run_region = getattr(config, "OMEGA_CLOUD_RUN_REGION", "us-central1").strip() or "us-central1"
    cloud_run_project = getattr(config, "OMEGA_CLOUD_PROJECT", "").strip() or None

    if cloud_run_job:
        args = [
            "--job-id",
            job_id,
            "--bucket",
            bucket_name,
            "--prefix",
            prefix,
        ]
        logger.info("🚀 Triggering Cloud Run job: %s (%s)", cloud_run_job, cloud_run_region)
        try:
            resp = run_cloud_run_job(
                job_name=cloud_run_job,
                region=cloud_run_region,
                project=cloud_run_project,
                args=args,
            )
            omega_db.update(
                stem,
                status="Cloud worker started",
                meta={
                    "cloud_run_execution": resp.get("name"),
                    "cloud_triggered_at": datetime.now().isoformat(),
                },
            )
        except Exception as e:
            logger.error("❌ Cloud Run trigger failed for %s: %s", stem, e)
            omega_db.update(
                stem,
                status=f"Cloud trigger failed: {e}",
                meta={
                    "cloud_trigger_error": str(e),
                    "cloud_trigger_failed_at": datetime.now().isoformat(),
                },
            )
        return

    trigger = str(os.environ.get("OMEGA_CLOUD_TRIGGER_COMMAND") or "").strip()
    if trigger:
        cmd = trigger.format(job_id=job_id, bucket=bucket_name, prefix=prefix)
        logger.info("🚀 Triggering cloud worker command: %s", cmd)
        subprocess.run(cmd, shell=True, check=True)

def _run_review(trans, stem):
    logger.info(f"🕵️‍♂️ Reviewing: {stem}")
    omega_db.update(stem, stage="REVIEWING", status="AI Reviewing", progress=60.0)
    
    editor.review(trans)
    omega_db.update(stem, stage="REVIEWED", status="Editor Approved", progress=70.0)

def _run_finalize(approved, stem):
    logger.info(f"🎬 Finalizing: {stem}")
    omega_db.update(stem, stage="FINALIZING", status="Finalizing", progress=80.0)
    
    # QA constant
    IDEAL_CPS = 14.0
    
    job = omega_db.get_job(stem)
    target_language = job.get("target_language", "is") if job else "is"
    
    finalizer.finalize(approved, target_language=target_language)
    omega_db.update(stem, stage="FINALIZED", status="Ready to Burn", progress=90.0)

def _run_burn(srt, stem):
    logger.info(f"🔥 Burning: {stem}")
    omega_db.update(stem, stage="BURNING", status="Burning", progress=95.0, meta={"burn_started_at": datetime.now().isoformat()})
    
    job = omega_db.get_job(stem)
    subtitle_style = job.get("subtitle_style", "Classic") if job else "Classic"
    delivery_profile = job.get("delivery_profile") if job else None  # Read from job settings
    
    meta = job.get("meta", {}) if job else {}
    video_path = None
    vault_path = meta.get("vault_path")
    if vault_path:
        candidate = Path(str(vault_path))
        if candidate.exists():
            video_path = candidate
    if video_path is None:
        original_filename = meta.get("original_filename")
        if original_filename:
            candidate = config.VAULT_VIDEOS / original_filename
            if candidate.exists():
                video_path = candidate

    if video_path is None:
        original_stem = meta.get("original_stem")
        video_path = _find_vault_video(original_stem or stem)

    if video_path is None:
        raise FileNotFoundError(f"Video not found for {stem}")

    output_video = publisher.publish(video_path, srt, subtitle_style=subtitle_style, delivery_profile=delivery_profile)
    omega_db.update(
        stem,
        stage="COMPLETED",
        status="Done",
        progress=100.0,
        meta={
            "final_output": str(output_video),
            "burn_end_time": datetime.now().isoformat(),
            "last_error": "",
            "failed_at": "",
        },
    )
    logger.info(f"✅ Job Complete: {output_video.name}")
    
    done_srt = srt.parent / f"DONE_{srt.name}"
    shutil.move(str(srt), str(done_srt))

import signal

def cleanup(signum, frame):
    logger.info(f"🛑 Received signal {signum}. Cleaning up...")
    sys.exit(0)

def main():
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    
    logger.info("🚀 Omega Manager Started (Async Mode)")
    if _cloud_pipeline_enabled():
        logger.info(
            "☁️ Cloud pipeline enabled (bucket=%s, prefix=%s, job=%s, region=%s, project=%s)",
            config.OMEGA_JOBS_BUCKET,
            config.OMEGA_JOBS_PREFIX,
            config.OMEGA_CLOUD_RUN_JOB or "unset",
            config.OMEGA_CLOUD_RUN_REGION,
            config.OMEGA_CLOUD_PROJECT or "default",
        )
    else:
        logger.info("🧩 Cloud pipeline disabled (set OMEGA_CLOUD_PIPELINE=1 to enable).")
    
    # Initialize ThreadPool
    # 22 workers allows for full concurrency of 20 client jobs + 2 overhead
    # Most steps are I/O bound (Cloud API), so high thread count is safe.
    with ThreadPoolExecutor(max_workers=22) as executor:
        while True:
            try:
                system_health.update_heartbeat("omega_manager")

                if RESTART_FLAG.exists():
                    force = RESTART_FORCE_FLAG.exists()
                    if active_tasks and not force:
                        logger.warning(f"🔄 Restart requested; waiting for {len(active_tasks)} active tasks to finish...")
                        time.sleep(2)
                        continue
                    try:
                        RESTART_FLAG.unlink()
                    except Exception:
                        pass
                    try:
                        RESTART_FORCE_FLAG.unlink()
                    except Exception:
                        pass
                    logger.warning("🔄 Restarting Omega Manager now%s...", " (forced)" if force else "")
                    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])

                if not config.critical_paths_ready(require_write=True):
                    logger.error("❌ Critical paths not writable/ready (external drive unmounted or permissions). Pausing.")
                    time.sleep(10)
                    continue
                
                # Check disk space before processing (warn if low)
                disk_ok, disk_gb = config.disk_space_available(min_gb=20.0)
                if not disk_ok:
                    logger.warning(f"⚠️ Low disk space: {disk_gb:.1f}GB available (need 20GB+). Pausing ingestion.")
                    # Still process existing jobs but don't ingest new ones
                    process_jobs(executor)
                    time.sleep(30)
                    continue
                
                ingest_new_files(executor)
                process_jobs(executor)
                
                time.sleep(2) # Faster polling since it's non-blocking
                
            except KeyboardInterrupt:
                logger.info("🛑 Manager Stopped by User")
                break
            except Exception as e:
                logger.error(f"🔥 Critical Manager Failure: {e}")
                time.sleep(10)

if __name__ == "__main__":
    with ProcessLock("omega_manager"):
        main()
