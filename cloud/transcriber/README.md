# Cloud Transcriber Deployment Guide

## Files Created

```
cloud/transcriber/
├── Dockerfile              # CUDA 12.1 + WhisperX + large-v3 model
├── transcriber_service.py  # Transcription job with GCS integration
├── cloudbuild.yaml         # Cloud Build configuration
├── deploy_transcriber.sh   # One-click deployment script
└── test_cloud_transcriber.py  # Test and validation tool
```

---

## Deployment Steps

### Step 1: Push to Cloud Shell

From your Mac, push the code to your repository (or upload directly):

```bash
cd /Users/haukurhauksson/SubtitleWorkflow
git add cloud/transcriber/
git commit -m "Add cloud transcriber with GPU support"
git push
```

### Step 2: Deploy from Cloud Shell

Open [Cloud Shell](https://console.cloud.google.com/cloudshell) and run:

```bash
cd SubtitleWorkflow  # or clone your repo
chmod +x cloud/transcriber/deploy_transcriber.sh
./cloud/transcriber/deploy_transcriber.sh
```

> ⚠️ **First build takes 15-20 minutes** (downloads ~3GB model + PyTorch)

---

## Test Procedure

### Option A: Using the test script

```bash
# From your Mac (requires gcloud + gsutil configured)
cd /Users/haukurhauksson/SubtitleWorkflow

# Pick a short test audio (5-10 min)
python cloud/transcriber/test_cloud_transcriber.py run --audio /path/to/test.wav

# Compare with local skeleton
python cloud/transcriber/test_cloud_transcriber.py compare \
  --local /path/to/local_SKELETON.json \
  --cloud cloud_skeleton_TEST-xxx.json
```

### Option B: Manual test

```bash
# 1. Upload audio
gsutil cp /path/to/test.wav gs://omega-jobs-subtitle-project/jobs/CLOUD-TEST-001/audio.wav

# 2. Trigger transcription
gcloud run jobs execute omega-transcriber \
  --region us-central1 \
  --args="--job-id=CLOUD-TEST-001,--bucket=omega-jobs-subtitle-project,--prefix=jobs"

# 3. Monitor progress (in Cloud Console or via gsutil)
gsutil cat gs://omega-jobs-subtitle-project/jobs/CLOUD-TEST-001/transcription_progress.json

# 4. Download result
gsutil cp gs://omega-jobs-subtitle-project/jobs/CLOUD-TEST-001/skeleton.json ./cloud_skeleton.json
```

---

## Success Criteria

- ✅ Skeleton has same number of segments (±5%)
- ✅ 95%+ of timestamps within 100ms of local
- ✅ No missing words in first/last segments

---

## What's Next

After validation passes:
1. I'll add `OMEGA_CLOUD_TRANSCRIBE` config option
2. Modify `omega_manager.py` to use cloud transcription
3. Full pipeline: INBOX → Cloud Transcribe → Cloud Translate → Local Finalize
