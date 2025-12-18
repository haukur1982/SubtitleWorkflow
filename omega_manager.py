import time
import os
import sys
import json
import logging
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional
import config
import omega_db
import system_health
from gcp_auth import ensure_google_application_credentials
from gcs_jobs import GcsJobPaths, new_job_id, upload_json, download_json, blob_exists
from lock_manager import ProcessLock
from concurrent.futures import ThreadPoolExecutor
from google.cloud import storage

# Import Workers
from workers import transcriber, translator, editor, finalizer, publisher

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

MAX_TASK_FAILURES = 5


def _cloud_pipeline_enabled() -> bool:
    return str(os.environ.get("OMEGA_CLOUD_PIPELINE", "")).strip().lower() in {"1", "true", "yes", "on"}

def task_wrapper(stem, task_name, func, *args, **kwargs):
    """
    Wraps a worker function to handle active_tasks cleanup, error logging, and backoff.
    """
    try:
        logger.info(f"🚀 Starting Async Task: {task_name} for {stem}")
        func(*args, **kwargs)
        
        # Success: Reset failure count
        if stem in failure_counts:
            del failure_counts[stem]
            
    except Exception as e:
        logger.error(f"❌ Async Task Failed ({task_name}): {e}")
        
        # Increment failure count
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
                    "halted_at": datetime.now().isoformat(),
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
        if stem in active_tasks:
            active_tasks.remove(stem)

def ingest_new_files(executor):
    """
    Scans INBOX for new video files.
    """
    EXTENSIONS = {".mp3", ".wav", ".mp4", ".m4a", ".mov", ".mkv", ".mpg", ".mpeg", ".moc"}
    
    WATCH_MAP = {
        # Auto Pilot
        config.INBOX_DIR / "01_AUTO_PILOT" / "Classic": ("AUTO", "Classic"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Modern_Look": ("AUTO", "Modern"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Apple_TV": ("AUTO", "Apple"),
        # Manual Review
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Classic": ("REVIEW", "Classic"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Modern_Look": ("REVIEW", "Modern"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Apple_TV": ("REVIEW", "Apple"),
    }
    
    for folder, (mode, style) in WATCH_MAP.items():
        if not folder.exists(): continue
        
        for file_path in folder.iterdir():
            if file_path.name.startswith("."): continue
            if file_path.suffix.lower() in EXTENSIONS:
                # Stability Check
                try:
                    initial_size = file_path.stat().st_size
                    time.sleep(1)
                    if file_path.stat().st_size != initial_size: continue
                except FileNotFoundError: continue

                stem = file_path.stem
                if stem in active_tasks:
                    logger.debug(f"⚠️ Skipping {stem}: Already active")
                    continue 

                logger.info(f"📥 Found Candidate: {file_path.name} in {folder}")
                
                # Mark as active
                active_tasks.add(stem)
                
                # Submit to ThreadPool
                executor.submit(task_wrapper, stem, "Ingest", _run_ingest, file_path, mode, style)

def _run_ingest(file_path, mode, style):
    stem = file_path.stem
    try:
        # 1. Init DB
        meta = {
            "original_filename": file_path.name,
            "mode": mode,
            "style": style,
            "source_path": str(file_path),
            "ingest_time": publisher.iso_now()
        }
        omega_db.update(stem, stage="INGEST", status="Processing Audio", progress=10.0, meta=meta, subtitle_style=style)
        
        # 2. Run Transcriber
        skeleton_path = transcriber.run(file_path)
        
        # 3. Update DB
        omega_db.update(stem, stage="TRANSCRIBED", status="Ready for Translation", progress=30.0)
        
    except Exception as e:
        raise e # Handled by wrapper

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
            if (job.get("stage") or "").upper() != "COMPLETED":
                logger.info(f"✅ Auto-correcting {stem}: output exists at {final_path}")
                omega_db.update(stem, stage="COMPLETED", status="Done", progress=100.0)
            return True
        return False

    jobs = omega_db.get_all_jobs()
    jobs_by_stem = {j.get("file_stem"): j for j in jobs if j.get("file_stem")}

    # 1. TRANSCRIBED -> TRANSLATING
    for skel in config.VAULT_DATA.glob("*_SKELETON.json"):
        stem = skel.stem.replace("_SKELETON", "")
        if stem in active_tasks: continue
        if is_in_cooldown(stem): continue
        
        job = jobs_by_stem.get(stem) or omega_db.get_job(stem)
        if not job: continue

        meta = _job_meta(job)
        if meta.get("halted"):
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
        
        active_tasks.add(stem)
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
                                omega_db.update(
                                    stem,
                                    status=str(status) if status else None,
                                    progress=float(progress) if progress is not None else None,
                                    meta={"cloud_stage": progress_payload.get("stage")},
                                )
                except Exception:
                    pass

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
                except Exception as e:
                    logger.error("❌ Failed to download cloud approval for %s: %s", stem, e)

    # 2. TRANSLATED -> REVIEWING (Editor)
    for trans in config.EDITOR_DIR.glob("*.json"):
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
        
        active_tasks.add(stem)
        executor.submit(task_wrapper, stem, "Review", _run_review, trans, stem)

    # 3. REVIEWED -> FINALIZING (Finalizer)
    for approved in config.TRANSLATED_DONE_DIR.glob("*_APPROVED.json"):
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

        if (config.SRT_DIR / f"{stem}.srt").exists(): continue
        if (config.VIDEO_DIR / f"{stem}_SUBBED.mp4").exists(): continue
            
        active_tasks.add(stem)
        executor.submit(task_wrapper, stem, "Finalize", _run_finalize, approved, stem)

    # 4. FINALIZED -> BURNING (Publisher)
    # 4. FINALIZED -> BURNING (Publisher)
    for srt in config.SRT_DIR.glob("*.srt"):
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
                omega_db.update(stem, stage="COMPLETED", status="Done", progress=100.0, meta={"final_output": str(legacy_output)})
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

        mode = meta.get("mode", "AUTO")
        status = job.get("status", "")
        
        if mode == "REVIEW" and status != "Approved for Burn":
            if status != "Waiting for Burn Approval":
                omega_db.update(stem, status="Waiting for Burn Approval", progress=90.0)
                logger.info(f"🛑 Pre-Burn Gate: Stopping {stem} (Waiting for Approval)")
            continue

        logger.info(f"🔍 Found candidate for burning: {stem}")
             
        active_tasks.add(stem)
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
    job_id = new_job_id(stem)
    paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)

    storage_client = storage.Client()

    with open(skel, "r", encoding="utf-8") as f:
        skeleton_payload = json.load(f)

    job = omega_db.get_job(stem) or {}
    program_profile = (job.get("program_profile") or "standard").strip() or "standard"

    job_payload = {
        "project_id": "sermon-translator-system",
        "stem": stem,
        "target_language_code": str(target_language or "is").strip().lower() or "is",
        "program_profile": program_profile,
        "translator_model": config.MODEL_TRANSLATOR,
        "editor_model": config.MODEL_EDITOR,
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

    trigger = str(os.environ.get("OMEGA_CLOUD_TRIGGER_COMMAND") or "").strip()
    if trigger:
        cmd = trigger.format(job_id=job_id, bucket=bucket_name, prefix=prefix)
        logger.info("🚀 Triggering cloud worker: %s", cmd)
        subprocess.run(cmd, shell=True, check=True)

def _run_review(trans, stem):
    logger.info(f"🕵️‍♂️ Reviewing: {stem}")
    omega_db.update(stem, stage="REVIEWING", status="AI Reviewing", progress=60.0)
    
    editor.review(trans)
    omega_db.update(stem, stage="REVIEWED", status="Editor Approved", progress=70.0)

def _run_finalize(approved, stem):
    logger.info(f"🎬 Finalizing: {stem}")
    omega_db.update(stem, stage="FINALIZING", status="Finalizing", progress=80.0)
    
    job = omega_db.get_job(stem)
    target_language = job.get("target_language", "is") if job else "is"
    
    finalizer.finalize(approved, target_language=target_language)
    omega_db.update(stem, stage="FINALIZED", status="Ready to Burn", progress=90.0)

def _run_burn(srt, stem):
    logger.info(f"🔥 Burning: {stem}")
    omega_db.update(stem, stage="BURNING", status="Burning", progress=95.0)
    
    job = omega_db.get_job(stem)
    subtitle_style = job.get("subtitle_style", "Classic") if job else "Classic"
    
    original_filename = job.get("meta", {}).get("original_filename") if job else None
    if not original_filename:
        raise ValueError(f"Original filename not found for {stem}")
    
    video_path = config.VAULT_VIDEOS / original_filename
    # Fallback search if not in VAULT_DATA
    if not video_path.exists():
         # Try INBOX or other locations?
         # For now, assume VAULT_DATA is correct as per transcriber logic
         pass

    output_video = publisher.publish(video_path, srt, subtitle_style=subtitle_style)
    omega_db.update(stem, stage="COMPLETED", status="Done", progress=100.0, meta={"final_output": str(output_video), "burn_end_time": datetime.now().isoformat()})
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
    
    # Initialize ThreadPool
    # 4 workers allows for: 1 Ingest + 1 Transcribe + 1 Translate + 1 Burn simultaneously
    with ThreadPoolExecutor(max_workers=4) as executor:
        while True:
            try:
                system_health.update_heartbeat("omega_manager")

                restart_flag = config.BASE_DIR / "heartbeats" / "omega_manager.restart"
                if restart_flag.exists():
                    if active_tasks:
                        logger.warning(f"🔄 Restart requested; waiting for {len(active_tasks)} active tasks to finish...")
                        time.sleep(2)
                        continue
                    try:
                        restart_flag.unlink()
                    except Exception:
                        pass
                    logger.warning("🔄 Restarting Omega Manager now...")
                    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])

                if not config.critical_paths_ready(require_write=True):
                    logger.error("❌ Critical paths not writable/ready (external drive unmounted or permissions). Pausing.")
                    time.sleep(10)
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
