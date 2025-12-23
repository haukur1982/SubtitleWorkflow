#!/usr/bin/env python3
"""
test_cloud_transcriber.py — Test and compare cloud vs local transcription

Usage:
    # Run a test transcription
    python test_cloud_transcriber.py run --audio /path/to/audio.wav
    
    # Compare results with local skeleton
    python test_cloud_transcriber.py compare --local /path/to/local_skeleton.json --cloud /path/to/cloud_skeleton.json
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Configuration
BUCKET = "omega-jobs-subtitle-project"
PREFIX = "jobs"
REGION = "us-central1"


def upload_audio(audio_path: Path, job_id: str) -> bool:
    """Upload audio to GCS."""
    gcs_path = f"gs://{BUCKET}/{PREFIX}/{job_id}/audio.wav"
    print(f"📤 Uploading {audio_path.name} to {gcs_path}")
    result = subprocess.run(
        ["gsutil", "cp", str(audio_path), gcs_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"   ❌ Failed: {result.stderr}")
        return False
    print("   ✅ Upload complete")
    return True


def trigger_transcription(job_id: str) -> bool:
    """Trigger the cloud transcription job."""
    print(f"🚀 Triggering transcription for job: {job_id}")
    result = subprocess.run(
        [
            "gcloud", "run", "jobs", "execute", "omega-transcriber",
            "--region", REGION,
            f"--args=--job-id={job_id},--bucket={BUCKET},--prefix={PREFIX}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"   ❌ Failed: {result.stderr}")
        return False
    print("   ✅ Job triggered")
    return True


def poll_for_completion(job_id: str, timeout: int = 1800) -> bool:
    """Poll for transcription completion."""
    progress_path = f"gs://{BUCKET}/{PREFIX}/{job_id}/transcription_progress.json"
    skeleton_path = f"gs://{BUCKET}/{PREFIX}/{job_id}/skeleton.json"
    
    print(f"⏳ Waiting for completion (timeout: {timeout}s)...")
    start = time.time()
    
    while time.time() - start < timeout:
        # Check if skeleton exists
        result = subprocess.run(
            ["gsutil", "stat", skeleton_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("   ✅ Skeleton ready!")
            return True
        
        # Check progress
        result = subprocess.run(
            ["gsutil", "cat", progress_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                progress = json.loads(result.stdout)
                stage = progress.get("stage", "?")
                status = progress.get("status", "?")
                pct = progress.get("progress", 0)
                print(f"   [{stage}] {status} ({pct:.0f}%)")
                
                if stage == "ERROR":
                    print(f"   ❌ Error: {progress.get('error', 'Unknown')}")
                    return False
            except json.JSONDecodeError:
                pass
        
        time.sleep(10)
    
    print("   ❌ Timeout waiting for completion")
    return False


def download_skeleton(job_id: str, output_path: Path) -> bool:
    """Download the skeleton from GCS."""
    gcs_path = f"gs://{BUCKET}/{PREFIX}/{job_id}/skeleton.json"
    print(f"📥 Downloading skeleton to {output_path}")
    result = subprocess.run(
        ["gsutil", "cp", gcs_path, str(output_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"   ❌ Failed: {result.stderr}")
        return False
    print("   ✅ Download complete")
    return True


def compare_skeletons(local_path: Path, cloud_path: Path) -> dict:
    """Compare local and cloud skeletons for timestamp accuracy."""
    with open(local_path) as f:
        local = json.load(f)
    with open(cloud_path) as f:
        cloud = json.load(f)
    
    local_segs = local.get("segments", [])
    cloud_segs = cloud.get("segments", [])
    
    print(f"\n📊 Comparison Results")
    print(f"   Local segments: {len(local_segs)}")
    print(f"   Cloud segments: {len(cloud_segs)}")
    
    if len(local_segs) != len(cloud_segs):
        print(f"   ⚠️ Segment count mismatch!")
    
    # Compare timestamps
    drifts = []
    for i, (l, c) in enumerate(zip(local_segs, cloud_segs)):
        if l.get("start") is not None and c.get("start") is not None:
            diff_start = abs(l["start"] - c["start"])
            diff_end = abs(l.get("end", 0) - c.get("end", 0))
            max_diff = max(diff_start, diff_end)
            drifts.append(max_diff)
            
            if max_diff > 0.1:
                print(f"   DRIFT seg {i+1}: start={diff_start:.3f}s, end={diff_end:.3f}s")
    
    if drifts:
        avg_drift = sum(drifts) / len(drifts)
        max_drift = max(drifts)
        pct_under_100ms = sum(1 for d in drifts if d <= 0.1) / len(drifts) * 100
        
        print(f"\n   Average drift: {avg_drift*1000:.1f}ms")
        print(f"   Max drift: {max_drift*1000:.1f}ms")
        print(f"   Under 100ms: {pct_under_100ms:.1f}%")
        
        if pct_under_100ms >= 95:
            print(f"\n   ✅ PASS: Timestamp quality acceptable")
            return {"pass": True, "avg_drift_ms": avg_drift*1000, "pct_under_100ms": pct_under_100ms}
        else:
            print(f"\n   ❌ FAIL: Too much timestamp drift")
            return {"pass": False, "avg_drift_ms": avg_drift*1000, "pct_under_100ms": pct_under_100ms}
    
    print("   ⚠️ No comparable segments")
    return {"pass": False, "error": "No comparable segments"}


def cmd_run(args):
    """Run a test transcription."""
    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"❌ Audio file not found: {audio_path}")
        return 1
    
    job_id = args.job_id or f"TEST-{int(time.time())}"
    output_path = Path(args.output) if args.output else Path(f"cloud_skeleton_{job_id}.json")
    
    print(f"\n🧪 Running cloud transcription test")
    print(f"   Job ID: {job_id}")
    print(f"   Audio: {audio_path}")
    print(f"   Output: {output_path}")
    print("")
    
    if not upload_audio(audio_path, job_id):
        return 1
    
    if not trigger_transcription(job_id):
        return 1
    
    if not poll_for_completion(job_id):
        return 1
    
    if not download_skeleton(job_id, output_path):
        return 1
    
    print(f"\n✅ Test complete! Skeleton saved to: {output_path}")
    print(f"\nTo compare with local, run:")
    print(f"  python test_cloud_transcriber.py compare --local <local_skeleton.json> --cloud {output_path}")
    return 0


def cmd_compare(args):
    """Compare local and cloud skeletons."""
    local_path = Path(args.local)
    cloud_path = Path(args.cloud)
    
    if not local_path.exists():
        print(f"❌ Local skeleton not found: {local_path}")
        return 1
    if not cloud_path.exists():
        print(f"❌ Cloud skeleton not found: {cloud_path}")
        return 1
    
    result = compare_skeletons(local_path, cloud_path)
    return 0 if result.get("pass") else 1


def main():
    parser = argparse.ArgumentParser(description="Test cloud transcriber")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run a test transcription")
    run_parser.add_argument("--audio", required=True, help="Path to audio file")
    run_parser.add_argument("--job-id", help="Job ID (default: TEST-<timestamp>)")
    run_parser.add_argument("--output", help="Output path for skeleton")
    
    # Compare command
    compare_parser = subparsers.add_parser("compare", help="Compare skeletons")
    compare_parser.add_argument("--local", required=True, help="Path to local skeleton")
    compare_parser.add_argument("--cloud", required=True, help="Path to cloud skeleton")
    
    args = parser.parse_args()
    
    if args.command == "run":
        return cmd_run(args)
    elif args.command == "compare":
        return cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
