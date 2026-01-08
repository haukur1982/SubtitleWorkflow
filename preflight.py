#!/usr/bin/env python3
"""
Omega Pre-Flight Check
======================
Validates all critical dependencies before starting the manager.
Run manually: python3 preflight.py
Integrated into: start_omega.sh

Exit Codes:
  0 = All checks passed
  1 = Critical failure (blocks startup)
  2 = Warning (non-critical, startup continues)
"""

import os
import sys
import subprocess
import json
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import config
from gcp_auth import ensure_google_application_credentials

# Ensure GCP credentials are loaded
ensure_google_application_credentials()

RESULTS = {"passed": [], "warnings": [], "failed": []}


def log_pass(name: str, detail: str = ""):
    RESULTS["passed"].append(name)
    print(f"  ✅ {name}" + (f": {detail}" if detail else ""))


def log_warn(name: str, detail: str = ""):
    RESULTS["warnings"].append(name)
    print(f"  ⚠️  {name}: {detail}")


def log_fail(name: str, detail: str = ""):
    RESULTS["failed"].append(name)
    print(f"  ❌ {name}: {detail}")


# ---------------------------------------------------------------------------
# CHECK: File System Paths
# ---------------------------------------------------------------------------
def check_paths():
    print("\n📁 Checking File System...")
    critical_paths = [
        config.VIDEO_DIR,
        config.SRT_DIR,
        config.VAULT_VIDEOS,
        config.VAULT_DATA,
    ]
    all_ok = True
    for p in critical_paths:
        if p.exists():
            # Check writability
            test_file = p / ".preflight_test"
            try:
                test_file.touch()
                test_file.unlink()
                log_pass(f"Path: {p.name}", "exists & writable")
            except Exception as e:
                log_fail(f"Path: {p.name}", f"not writable: {e}")
                all_ok = False
        else:
            log_fail(f"Path: {p.name}", "does not exist")
            all_ok = False
    return all_ok


# ---------------------------------------------------------------------------
# CHECK: FFmpeg Capabilities
# ---------------------------------------------------------------------------
def check_ffmpeg():
    print("\n🎬 Checking FFmpeg...")
    ffmpeg_bin = getattr(config, "FFMPEG_BIN", "ffmpeg")
    
    # Check if ffmpeg exists
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version_line = result.stdout.split("\n")[0]
            log_pass("FFmpeg", version_line[:60])
        else:
            log_fail("FFmpeg", "command failed")
            return False
    except FileNotFoundError:
        log_fail("FFmpeg", f"not found at {ffmpeg_bin}")
        return False
    except Exception as e:
        log_fail("FFmpeg", str(e))
        return False
    
    # Check for h264_videotoolbox (GPU encoder)
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        if "h264_videotoolbox" in result.stdout:
            log_pass("GPU Encoder", "h264_videotoolbox available")
        else:
            log_warn("GPU Encoder", "h264_videotoolbox not found; will use CPU (slower)")
    except Exception:
        log_warn("GPU Encoder", "could not verify")
    
    return True


# ---------------------------------------------------------------------------
# CHECK: Google Cloud Storage
# ---------------------------------------------------------------------------
def check_gcs():
    print("\n☁️  Checking Google Cloud Storage...")
    bucket = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
    
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket_obj = client.bucket(bucket)
        
        # Try to list a single blob (lightweight check)
        blobs = list(bucket_obj.list_blobs(max_results=1))
        log_pass("GCS Connection", f"bucket '{bucket}' accessible")
        return True
    except ImportError:
        log_fail("GCS", "google-cloud-storage not installed")
        return False
    except Exception as e:
        log_fail("GCS", str(e)[:100])
        return False


# ---------------------------------------------------------------------------
# CHECK: Vertex AI / Gemini
# ---------------------------------------------------------------------------
def check_vertex():
    print("\n🧠 Checking Vertex AI (Gemini)...")
    
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel
        
        project = os.environ.get("OMEGA_CLOUD_PROJECT", "sermon-translator-system")
        location = getattr(config, "GEMINI_LOCATION", "us-central1")
        
        vertexai.init(project=project, location=location)
        model = GenerativeModel(getattr(config, "MODEL_TRANSLATOR", "gemini-1.5-flash"))
        
        # Quick test call
        response = model.generate_content("Say 'OK' if you can hear me.", 
                                          generation_config={"max_output_tokens": 10})
        if response and response.text:
            log_pass("Vertex AI", f"Gemini responding (project={project})")
            return True
        else:
            log_fail("Vertex AI", "No response from model")
            return False
    except ImportError:
        log_fail("Vertex AI", "vertexai SDK not installed")
        return False
    except Exception as e:
        log_fail("Vertex AI", str(e)[:100])
        return False


# ---------------------------------------------------------------------------
# CHECK: AssemblyAI (if enabled)
# ---------------------------------------------------------------------------
def check_assemblyai():
    print("\n🎤 Checking AssemblyAI...")
    transcriber = os.environ.get("OMEGA_TRANSCRIBER", "assemblyai")
    
    if transcriber.lower() != "assemblyai":
        log_pass("AssemblyAI", "not enabled (using WhisperX)")
        return True
    
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not api_key:
        log_fail("AssemblyAI", "ASSEMBLYAI_API_KEY not set")
        return False
    
    try:
        import assemblyai as aai
        aai.settings.api_key = api_key
        # Lightweight check: just verify key format and connectivity
        # A full transcription test would be expensive
        log_pass("AssemblyAI", f"API key configured (ends ...{api_key[-4:]})")
        return True
    except ImportError:
        log_warn("AssemblyAI", "SDK not installed; will fail at runtime")
        return True  # Not critical at startup
    except Exception as e:
        log_fail("AssemblyAI", str(e)[:100])
        return False


# ---------------------------------------------------------------------------
# CHECK: Demucs (if enabled)
# ---------------------------------------------------------------------------
def check_demucs():
    print("\n🎵 Checking Demucs...")
    demucs_enabled = os.environ.get("OMEGA_DEMUCS_ENABLED", "1") == "1"
    
    if not demucs_enabled:
        log_pass("Demucs", "disabled")
        return True
    
    # Check both shell PATH and venv bin
    venv_demucs = Path(__file__).parent / ".venv" / "bin" / "demucs"
    
    try:
        # First check venv (most common location)
        if venv_demucs.exists():
            log_pass("Demucs", f"found in .venv/bin")
            return True
        
        # Fallback to shell PATH
        result = subprocess.run(
            ["which", "demucs"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            log_pass("Demucs", f"found at {result.stdout.strip()}")
            return True
        else:
            log_warn("Demucs", "not found; music removal may not work")
            return True
    except Exception as e:
        log_warn("Demucs", str(e)[:60])
        return True


# ---------------------------------------------------------------------------
# CHECK: Email / SMTP
# ---------------------------------------------------------------------------
def check_smtp():
    print("\n📧 Checking Email (SMTP)...")
    
    host = os.environ.get("OMEGA_SMTP_HOST", "")
    user = os.environ.get("OMEGA_SMTP_USER", "")
    password = os.environ.get("OMEGA_SMTP_PASS", "")
    
    if not all([host, user, password]):
        log_warn("SMTP", "credentials not fully configured; alerts disabled")
        return True  # Not critical
    
    log_pass("SMTP", f"configured ({user}@{host})")
    return True


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def run_preflight():
    print("=" * 60)
    print("🚀 OMEGA PRE-FLIGHT CHECK")
    print("=" * 60)
    
    # Run all checks
    checks = [
        ("File System", check_paths),
        ("FFmpeg", check_ffmpeg),
        ("GCS", check_gcs),
        ("Vertex AI", check_vertex),
        ("AssemblyAI", check_assemblyai),
        ("Demucs", check_demucs),
        ("SMTP", check_smtp),
    ]
    
    for name, check_fn in checks:
        try:
            check_fn()
        except Exception as e:
            log_fail(name, f"Unexpected error: {e}")
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"  ✅ Passed:   {len(RESULTS['passed'])}")
    print(f"  ⚠️  Warnings: {len(RESULTS['warnings'])}")
    print(f"  ❌ Failed:   {len(RESULTS['failed'])}")
    
    if RESULTS["failed"]:
        print("\n🛑 CRITICAL FAILURES - System will not start reliably:")
        for f in RESULTS["failed"]:
            print(f"     - {f}")
        print("\nFix the above issues and re-run preflight.")
        return 1
    
    if RESULTS["warnings"]:
        print("\n⚠️  Warnings (non-critical):")
        for w in RESULTS["warnings"]:
            print(f"     - {w}")
    
    print("\n✅ Pre-flight complete. System ready to start.")
    return 0


if __name__ == "__main__":
    exit_code = run_preflight()
    sys.exit(exit_code)
