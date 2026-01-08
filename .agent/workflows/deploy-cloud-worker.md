---
description: Deploy updated code to Cloud Run omega-cloud-worker
---
# Deploy Cloud Worker

## 📋 PRE-DEPLOY CHECKLIST (MANDATORY)
Before deploying, verify ALL of the following:

### 1. Dockerfile includes all required files
```bash
grep -E "^COPY" /Users/haukurhauksson/SubtitleWorkflow/Dockerfile
```
Confirm that ALL new Python files and directories (especially `providers/`) are copied.

### 2. Imports resolve locally
```bash
cd /Users/haukurhauksson/SubtitleWorkflow && python3 -c "from providers.anthropic_claude import polish_with_claude; print('✅ Import OK')"
```

### 3. Environment variables set in Cloud Run
```bash
gcloud run jobs describe omega-cloud-worker --project=sermon-translator-system --region=us-central1 --format="yaml(spec.template.spec.containers[0].env)"
```
Verify: `OMEGA_CLAUDE_POLISH=1`, `ANTHROPIC_API_KEY` present, `OMEGA_CLAUDE_MODEL` correct.

### 4. Run verify_pipeline.py
```bash
cd /Users/haukurhauksson/SubtitleWorkflow && python3 verify_pipeline.py --component anthropic
```

---

## 🚀 DEPLOY STEPS

// turbo-all

1. Build the Docker image using Cloud Build:
```bash
cd /Users/haukurhauksson/SubtitleWorkflow
gcloud builds submit --project=sermon-translator-system --region=us-central1 --tag gcr.io/sermon-translator-system/omega-cloud-worker:latest --gcs-source-staging-dir=gs://omega-jobs-subtitle-project/cloud-build-staging .
```

2. Update the Cloud Run Job to use the new image:
```bash
gcloud run jobs update omega-cloud-worker --image gcr.io/sermon-translator-system/omega-cloud-worker:latest --project=sermon-translator-system --region=us-central1
```

3. Verify deployment:
```bash
gcloud run jobs describe omega-cloud-worker --project=sermon-translator-system --region=us-central1 --format="value(spec.template.spec.containers[0].image)"
```
