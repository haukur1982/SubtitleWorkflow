import time
import sys
import logging
import shutil
from pathlib import Path
import config
import omega_db
import system_health
from lock_manager import ProcessLock
from concurrent.futures import ThreadPoolExecutor

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
        
        if count > 5:
            logger.error(f"🛑 Job {stem} failed {count} times. Moving to ERROR state.")
            omega_db.update(stem, status=f"Fatal Error: {e}", progress=0)
            # Remove from active tasks to allow manual retry later if needed
            # But for now, we keep it in failure_counts to prevent immediate retry
        else:
            logger.warning(f"⚠️ Job {stem} failed {count} times. Backing off for {backoff}s...")
            omega_db.update(stem, status=f"Error (Retry {count}/5): {e}", progress=0)
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
        config.INBOX_DIR / "01_AUTO_PILOT" / "Classic_Look": ("AUTO", "RUV_BOX"),
        config.INBOX_DIR / "01_AUTO_PILOT" / "Modern_Look": ("AUTO", "OMEGA_MODERN"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Classic_Look": ("REVIEW", "RUV_BOX"),
        config.INBOX_DIR / "02_HUMAN_REVIEW" / "Modern_Look": ("REVIEW", "OMEGA_MODERN"),
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
            "ingest_time": publisher.iso_now()
        }
        omega_db.update(stem, stage="INGEST", status="Processing Audio", progress=10.0, meta=meta)
        
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

    # 1. TRANSCRIBED -> TRANSLATING
    for skel in config.VAULT_DATA.glob("*_SKELETON.json"):
        stem = skel.stem.replace("_SKELETON", "")
        if stem in active_tasks: continue
        if is_in_cooldown(stem): continue
        
        job = omega_db.get_job(stem)
        if not job: continue
        
        target_language = job.get("target_language", "is")
        
        active_tasks.add(stem)
        executor.submit(task_wrapper, stem, "Translate", _run_translate, skel, stem, target_language)

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
        job = omega_db.get_job(stem)
        if not job: 
             if trans.name.endswith("_ICELANDIC.json"):
                 stem = trans.stem.replace("_ICELANDIC", "")
             else:
                 continue
        
        active_tasks.add(stem)
        executor.submit(task_wrapper, stem, "Review", _run_review, trans, stem)

    # 3. REVIEWED -> FINALIZING (Finalizer)
    for approved in config.TRANSLATED_DONE_DIR.glob("*_APPROVED.json"):
        stem = approved.stem.replace("_APPROVED", "")
        if stem in active_tasks: continue
        if is_in_cooldown(stem): continue
        
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
        
        if (config.VIDEO_DIR / f"{stem}_SUBBED.mp4").exists():
            # Auto-Correction: If video exists but DB says otherwise, mark as DONE.
            job = omega_db.get_job(stem)
            if job and job.get("stage") != "COMPLETED":
                logger.info(f"✅ Auto-Correcting Status for {stem} (Video Exists)")
                omega_db.update(stem, stage="COMPLETED", status="Done", progress=100.0)
            continue
            
        logger.info(f"🔍 Found candidate for burning: {stem}")

        # Pre-Burn Gate
        job = omega_db.get_job(stem)
        mode = job.get("meta", {}).get("mode", "AUTO") if job else "AUTO"
        status = job.get("status", "") if job else ""
        
        if mode == "REVIEW" and status != "Approved for Burn":
             if status != "Waiting for Burn Approval":
                 omega_db.update(stem, status="Waiting for Burn Approval", progress=90.0)
                 logger.info(f"🛑 Pre-Burn Gate: Stopping {stem} (Waiting for Approval)")
             continue
             
        active_tasks.add(stem)
        executor.submit(task_wrapper, stem, "Burn", _run_burn, srt, stem)

def _run_translate(skel, stem, target_language):
    logger.info(f"🧠 Translating: {stem} to {target_language}")
    omega_db.update(stem, stage="TRANSLATING", status=f"Translating ({target_language})", progress=40.0)
    
    output_path = translator.translate(skel, target_language_code=target_language)
    
    done_skel = config.VAULT_DATA / f"{stem}_SKELETON_DONE.json"
    shutil.move(str(skel), str(done_skel))
    
    # DB update handled by translator or inferred by next stage

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
    omega_db.update(stem, stage="COMPLETED", status="Done", progress=100.0)
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
