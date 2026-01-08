"""
Omega Review Portal
===================
A delightful web interface for reviewing and approving subtitle translations.

Endpoints:
- GET  /review/<job_id>?token=<token>  - View review interface
- GET  /api/job/<job_id>               - Get job data (JSON)
- POST /api/job/<job_id>/save          - Save draft
- POST /api/job/<job_id>/approve       - Approve and finalize
- GET  /api/audio/<job_id>/<index>     - Stream audio clip
"""

import os
import json
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_file, abort

app = Flask(__name__)

# Configuration
GCS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
GCS_PREFIX = os.environ.get("OMEGA_JOBS_PREFIX", "jobs")
SECRET_KEY = os.environ.get("OMEGA_REVIEW_SECRET", "omega-review-secret-2024")
TOKEN_EXPIRY_HOURS = 72

# Lazy GCS client initialization
_storage_client = None
_bucket = None


def get_bucket():
    """Get GCS bucket with lazy initialization."""
    global _storage_client, _bucket
    if _bucket is None:
        # Try to load credentials from service account file
        sa_file = Path(__file__).parent.parent.parent / "service_account.json"
        if sa_file.exists():
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(sa_file))
        
        from google.cloud import storage
        _storage_client = storage.Client()
        _bucket = _storage_client.bucket(GCS_BUCKET)
    return _bucket


# =============================================================================
# Token Management
# =============================================================================

def generate_token(job_id: str, expiry_hours: int = TOKEN_EXPIRY_HOURS) -> tuple[str, int]:
    """Generate a secure review token for a job."""
    expiry_ts = int(time.time()) + (expiry_hours * 3600)
    payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    token = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return token, expiry_ts


def verify_token(job_id: str, token: str, expiry_ts: int) -> bool:
    """Verify a review token is valid and not expired."""
    if time.time() > expiry_ts:
        return False
    expected_payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    expected_token = hashlib.sha256(expected_payload.encode()).hexdigest()[:32]
    return token == expected_token


# =============================================================================
# GCS Helpers
# =============================================================================

def get_job_data(job_id: str) -> dict:
    """Fetch job data from GCS."""
    bucket = get_bucket()
    
    # Try multiple naming patterns for skeleton
    skeleton_patterns = [
        f"{GCS_PREFIX}/{job_id}/{job_id}_SKELETON.json",
        f"{GCS_PREFIX}/{job_id}/skeleton.json",
    ]
    
    skeleton_data = None
    for pattern in skeleton_patterns:
        skeleton_blob = bucket.blob(pattern)
        if skeleton_blob.exists():
            skeleton_data = json.loads(skeleton_blob.download_as_text())
            break
    
    if not skeleton_data:
        return None
    
    # Try multiple naming patterns for translation/approved
    translation_patterns = [
        f"{GCS_PREFIX}/{job_id}/{job_id}_APPROVED.json",
        f"{GCS_PREFIX}/{job_id}/approved.json",
        f"{GCS_PREFIX}/{job_id}/{job_id}_TRANSLATED.json",
        f"{GCS_PREFIX}/{job_id}/translation_draft.json",
    ]
    
    approved_data = skeleton_data  # Default to skeleton
    for pattern in translation_patterns:
        blob = bucket.blob(pattern)
        if blob.exists():
            approved_data = json.loads(blob.download_as_text())
            break
    
    # Get job metadata
    job_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/job.json")
    job_meta = json.loads(job_blob.download_as_text()) if job_blob.exists() else {}
    
    # Get editor report if exists
    report_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/editor_report.json")
    editor_report = json.loads(report_blob.download_as_text()) if report_blob.exists() else None
    
    # Handle different segment structures
    segments = approved_data.get("segments", [])
    if not segments and isinstance(approved_data, list):
        segments = approved_data
    
    return {
        "job_id": job_id,
        "program_name": job_meta.get("stem", job_id),
        "target_language": job_meta.get("target_lang", job_meta.get("target_language_code", "Unknown")),
        "skeleton": skeleton_data,
        "translation": approved_data if isinstance(approved_data, dict) else {"segments": approved_data},
        "editor_report": editor_report,
        "subtitle_count": len(segments),
        # Video preview (from Bunny Stream)
        "bunny_embed_url": job_meta.get("meta", {}).get("bunny_embed_url"),
        "has_video": bool(job_meta.get("meta", {}).get("bunny_video_id")),
    }


def save_draft(job_id: str, segments: list) -> bool:
    """Save draft edits to GCS."""
    draft_blob = get_bucket().blob(f"{GCS_PREFIX}/{job_id}/{job_id}_DRAFT.json")
    draft_data = {
        "segments": segments,
        "saved_at": datetime.utcnow().isoformat(),
        "status": "draft"
    }
    draft_blob.upload_from_string(json.dumps(draft_data, indent=2))
    return True


def approve_job(job_id: str, segments: list, reviewer_name: str = "Reviewer") -> bool:
    """Approve and finalize the job."""
    approved_blob = get_bucket().blob(f"{GCS_PREFIX}/{job_id}/{job_id}_REVIEWED.json")
    approved_data = {
        "segments": segments,
        "approved_at": datetime.utcnow().isoformat(),
        "approved_by": reviewer_name,
        "status": "approved"
    }
    approved_blob.upload_from_string(json.dumps(approved_data, indent=2))
    
    # Also update job status
    status_blob = get_bucket().blob(f"{GCS_PREFIX}/{job_id}/review_status.json")
    status_blob.upload_from_string(json.dumps({
        "status": "approved",
        "approved_at": datetime.utcnow().isoformat(),
        "approved_by": reviewer_name
    }))
    
    return True


# =============================================================================
# Routes
# =============================================================================

@app.route("/")
def index():
    """Landing page."""
    return render_template("index.html")


@app.route("/review/<job_id>")
def review(job_id: str):
    """Main review interface."""
    token = request.args.get("token", "")
    expiry = request.args.get("exp", 0)
    
    # For development, allow access without token
    if os.environ.get("OMEGA_DEV_MODE") == "1":
        pass
    else:
        try:
            expiry = int(expiry)
        except ValueError:
            abort(401, "Invalid token")
        
        if not verify_token(job_id, token, expiry):
            abort(401, "Token expired or invalid")
    
    job_data = get_job_data(job_id)
    if not job_data:
        abort(404, "Job not found")
    
    return render_template("review.html", job=job_data)


@app.route("/api/job/<job_id>")
def api_get_job(job_id: str):
    """API: Get job data."""
    job_data = get_job_data(job_id)
    if not job_data:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job_data)


@app.route("/api/job/<job_id>/save", methods=["POST"])
def api_save_draft(job_id: str):
    """API: Save draft."""
    data = request.get_json()
    segments = data.get("segments", [])
    
    if save_draft(job_id, segments):
        return jsonify({"success": True, "message": "Draft saved"})
    return jsonify({"error": "Failed to save"}), 500


@app.route("/api/job/<job_id>/approve", methods=["POST"])
def api_approve(job_id: str):
    """API: Approve and finalize."""
    data = request.get_json()
    segments = data.get("segments", [])
    reviewer = data.get("reviewer_name", "Anonymous Reviewer")
    
    if approve_job(job_id, segments, reviewer):
        return jsonify({
            "success": True, 
            "message": "Approved! The video will be burned within 30 minutes."
        })
    return jsonify({"error": "Failed to approve"}), 500


@app.route("/api/audio/<job_id>/<int:index>")
def api_audio_clip(job_id: str, index: int):
    """API: Stream audio clip for a specific subtitle."""
    # Audio clips are stored as: jobs/{job_id}/audio_clips/clip_{index:04d}.mp3
    clip_name = f"{GCS_PREFIX}/{job_id}/audio_clips/clip_{index:04d}.mp3"
    clip_blob = get_bucket().blob(clip_name)
    
    if not clip_blob.exists():
        abort(404, "Audio clip not found")
    
    # Stream from GCS
    content = clip_blob.download_as_bytes()
    from io import BytesIO
    return send_file(
        BytesIO(content),
        mimetype="audio/mpeg",
        as_attachment=False
    )


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    debug = os.environ.get("OMEGA_DEV_MODE") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
