"""
Mini Pipeline Test - 5 Segments Only
This script creates a minimal test case with exactly 5 segments to verify
the entire Claude pipeline works without running a 20-minute full video test.
"""
import json
import os
import sys
from pathlib import Path

# Mini test data - 5 segments only
MINI_SKELETON = {
    "segments": [
        {
            "id": 1,
            "text": "Welcome to this special broadcast.",
            "start": 0.0,
            "end": 2.5,
            "words": [{"word": "Welcome", "start": 0.0, "end": 0.5}]
        },
        {
            "id": 2,
            "text": "Today we're talking about faith.",
            "start": 2.6,
            "end": 5.0,
            "words": [{"word": "Today", "start": 2.6, "end": 3.0}]
        },
        {
            "id": 3,
            "text": "God loves you very much.",
            "start": 5.1,
            "end": 7.5,
            "words": [{"word": "God", "start": 5.1, "end": 5.5}]
        },
        {
            "id": 4,
            "text": "He sent His son Jesus for you.",
            "start": 7.6,
            "end": 10.0,
            "words": [{"word": "He", "start": 7.6, "end": 7.8}]
        },
        {
            "id": 5,
            "text": "Thank you for watching. God bless you.",
            "start": 10.1,
            "end": 13.0,
            "words": [{"word": "Thank", "start": 10.1, "end": 10.4}]
        }
    ]
}

MINI_JOB = {
    "job_id": "MINI_TEST_CLAUDE",
    "target_language": "is",
    "program_profile": "standard",
    "subtitle_style": "Classic",
    "polish_pass": True
}

def create_mini_test():
    """Create mini test artifacts in GCS"""
    import gcp_auth
    gcp_auth.ensure_google_application_credentials()
    from google.cloud import storage
    
    job_id = "MINI_TEST_CLAUDE"
    bucket_name = "omega-jobs-subtitle-project"
    prefix = f"jobs/{job_id}"
    
    client = storage.Client(project="sermon-translator-system")
    bucket = client.bucket(bucket_name)
    
    # Clear old test artifacts
    blobs = list(bucket.list_blobs(prefix=prefix))
    print(f"🗑️ Clearing {len(blobs)} old test files...")
    for blob in blobs:
        blob.delete()
    
    # Upload mini test data
    print(f"📤 Uploading mini test data (5 segments)...")
    bucket.blob(f"{prefix}/job.json").upload_from_string(json.dumps(MINI_JOB, indent=2))
    print("   Uploaded: job.json")
    
    # Upload skeleton
    blob = bucket.blob(f"{prefix}/skeleton.json")
    blob.upload_from_string(json.dumps(MINI_SKELETON, indent=2, ensure_ascii=False), content_type="application/json")
    print("   Uploaded: skeleton.json")
    
    # NOTE: Phase 2 skip removed. Testing actual Gemini 3 Flash fix.
    
    print(f"✅ Mini test artifacts ready in GCS: {prefix}")
    print(f"\nTo run the mini test:")
    print(f"  python3 trigger_mini_test.py")
    
    return job_id

def trigger_mini_test():
    """Trigger Cloud Run job with mini test"""
    import gcp_auth
    gcp_auth.ensure_google_application_credentials()
    import cloud_run_jobs
    
    job_id = "MINI_TEST_CLAUDE"
    
    print(f"🚀 Triggering Cloud Run job for mini test ({job_id})...")
    result = cloud_run_jobs.run_cloud_run_job(
        job_name="omega-cloud-worker",
        region="us-central1",
        project="sermon-translator-system",
        args=[
            "--job-id", job_id,
            "--bucket", "omega-jobs-subtitle-project",
            "--prefix", "jobs",
        ]
    )
    
    execution_path = result.get('metadata', {}).get('name', '')
    execution_id = execution_path.split('/')[-1] if execution_path else 'Unknown'
    
    print(f"✅ Mini test execution: {execution_id}")
    print(f"\nMonitor with:")
    print(f"  gcloud logging read 'labels.\"run.googleapis.com/execution_name\"=\"{execution_id}\"' --project=sermon-translator-system --format='value(textPayload)' --order=asc")
    
    return execution_id

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "trigger":
        trigger_mini_test()
    else:
        create_mini_test()
