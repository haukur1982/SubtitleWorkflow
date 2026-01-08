import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, jsonify, render_template, request
from google.cloud import storage

import config
from gcp_auth import ensure_google_application_credentials
from gcs_jobs import GcsJobPaths, download_json, upload_json, blob_exists


logger = logging.getLogger("OmegaReviewPortal")

app = Flask(__name__, template_folder="templates")
_storage_client: Optional[storage.Client] = None


def _get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        ensure_google_application_credentials()
        _storage_client = storage.Client()
    return _storage_client


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _validate_token(paths: GcsJobPaths, token: str) -> tuple[bool, str]:
    client = _get_storage_client()
    if not blob_exists(client, paths.bucket, paths.review_token_json()):
        return False, "missing_token"
    payload = download_json(client, bucket=paths.bucket, blob_name=paths.review_token_json())
    if not isinstance(payload, dict):
        return False, "invalid_token_payload"
    expected = str(payload.get("token") or "")
    if not expected or token != expected:
        return False, "invalid_token"
    expires_at = _parse_iso(str(payload.get("expires_at") or ""))
    if expires_at and datetime.now(timezone.utc) > expires_at:
        return False, "token_expired"
    return True, ""


@app.route("/review/<job_id>")
def review_page(job_id: str):
    token = request.args.get("token", "")
    return render_template("review_portal.html", job_id=job_id, token=token)


@app.route("/api/review/<job_id>", methods=["GET"])
def get_review(job_id: str):
    token = request.args.get("token", "")
    bucket = os.environ.get("OMEGA_JOBS_BUCKET", config.OMEGA_JOBS_BUCKET)
    prefix = os.environ.get("OMEGA_JOBS_PREFIX", config.OMEGA_JOBS_PREFIX)
    paths = GcsJobPaths(bucket=bucket, prefix=prefix, job_id=job_id)

    ok, reason = _validate_token(paths, token)
    if not ok:
        return jsonify({"error": reason}), 403

    client = _get_storage_client()
    if not blob_exists(client, bucket, paths.review_json()):
        return jsonify({"error": "review_not_found"}), 404

    payload = download_json(client, bucket=bucket, blob_name=paths.review_json())
    return jsonify(payload)


@app.route("/api/review/<job_id>", methods=["POST"])
def submit_review(job_id: str):
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token") or "")
    bucket = os.environ.get("OMEGA_JOBS_BUCKET", config.OMEGA_JOBS_BUCKET)
    prefix = os.environ.get("OMEGA_JOBS_PREFIX", config.OMEGA_JOBS_PREFIX)
    paths = GcsJobPaths(bucket=bucket, prefix=prefix, job_id=job_id)

    ok, reason = _validate_token(paths, token)
    if not ok:
        return jsonify({"error": reason}), 403

    corrections = payload.get("corrections") or []
    if not isinstance(corrections, list):
        corrections = []

    reviewer = str(payload.get("reviewer") or "").strip()
    note = str(payload.get("note") or "").strip()
    approved = bool(payload.get("approved", True))

    result_payload = {
        "job_id": job_id,
        "approved": approved,
        "reviewer": reviewer,
        "note": note,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "corrections": corrections,
    }

    client = _get_storage_client()
    upload_json(client, bucket=bucket, blob_name=paths.review_corrections_json(), payload=result_payload)
    return jsonify({"success": True})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("OMEGA_REVIEW_HOST", "0.0.0.0")
    port = int(os.environ.get("OMEGA_REVIEW_PORT", "8080"))
    app.run(host=host, port=port, debug=False, use_reloader=False)
