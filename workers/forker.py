import os
import shutil
import logging
from pathlib import Path
import config
import omega_db
from profiles import get_language_policy

# Configure logging
logger = logging.getLogger(__name__)

class Forker:
    """
    Spawns multiple child jobs from a single Master job.
    Uses Symlinks to avoid duplicating media files.
    """
    def __init__(self, master_stem):
        self.master_stem = master_stem
        self.master_job = omega_db.get_job(master_stem)
        if not self.master_job:
            raise ValueError(f"Master job {master_stem} not found")

    def fork(self, target_languages: list[str]):
        """
        Creates child jobs for the given target languages.
        """
        results = []
        meta = self.master_job.get("meta") or {}
        original_filename = meta.get("original_filename")
        if not original_filename:
            logger.warning(f"Master {self.master_stem} has no original_filename. Media linking may fail.")
        
        # Paths
        master_audio = config.VAULT_AUDIO / f"{self.master_stem}.wav"
        master_skeleton = config.VAULT_DATA / f"{self.master_stem}_SKELETON.json"
        
        if not master_skeleton.exists():
            # If skeleton doesn't exist, check _SKELETON_DONE
            master_skeleton_done = config.VAULT_DATA / f"{self.master_stem}_SKELETON_DONE.json"
            if master_skeleton_done.exists():
                master_skeleton = master_skeleton_done
            else:
                 raise FileNotFoundError(f"Master Skeleton not found for {self.master_stem}")

        for lang in target_languages:
            lang = lang.lower()
            if lang == self.master_job.get("target_language", "is") and not self.master_job.get("master_id"):
                continue # Skip if same as master (unless master is English and we fork to English SDH?)
            
            child_stem = f"{self.master_stem}_{lang.upper()}"
            child_dir = config.JOBS_DIR / child_stem # Assuming config has path, or use Path("jobs")
            if not getattr(config, "JOBS_DIR", None):
                 child_dir = Path("jobs") / child_stem
            
            logger.info(f"Forking {self.master_stem} -> {child_stem} ({lang})")
            
            try:
                # 1. Create Directories (Vault Data, Job Dir)
                child_dir.mkdir(parents=True, exist_ok=True)
                
                # 2. Symlink Audio (Crucial for Transcriber/Translator checks)
                # target: VAULT_AUDIO / child.wav -> points to master.wav
                child_audio = config.VAULT_AUDIO / f"{child_stem}.wav"
                if not child_audio.exists():
                    try:
                        os.symlink(master_audio.resolve(), child_audio)
                        logger.info(f"   Linked Audio: {child_audio}")
                    except OSError as e:
                        logger.warning(f"   Symlink failed (using copy): {e}")
                        shutil.copy2(master_audio, child_audio)

                # 3. Copy Skeleton (The "DNA")
                child_skeleton = config.VAULT_DATA / f"{child_stem}_SKELETON.json"
                if not child_skeleton.exists():
                    shutil.copy2(master_skeleton, child_skeleton)
                    logger.info(f"   Copied Skeleton: {child_skeleton}")

                # 4. Symlink Video (if original exists)
                if original_filename:
                     # Create a symlink in Vault Videos? No, wait. 
                     # Only if we need to rename it. 
                     # Usually we don't strictly require video for translation (audio is key).
                     # But for burning, we need input.
                     # The Burner uses `meta.original_filename` to find VAULT_VIDEOS/<file>.
                     # We can just reuse the SAME filename in meta! No need to symlink video files if names match?
                     # Wait, `finalizer` or `publisher` might expect {stem}.mp4 if working blindly.
                     # But our system uses `meta['original_filename']`.
                     pass
                     
                # 5. DB Upsert
                policy = get_language_policy(lang)
                default_mode = policy.get("mode", "sub")
                
                # If we are forking, we are assuming Transcription is done.
                # Stage -> TRANSCRIBED (Ready for Translation)
                
                omega_db.upsert(
                    child_stem,
                    stage="TRANSCRIBED",
                    status=f"Forked from {self.master_stem}",
                    progress=30.0,
                    target_language=lang,
                    subtitle_style="Classic", # Can be overridden
                    meta={
                        "master_id": self.master_stem,
                        "original_filename": original_filename, # Use SAME master video
                        "forked_at": self._now(),
                        "preferred_mode": default_mode,
                        "preferred_voice": policy.get("voice"),
                        "source": "fork"
                    }
                )
                results.append(child_stem)
                
            except Exception as e:
                logger.error(f"Failed to fork {child_stem}: {e}")
                
        return results

    def _now(self):
        from datetime import datetime
        return datetime.now().isoformat()
