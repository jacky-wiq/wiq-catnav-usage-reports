#!/usr/bin/env bash
# One-off setup: run the CatNav refresh as a Cloud Run job every Monday 9am Sydney time.
#
# Run by someone with deploy rights on the hosting project (likely the WIQ data/platform
# team). Read each step before running — it creates a service account, a bucket and IAM
# bindings. Nothing here contains or creates a credential file: the job authenticates as
# its attached service account.
#
# Usage:  PROJECT_ID=... BUCKET=... VIEWER_GROUP=group:catnav-owners@... ./deploy/deploy.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to the GCP project that will host the job}"
: "${BUCKET:?Set BUCKET to a new private bucket name, e.g. wiq-catnav-reports}"
: "${VIEWER_GROUP:?Set VIEWER_GROUP, e.g. group:catnav-viewers@woolworths.com.au}"
REGION="${REGION:-australia-southeast1}"
DATA_PROJECT="${DATA_PROJECT:-gcp-wow-wiq-kscope-prod}"
JOB_NAME="${JOB_NAME:-catnav-refresh}"
SA_NAME="${SA_NAME:-catnav-refresh}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."

echo "== 0. Turn on the Google Cloud services this setup uses (no-op if already on)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com bigquery.googleapis.com storage.googleapis.com \
  sheets.googleapis.com iam.googleapis.com --project "$PROJECT_ID"

echo "== 1. Service account the job runs as"
gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" \
  --display-name "CatNav Compass/Adoption weekly refresh" || echo "(already exists)"

echo "== 2. Read-only BigQuery access to the CatNav tables (least privilege)"
# Run query jobs in the data project (refresh.py queries with project_id=$DATA_PROJECT).
gcloud projects add-iam-policy-binding "$DATA_PROJECT" \
  --member "serviceAccount:$SA_EMAIL" --role roles/bigquery.jobUser --condition=None
# Read the three source tables only — table-level, not the whole dataset.
for TABLE in fact_user_events dim_pseudo_users usage_by_user; do
  bq add-iam-policy-binding --member "serviceAccount:$SA_EMAIL" \
    --role roles/bigquery.dataViewer "${DATA_PROJECT}:prod.${TABLE}"
done

echo "== 3. Private bucket for the finished pages"
# Security: uniform access + public access prevention — pages list named individuals.
gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT_ID" --location "$REGION" \
  --uniform-bucket-level-access --public-access-prevention || echo "(already exists)"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$SA_EMAIL" --role roles/storage.objectAdmin
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "$VIEWER_GROUP" --role roles/storage.objectViewer

echo "== 4. Build + deploy the Cloud Run job (builds the Dockerfile via Cloud Build)"
gcloud run jobs deploy "$JOB_NAME" --project "$PROJECT_ID" --region "$REGION" \
  --source . \
  --service-account "$SA_EMAIL" \
  --set-env-vars "CATNAV_GCS_BUCKET=$BUCKET,CATNAV_BQ_PROJECT=$DATA_PROJECT" \
  --memory 2Gi --cpu 1 --task-timeout 30m --max-retries 1

echo "== 5. Weekly trigger: Mondays 09:00 Australia/Sydney"
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --project "$PROJECT_ID" --region "$REGION" \
  --member "serviceAccount:$SA_EMAIL" --role roles/run.invoker
gcloud scheduler jobs create http "${JOB_NAME}-weekly" --project "$PROJECT_ID" --location "$REGION" \
  --schedule "0 9 * * 1" --time-zone "Australia/Sydney" \
  --http-method POST \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run" \
  --oauth-service-account-email "$SA_EMAIL" || echo "(scheduler job already exists)"

cat <<EOF

== Done. Manual steps left:
  a) Share BOTH Google Sheets with $SA_EMAIL as Viewer:
       - CatNav Users and Portfolios  (1cRp90r5ziSHP9SRguGbz2gzznQSJ61oat41qGXLbqmw)
       - Commercial Team directory    (1EVhLqKY6YIfdPUOCSnc_P7HVqdADss8CSkVptr_-Nmc)
  b) Upload the (git-ignored) leave notes, if you use them:
       gcloud storage cp leave_notes.json gs://$BUCKET/config/leave_notes.json
  c) Test it now:  gcloud run jobs execute $JOB_NAME --project $PROJECT_ID --region $REGION --wait

Pages (signed-in Google users in $VIEWER_GROUP only):
  https://storage.cloud.google.com/$BUCKET/catnav_compass.html
  https://storage.cloud.google.com/$BUCKET/catnav_adoption.html
EOF
