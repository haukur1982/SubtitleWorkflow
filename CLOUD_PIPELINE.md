# Omega Cloud-First Pipeline (GCS + Cloud Worker)

This repo now supports an optional **cloud-first** translation + Chief Editor flow:

- Your Mac keeps doing: ingest, WhisperX, finalize/burn (video stays on the SSD).
- Google Cloud does: translation + Chief Editor, writing artifacts to **GCS**.
- `omega_manager.py` uploads the job to GCS and polls for `approved.json`.

## Prereqs (one-time)

### 1) GCS jobs bucket
- Create a bucket for job artifacts (example): `omega-jobs-subtitle-project`
- Location: `us-central1` (recommended)
- Uniform access: on

### 2) IAM permissions (service account)
Grant the worker identity access to the jobs bucket.

If you’re using the local key in this repo (`service_account.json`), the service account is:
- `ranslator-bot@sermon-translator-system.iam.gserviceaccount.com`

On the jobs bucket, grant:
- `roles/storage.objectAdmin`
- `roles/storage.bucketViewer` (fixes `storage.buckets.get` errors)

For Vertex access, ensure it has:
- `roles/aiplatform.user`

## How it works (artifact layout)

Each job lives under:

`gs://<OMEGA_JOBS_BUCKET>/<OMEGA_JOBS_PREFIX>/<job_id>/`

Artifacts:
- `job.json` (metadata: language/profile/model names)
- `skeleton.json` (WhisperX segments)
- `translation_checkpoint.json` (resume state)
- `translation_draft.json` (source + translated payload)
- `editor_report.json`
- `approved.json` (final segments + timestamps)
- `progress.json`

## Enable cloud mode (local manager)

Set env vars before starting:
- `OMEGA_CLOUD_PIPELINE=1`
- `OMEGA_JOBS_BUCKET=omega-jobs-subtitle-project` (or your bucket)
- `OMEGA_JOBS_PREFIX=jobs` (default)

Start normally: `sh start_omega.sh`

When a `_SKELETON.json` appears, the manager will:
1) Upload `job.json` + `skeleton.json` to GCS
2) Move the local skeleton to `_SKELETON_DONE.json`
3) Set DB stage to `TRANSLATING_CLOUD_SUBMITTED`
4) Poll for `approved.json` and download it to `3_TRANSLATED_DONE/<stem>_APPROVED.json`
5) Continue the existing `finalizer` → `publisher` steps

## Running the cloud worker (manual, for now)

You can run the worker locally against a GCS job (good for early testing):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$PWD/service_account.json"
python3 omega_cloud_worker.py --job-id "<job_id>" --bucket "$OMEGA_JOBS_BUCKET" --prefix "$OMEGA_JOBS_PREFIX"
```

You can find `<job_id>` in the dashboard job `meta.cloud_job_id`, or by listing the bucket prefix.

## Optional: auto-trigger command

If you want the manager to trigger a worker automatically, set:

`OMEGA_CLOUD_TRIGGER_COMMAND`

It supports placeholders:
- `{job_id}`
- `{bucket}`
- `{prefix}`

Example (local trigger; not recommended for production, but useful for debugging):

```bash
export OMEGA_CLOUD_TRIGGER_COMMAND='python3 omega_cloud_worker.py --job-id {job_id} --bucket {bucket} --prefix {prefix}'
```

## Recommended: auto-trigger via Cloud Run Jobs (no `gcloud` on the Mac)

If you deploy the worker as a Cloud Run Job, the local manager can trigger executions
directly via the Cloud Run API (using your `service_account.json`) — no manual steps per job.

### 1) Deploy Cloud Run Job (one-time)
Run these in **Cloud Shell** (recommended) or any environment with `gcloud`:

```bash
PROJECT_ID=sermon-translator-system
REGION=us-central1
IMAGE=gcr.io/$PROJECT_ID/omega-cloud-worker

gcloud config set project "$PROJECT_ID"

gcloud builds submit --file cloud/Dockerfile --tag "$IMAGE" .

gcloud run jobs create omega-cloud-worker \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account ranslator-bot@sermon-translator-system.iam.gserviceaccount.com \
  --set-env-vars OMEGA_JOBS_BUCKET=omega-jobs-subtitle-project,OMEGA_JOBS_PREFIX=jobs
```

### 2) Grant the local service account permission to run the job
In IAM, grant `ranslator-bot@sermon-translator-system.iam.gserviceaccount.com`:
- `roles/run.admin` (simplest) or at least a role that includes `run.jobs.run`.

### 3) Enable automatic triggering (local Mac)
Set:
- `OMEGA_CLOUD_RUN_JOB=omega-cloud-worker`
- `OMEGA_CLOUD_RUN_REGION=us-central1`
- (optional) `OMEGA_CLOUD_PROJECT=sermon-translator-system`

Then start: `OMEGA_CLOUD_PIPELINE=1 sh start_omega.sh`

## Deploying to Cloud Run Jobs (recommended)

Run the **container** in `us-central1` while still calling Vertex with `location="global"` (preview models).

### 1) Build image
Use the `cloud/Dockerfile` and `cloud/requirements.txt`.

Example:
```bash
gcloud builds submit --file cloud/Dockerfile --tag gcr.io/sermon-translator-system/omega-cloud-worker .
```

### 2) Create a Cloud Run Job
Attach a dedicated service account (recommended), for example `omega-cloud-worker@sermon-translator-system.iam.gserviceaccount.com`,
with:
- `roles/aiplatform.user`
- `roles/storage.objectAdmin` on the jobs bucket
- `roles/storage.bucketViewer` on the jobs bucket
- `roles/logging.logWriter`

Example:
```bash
gcloud run jobs create omega-cloud-worker \
  --image gcr.io/sermon-translator-system/omega-cloud-worker \
  --region us-central1 \
  --service-account omega-cloud-worker@sermon-translator-system.iam.gserviceaccount.com
```

### 3) Execute per job
```bash
gcloud run jobs execute omega-cloud-worker --region us-central1 --args="--job-id=<job_id>,--bucket=<bucket>,--prefix=<prefix>"
```

Then the local manager will detect `approved.json` and continue finalize/burn.
