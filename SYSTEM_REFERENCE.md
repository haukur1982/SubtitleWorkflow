SYSTEM REFERENCE: SubtitleWorkflow
=================================
Last updated: 2025-12-23
Owner: /Users/haukurhauksson/SubtitleWorkflow

Purpose
-------
End-to-end system to ingest English video/audio, transcribe with WhisperX, translate and review with Gemini/Vertex AI, finalize broadcast-compliant subtitles, and optionally burn subtitles into video. Supports local-only and cloud-assisted translation/editor flow. Includes remote review capability and dashboard monitoring.

Current State (Snapshot)
------------------------
- System is running under launchd (LaunchAgent) and a watchdog.
- Transcription recovery is active for:
  - Job: Walk Before Him and Be Blameless - Joe Sweet - 1H
  - Stage: INGEST
  - Status: Manual recovery: re-running transcription
  - Progress: 12%
  - Last update in DB: 2025-12-23 13:04:41
- WhisperX is running in a separate Python 3.9 environment. The manager runs in Python 3.11 venv.
- The system will auto-retry stalled steps. WhisperX now has an idle timeout to prevent infinite hangs.

High-Level Architecture
-----------------------
1) Local ingest and transcription (WhisperX) on macOS.
2) Cloud translation/editor/polish (Cloud Run Job: omega-cloud-worker) OR local translation/editor (legacy).
3) Local finalization (SRT/ASS) and burn (FFmpeg).
4) Dashboard and monitoring with heartbeat watchdog.

Data Flow (Local + Cloud)
-------------------------
1. INBOX Watch:
   - Watches symlinked inbox folders in 1_INBOX/*
   - Moves media to Vault, extracts audio

2. Transcribe (WhisperX):
   - Produces *_SKELETON.json (segments with start/end/text)
   - Optional safety pass for the first N seconds

3. Translation & Review:
   - Cloud pipeline (default):
     - Upload job.json + skeleton.json to GCS
     - Cloud worker runs: translate -> editor -> optional polish
     - approved.json is written to GCS
     - Local manager downloads approved.json
   - Local pipeline (legacy fallback):
     - Local translator -> editor

4. Finalize:
   - approved.json -> SRT/ASS
   - CPS/line splitting; rescue merges for fast segments

5. Burn:
   - SRT/ASS burned into video using FFmpeg
   - Output delivered to 4_DELIVERY

Storage Layout
--------------
Repo: /Users/haukurhauksson/SubtitleWorkflow
Symlinks to external drive:
- 1_INBOX -> /Volumes/Extreme SSD/Omega_Work/1_INBOX
- 2_VAULT -> /Volumes/Extreme SSD/Omega_Work/2_VAULT
- 3_EDITOR -> /Volumes/Extreme SSD/Omega_Work/3_EDITOR
- 3_TRANSLATED_DONE -> /Volumes/Extreme SSD/Omega_Work/3_TRANSLATED_DONE
- 4_DELIVERY -> /Volumes/Extreme SSD/Omega_Work/4_DELIVERY
- 99_ERRORS -> /Volumes/Extreme SSD/Omega_Work/99_ERRORS

Key Vault folders:
- 2_VAULT/Videos (original media)
- 2_VAULT/Audio (extracted wav)
- 2_VAULT/Data (skeletons and artifacts)

Database
--------
SQLite DB: production.db
Table: jobs
- file_stem (primary key)
- stage, status, progress, updated_at
- meta (JSON): timeline entries, cloud data, QA metrics, etc.
- target_language, program_profile, subtitle_style
- editor_report (JSON string)

meta fields used by the pipeline include:
- stage_timeline, status_timeline, cloud_stage_timeline
- source_path, original_filename, review_required
- cloud_job_id, cloud_bucket, cloud_prefix
- cloud_progress, cloud_run_execution
- qa_srt, qa_caps
- final_output

Runtime Processes
-----------------
- omega_manager.py: Orchestrates ingest, translation, finalization, burn
- dashboard.py: UI + API
- process_watchdog.py: Restarts manager/dashboard if heartbeats stop
- caffeinate: Keeps macOS awake while manager runs

Supervisor
----------
LaunchAgent: launchd/com.omega.subtitleworkflow.plist
Script: scripts/omega_supervisor.sh
Effect: system auto-starts at login/reboot and keeps running

Code Map (Core Files)
---------------------
Top-level
- start_omega.sh
  - Starts dashboard + manager, sets env, starts watchdog and caffeinate
  - Chooses Python venv, sets OMEGA_WHISPER_BIN
- stop_all.sh
  - Stops manager, dashboard, watchdog, caffeinate

Core runtime
- omega_manager.py
  - Primary orchestration engine
  - Ingest, translate, review, finalize, burn
  - Cloud job submission/polling
  - Stage stall detection and recovery
- dashboard.py
  - Web UI, API endpoints, job inspection
  - Writes heartbeats and renders monitoring UI
- omega_db.py
  - DB schema, update/get functions
  - Tracks stage/status timeline data
- config.py
  - Paths, binaries, models, cloud config
  - Reads env vars, default settings

Transcription and ingest
- workers/transcriber.py
  - Moves video to Vault, extracts audio
  - Runs WhisperX and saves skeleton
  - Safety pass for missing early segments
  - Idle timeout to prevent hangs

Translation and editing
- workers/translator.py (local/legacy path)
  - Vertex-based translation with caching
  - Checkpointed translation batches
- workers/editor.py
  - Chief Editor pass (Vertex)
  - Constraint-aware CPS targets
  - Context window (prev/next segments)

Finalization and burn
- workers/finalizer.py
  - Approved JSON -> SRT/ASS
  - CPS rescue merges, line splitting, QA
- workers/publisher.py
  - FFmpeg burn logic

Cloud pipeline
- omega_cloud_worker.py
  - Cloud Run job: translate -> editor -> polish
  - Music detection and constraint-aware edits
  - Writes approved.json + editor_report.json
- gcs_jobs.py
  - Defines GCS job paths and helpers
- cloud_run_jobs.py
  - Triggers Cloud Run Jobs via REST API
- gcp_auth.py
  - Loads service account json if present

Remote review
- review_portal.py
  - Flask app for external reviewer
  - Reads review.json from GCS and writes corrections.json
- email_utils.py
  - SMTP helper for review notifications

Monitoring/health
- system_health.py
  - Heartbeats + disk space check
- process_watchdog.py
  - Restarts manager/dashboard when heartbeats stop

Cloud Artifacts (GCS)
---------------------
Bucket: omega-jobs-subtitle-project
Prefix: jobs
Job ID: {slugified_stem}-{timestamp}
Artifacts:
- job.json
- skeleton.json
- translation_checkpoint.json
- translation_draft.json
- editor_report.json
- approved.json
- progress.json
- review.json
- review_token.json
- review_corrections.json

Dashboard/API
-------------
- http://127.0.0.1:8080
- /api/jobs
- /api/health
- /api/burn, /api/finalize
- /api/surgical/save (dashboard edits -> re-finalize)

Configuration (Key Env Vars)
----------------------------
Runtime:
- OMEGA_PYTHON: python binary used by start_omega.sh
- OMEGA_WHISPER_BIN: path to whisperx CLI
- OMEGA_WHISPER_MODEL: Whisper model name (default: large-v3)
- OMEGA_WHISPER_COMPUTE: compute type (default: float32)
- OMEGA_WHISPER_DEVICE: device (cpu/mps/cuda)
- TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD: required for whisperx VAD loading

Cloud:
- OMEGA_CLOUD_PIPELINE (1/0)
- OMEGA_CLOUD_RUN_JOB, OMEGA_CLOUD_RUN_REGION, OMEGA_CLOUD_PROJECT
- OMEGA_JOBS_BUCKET, OMEGA_JOBS_PREFIX
- OMEGA_CLOUD_POLISH_MODE (review/all)
- OMEGA_CLOUD_MUSIC_DETECT (1/0)

Transcription:
- OMEGA_ASR_IDLE_TIMEOUT (seconds)
- If unset, idle timeout scales with audio duration (max 4h) to avoid false stalls.
- OMEGA_ASR_SAFETY_PASS (1/0)
- OMEGA_ASR_SAFETY_SECONDS
- OMEGA_ASR_SAFETY_GAP
- OMEGA_ASR_SAFETY_COVERAGE
- OMEGA_ASR_SAFETY_FIRST_GAP
- OMEGA_ASR_SAFETY_VAD_ONSET
- OMEGA_ASR_SAFETY_VAD_OFFSET
- OMEGA_ASR_SAFETY_CHUNK_SIZE

Ingest:
- OMEGA_INGEST_STABILITY_CHECKS (default: 3)
- OMEGA_INGEST_STABILITY_DELAY (default: 1.0s)
- OMEGA_INGEST_MIN_AGE (default: 3.0s)

Stall recovery:
- OMEGA_INGEST_STALL_SECONDS (default: 1800s / 30min)
- OMEGA_STALL_TRANSLATING (default: 5400s / 90min)
- OMEGA_STALL_CLOUD_SUBMITTED (default: 5400s / 90min)
- OMEGA_STALL_CLOUD (default: 5400s / 90min)
- OMEGA_STALL_CLOUD_REVIEWING (default: 7200s / 2h)
- OMEGA_STALL_REVIEWING (default: 10800s / 3h)
- OMEGA_STALL_FINALIZING (default: 10800s / 3h)
- OMEGA_STALL_BURNING (default: 21600s / 6h)

SMTP (remote review):
- OMEGA_SMTP_HOST, OMEGA_SMTP_PORT
- OMEGA_SMTP_USER, OMEGA_SMTP_PASS
- OMEGA_SMTP_FROM
- OMEGA_REVIEW_PORTAL_URL
- OMEGA_REVIEWER_EMAIL

Cloud Models
------------
- config.MODEL_TRANSLATOR
- config.MODEL_EDITOR
- config.MODEL_POLISH
- config.GEMINI_LOCATION

Overlay Rendering
-----------------
- OMEGA_OVERLAY_WORKERS (default: cpu_count-1, capped at 4)
- OMEGA_OVERLAY_CHUNK_SIZE (default: 100 frames)

Cloud Review
------------
- OMEGA_CLOUD_EDITOR_MAX_ATTEMPTS (default: 3)

Known Fragilities
-----------------
1) WhisperX binary is outside the venv
   - Current fix: OMEGA_WHISPER_BIN pinned to 3.9 install
   - Risk: mismatch between manager runtime and WhisperX runtime

2) External SSD mount dependency
   - If /Volumes/Extreme SSD is not mounted, manager pauses
   - LaunchAgent can start before SSD mounts

3) Long-running CPU transcription
   - Transcription can take hours on CPU; stalls now auto-recovered

4) Cloud dependencies
   - Cloud translation requires working GCP credentials and networking
   - GCS and Cloud Run errors can block translation

5) Multiple Python runtimes
   - Python 3.11 venv for manager/dashboard
   - Python 3.9 for WhisperX
   - Needs consolidation for long-term stability

6) No local alerting beyond logs
   - If not watching, failures are only in logs

Shortcomings (Technical and Operational)
----------------------------------------
- No built-in alerting (email/Slack) for failed jobs or stalls
- WhisperX still CPU-bound; GPU not used
- Cloud pipeline depends on local credentials JSON
- Some legacy scripts are still in repo and not used (cloud_brain.py, legacy/)
- LaunchAgent may start before external drive is mounted

Stability Improvements Implemented
----------------------------------
- WhisperX idle timeout
- Automatic ingest recovery and stage stall recovery
- Forced manager restarts for stalled stages
- Watchdog for manager/dashboard
- Mac sleep prevention while running
- LaunchAgent for auto-start and reboot recovery
- Python 3.11 venv for core system
- Explicit WhisperX path to prevent missing binary errors

Recommended Next Actions
------------------------
1) Consolidate WhisperX into a stable runtime (either:
   - install whisperx in the venv, or
   - isolate WhisperX in its own service container)
2) Add alerting (email/Slack) for stalled or DEAD jobs
3) Add mount-wait loop before starting manager when SSD is missing
4) Add a health endpoint to confirm whisperx availability
5) Add job-level SLA timers visible in dashboard

Appendix: Misc / Utility Scripts
--------------------------------
- manual_finalize_burn.py
- merge_timing.py
- analyze_srt.py
- analyze_srt_standards.py
- generate_english_srt.py
- monitor.sh, monitor_v2.sh
- debug_* scripts and audit files
- legacy/ and legacy_backup/
