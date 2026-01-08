import datetime
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from google.cloud import storage


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return "job"
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-._") or "job"


def new_job_id(stem: str) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{slugify(stem)}-{ts}"


@dataclass(frozen=True)
class GcsJobPaths:
    bucket: str
    prefix: str
    job_id: str

    def _base(self) -> str:
        pfx = (self.prefix or "").strip("/ ")
        jid = (self.job_id or "").strip("/ ")
        return f"{pfx}/{jid}" if pfx else jid

    def job_json(self) -> str:
        return f"{self._base()}/job.json"

    def skeleton_json(self) -> str:
        return f"{self._base()}/skeleton.json"

    def termbook_json(self) -> str:
        return f"{self._base()}/termbook.json"

    def translation_checkpoint_json(self) -> str:
        return f"{self._base()}/translation_checkpoint.json"

    def translation_draft_json(self) -> str:
        return f"{self._base()}/translation_draft.json"

    def editor_report_json(self) -> str:
        return f"{self._base()}/editor_report.json"

    def approved_json(self) -> str:
        return f"{self._base()}/approved.json"

    def review_json(self) -> str:
        return f"{self._base()}/review.json"

    def review_token_json(self) -> str:
        return f"{self._base()}/review_token.json"

    def review_corrections_json(self) -> str:
        return f"{self._base()}/review_corrections.json"
    
    def reviewed_json(self) -> str:
        """Pattern used by review portal: {job_id}_REVIEWED.json"""
        return f"{self._base()}/{self.job_id}_REVIEWED.json"
        
    def review_status_json(self) -> str:
        """Status file written by review portal after approval"""
        return f"{self._base()}/review_status.json"

    def progress_json(self) -> str:
        return f"{self._base()}/progress.json"


def gcs_uri(bucket: str, blob_name: str) -> str:
    name = (blob_name or "").lstrip("/")
    return f"gs://{bucket}/{name}"


def blob_exists(client: storage.Client, bucket: str, blob_name: str) -> bool:
    return client.bucket(bucket).blob(blob_name).exists(client)


def upload_json(
    client: storage.Client,
    *,
    bucket: str,
    blob_name: str,
    payload: Any,
    content_type: str = "application/json; charset=utf-8",
) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    client.bucket(bucket).blob(blob_name).upload_from_string(data, content_type=content_type)


def download_json(client: storage.Client, *, bucket: str, blob_name: str) -> Any:
    raw = client.bucket(bucket).blob(blob_name).download_as_bytes()
    return json.loads(raw.decode("utf-8"))


def try_download_json(client: storage.Client, *, bucket: str, blob_name: str) -> Optional[Any]:
    try:
        return download_json(client, bucket=bucket, blob_name=blob_name)
    except Exception:
        return None


def upload_text(
    client: storage.Client,
    *,
    bucket: str,
    blob_name: str,
    text: str,
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    client.bucket(bucket).blob(blob_name).upload_from_string(text or "", content_type=content_type)


def utc_iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def backoff_sleep(attempt: int, *, base_seconds: float = 1.7, cap_seconds: float = 45.0) -> None:
    """Standard exponential backoff for transient failures."""
    delay = min(cap_seconds, base_seconds ** max(1, attempt))
    time.sleep(delay)


def is_rate_limit_error(exc: Exception) -> bool:
    """
    Check if an exception is a rate limit / quota error.
    
    Vertex AI returns google.api_core.exceptions.ResourceExhausted for 429.
    """
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()
    
    # Check for common rate limit indicators
    if "429" in exc_str or "resourceexhausted" in exc_type:
        return True
    if "quota" in exc_str or "rate" in exc_str:
        return True
    if "too many requests" in exc_str:
        return True
    return False


def rate_limit_backoff(attempt: int, *, base_seconds: float = 15.0, cap_seconds: float = 120.0) -> None:
    """
    Extended backoff for rate limit / quota errors.
    
    Uses longer delays (15s base, 120s cap) to let quota reset.
    For batch processing of 10+ programs, this prevents quota exhaustion.
    """
    delay = min(cap_seconds, base_seconds * max(1, attempt))
    time.sleep(delay)

