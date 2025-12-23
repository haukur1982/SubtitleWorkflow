#!/bin/bash
# deploy_transcriber.sh — Deploy the GPU transcriber to Cloud Run
#
# Run this from Cloud Shell or a machine with gcloud configured.
# Usage: ./cloud/transcriber/deploy_transcriber.sh

set -e

PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
SERVICE_ACCOUNT="${OMEGA_SERVICE_ACCOUNT:-ranslator-bot@sermon-translator-system.iam.gserviceaccount.com}"

echo "=== Omega Cloud Transcriber Deployment ==="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Service Account: $SERVICE_ACCOUNT"
echo ""

# Step 1: Build the Docker image
echo "📦 Building Docker image (this may take 10-15 minutes for first build)..."
gcloud builds submit \
  --project "$PROJECT_ID" \
  --config cloud/transcriber/cloudbuild.yaml \
  .

# Step 2: Create the Cloud Run Job with GPU
echo ""
echo "🚀 Creating Cloud Run Job with GPU..."

# Check if job already exists
if gcloud run jobs describe omega-transcriber --region "$REGION" --project "$PROJECT_ID" &>/dev/null; then
  echo "   Job exists, updating..."
  gcloud run jobs update omega-transcriber \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "gcr.io/$PROJECT_ID/omega-transcriber" \
    --gpu 1 \
    --gpu-type nvidia-l4 \
    --cpu 8 \
    --memory 32Gi \
    --max-retries 1 \
    --task-timeout 60m \
    --service-account "$SERVICE_ACCOUNT" \
    --set-env-vars "OMEGA_WHISPER_MODEL=large-v3"
else
  echo "   Creating new job..."
  gcloud run jobs create omega-transcriber \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "gcr.io/$PROJECT_ID/omega-transcriber" \
    --gpu 1 \
    --gpu-type nvidia-l4 \
    --cpu 8 \
    --memory 32Gi \
    --max-retries 1 \
    --task-timeout 60m \
    --service-account "$SERVICE_ACCOUNT" \
    --set-env-vars "OMEGA_WHISPER_MODEL=large-v3"
fi

echo ""
echo "✅ Deployment complete!"
echo ""
echo "To test, run:"
echo "  gsutil cp <audio.wav> gs://omega-jobs-subtitle-project/jobs/TEST-001/audio.wav"
echo "  gcloud run jobs execute omega-transcriber --region $REGION --args='--job-id=TEST-001,--bucket=omega-jobs-subtitle-project,--prefix=jobs'"
