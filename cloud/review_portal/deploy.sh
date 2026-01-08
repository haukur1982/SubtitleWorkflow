#!/bin/bash
# Deploy Omega Review Portal to Cloud Run

set -e

PROJECT_ID="${OMEGA_CLOUD_PROJECT:-sermon-translator-system}"
REGION="${OMEGA_CLOUD_RUN_REGION:-us-central1}"
SERVICE_NAME="omega-review"

echo "🚀 Deploying Omega Review Portal..."
echo "   Project: $PROJECT_ID"
echo "   Region: $REGION"
echo "   Service: $SERVICE_NAME"
echo ""

# Build and deploy
gcloud run deploy $SERVICE_NAME \
    --source . \
    --project $PROJECT_ID \
    --region $REGION \
    --allow-unauthenticated \
    --memory 256Mi \
    --cpu 1 \
    --max-instances 3 \
    --set-env-vars "OMEGA_JOBS_BUCKET=omega-jobs-subtitle-project,OMEGA_JOBS_PREFIX=jobs"

echo ""
echo "✅ Deployment complete!"
echo ""

# Get the URL
URL=$(gcloud run services describe $SERVICE_NAME --project $PROJECT_ID --region $REGION --format 'value(status.url)')
echo "🌐 Portal URL: $URL"
