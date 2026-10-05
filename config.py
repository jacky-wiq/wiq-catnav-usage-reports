"""Shared settings for the CatNav Compass + Adoption pipeline.

Everything that differs between "running on a laptop/devcontainer" and "running as a
scheduled Cloud Run job" is read from environment variables here, with defaults that
reproduce the original local behaviour exactly.

Security: no credentials live in this file or anywhere in the repo. Authentication is
always ambient — your own `gcloud`/`gws` login locally, or the Cloud Run job's attached
service account (Application Default Credentials) in the cloud.
"""
import os

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where raw pulls + intermediate JSON are written. Contains personal data (names, emails,
# phone numbers) — must never be committed (see .gitignore) or written anywhere public.
DATA_DIR = os.environ.get("CATNAV_DATA_DIR", PIPELINE_DIR)

# Where the two finished HTML pages are written.
OUTPUT_DIR = os.environ.get("CATNAV_OUTPUT_DIR", PIPELINE_DIR)

# Optional extra copy of each page (the shareable local copies in /workspace).
# Set to an empty string to disable (the Cloud Run image does).
SHARE_COPY_DIR = os.environ.get("CATNAV_SHARE_COPY_DIR", "/workspace")

# "cli"  = shell out to `bq` and `gws` using the person's own logins (default, local use).
# "api"  = Python client libraries + Application Default Credentials (Cloud Run / service account).
BACKEND = os.environ.get("CATNAV_BACKEND", "cli")
if BACKEND not in ("cli", "api"):
    raise SystemExit(
        f"CATNAV_BACKEND='{BACKEND}' is not valid. Use 'cli' (local bq/gws logins) "
        f"or 'api' (service account / Application Default Credentials)."
    )

# Optional: private GCS bucket the finished pages are uploaded to after each run.
# Empty = don't upload. The bucket must NOT be public — pages contain named individuals.
GCS_BUCKET = os.environ.get("CATNAV_GCS_BUCKET", "")

BQ_PROJECT = os.environ.get("CATNAV_BQ_PROJECT", "gcp-wow-wiq-kscope-prod")

# Google Sheets (IDs are not secrets — access is controlled by Sheets sharing).
PORTFOLIO_MAPPING_SHEET_ID = "1cRp90r5ziSHP9SRguGbz2gzznQSJ61oat41qGXLbqmw"  # CatNav Users and Portfolios
COMMERCIAL_TEAM_SHEET_ID = "1EVhLqKY6YIfdPUOCSnc_P7HVqdADss8CSkVptr_-Nmc"    # Commercial Team directory

# Inputs / intermediates
CSV_PATH = os.path.join(DATA_DIR, "fact_user_events.csv")
DATA_PATH = os.path.join(DATA_DIR, "daily_data.json")
AU_PORTFOLIOS_PATH = os.path.join(DATA_DIR, "au_portfolios.json")
AU_USERS_PATH = os.path.join(DATA_DIR, "au_users.json")
USAGE_BY_USER_PATH = os.path.join(DATA_DIR, "usage_by_user_raw.json")
PSEUDO_USERS_PATH = os.path.join(DATA_DIR, "dim_pseudo_users.json")
COMMERCIAL_TEAM_PATH = os.path.join(DATA_DIR, "commercial_team_raw.txt")
# Hand-maintained leave annotations for the Adoption page (HR info: git-ignored).
# Locally it sits in DATA_DIR; on Cloud Run it's fetched from gs://$CATNAV_GCS_BUCKET/config/leave_notes.json.
LEAVE_NOTES_PATH = os.path.join(DATA_DIR, "leave_notes.json")
LEAVE_NOTES_GCS_OBJECT = "config/leave_notes.json"

# Templates (checked in) and outputs
COMPASS_TEMPLATE_PATH = os.path.join(PIPELINE_DIR, "catnav_compass_template.html")
ADOPTION_TEMPLATE_PATH = os.path.join(PIPELINE_DIR, "catnav_adoption_template.html")
COMPASS_OUTPUT_PATH = os.path.join(OUTPUT_DIR, "catnav_compass_output.html")
ADOPTION_OUTPUT_PATH = os.path.join(OUTPUT_DIR, "catnav_adoption_output.html")


def share_copy_path(filename):
    """Path for the extra shareable copy, or None when disabled."""
    return os.path.join(SHARE_COPY_DIR, filename) if SHARE_COPY_DIR else None
