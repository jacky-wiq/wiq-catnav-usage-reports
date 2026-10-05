"""Data access for the pipeline: BigQuery queries, Google Sheets reads, GCS uploads.

Two interchangeable backends, selected by CATNAV_BACKEND (see config.py):
  - "cli": `bq` / `gws` command-line tools on the person's own login (original behaviour)
  - "api": official Google client libraries using Application Default Credentials,
           i.e. the service account attached to the Cloud Run job

Both backends return identical shapes, so aggregate_daily.py / adoption.py don't care
which one produced their inputs:
  - query_to_csv  -> CSV text with a header row, NULLs as empty strings
  - query_to_rows -> list of dicts with every non-NULL value as a string (like `bq --format=json`)
  - sheet_values  -> {"values": [[...], ...]} (like the Sheets API values.get response)

Security notes:
  - All SQL here is fixed, pipeline-authored text with no external input interpolated,
    so there is no injection surface (CWE-89). If you ever add a user-supplied filter,
    use BigQuery query parameters, never string formatting.
  - No credentials are read or stored by this module (CWE-798).
  - Sheets/BigQuery access uses read-only scopes; only GCS upload needs write.
"""
import csv
import io
import json
import subprocess
import sys
import urllib.parse

import config

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def run(cmd, log_stdout=True):
    """Run a command (argument list, never a shell string — avoids shell injection) or exit."""
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if log_stdout:
        print(result.stdout[-2000:])
    if result.returncode != 0:
        print(result.stderr[-4000:], file=sys.stderr)
        hint = ""
        if "Reauthentication failed" in result.stderr or "gcloud auth login" in result.stderr:
            hint = " — your gcloud login has expired: run `gcloud auth login` and retry."
        sys.exit(f"FAILED: {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}{hint}")
    return result


def _stringify(value):
    return None if value is None else str(value)


# ---------- BigQuery ----------

def query_to_csv(sql, max_rows=500000):
    if config.BACKEND == "cli":
        r = run(["bq", "query", "--use_legacy_sql=false", f"--project_id={config.BQ_PROJECT}",
                 "--format=csv", f"--max_rows={max_rows}", sql], log_stdout=False)
        return r.stdout
    from google.cloud import bigquery  # imported lazily so the CLI path needs no extra packages
    client = bigquery.Client(project=config.BQ_PROJECT)
    result = client.query(sql).result()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([field.name for field in result.schema])
    for row in result:
        writer.writerow(["" if v is None else v for v in row.values()])
    return buf.getvalue()


def query_to_rows(sql, max_rows=20000):
    if config.BACKEND == "cli":
        r = run(["bq", "query", "--use_legacy_sql=false", f"--project_id={config.BQ_PROJECT}",
                 "--format=json", f"--max_rows={max_rows}", sql])
        rows = json.loads(r.stdout)
    else:
        from google.cloud import bigquery
        client = bigquery.Client(project=config.BQ_PROJECT)
        rows = [{k: _stringify(v) for k, v in row.items()} for row in client.query(sql).result()]
    if len(rows) >= max_rows:
        # The bq CLI silently truncates at --max_rows; fail loudly instead of building on partial data.
        sys.exit(f"Query returned {len(rows)} rows (limit {max_rows}). Raise max_rows in the caller.")
    return rows


# ---------- Google Sheets ----------

def sheet_values(spreadsheet_id, range_):
    if config.BACKEND == "cli":
        params = json.dumps({"spreadsheetId": spreadsheet_id, "range": range_})
        r = run(["gws", "sheets", "spreadsheets", "values", "get", "--params", params], log_stdout=False)
        text = r.stdout
        return json.loads(text[text.index("{"):])  # skip the "Using keyring backend" banner
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=[SHEETS_READONLY_SCOPE])
    session = AuthorizedSession(credentials)
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{urllib.parse.quote(spreadsheet_id, safe='')}"
           f"/values/{urllib.parse.quote(range_, safe='')}")
    resp = session.get(url, timeout=60)
    if resp.status_code == 403:
        sys.exit(f"Sheets returned 403 for {spreadsheet_id}. Share the sheet (Viewer) with the "
                 f"service account's email address, then retry.")
    resp.raise_for_status()
    return resp.json()


# ---------- GCS upload ----------

def upload_to_gcs(local_path, object_name):
    """Upload a finished page to the private bucket in CATNAV_GCS_BUCKET (no-op if unset)."""
    if not config.GCS_BUCKET:
        return
    from google.cloud import storage
    blob = storage.Client().bucket(config.GCS_BUCKET).blob(object_name)
    # Pages are personal-data reports: never cache in shared/proxy caches.
    blob.cache_control = "private, no-cache"
    blob.upload_from_filename(local_path, content_type="text/html; charset=utf-8")
    print(f"Uploaded gs://{config.GCS_BUCKET}/{object_name}")


def download_from_gcs(object_name, local_path):
    """Fetch a private config object (e.g. leave notes) from the bucket. Returns False if absent/unset."""
    if not config.GCS_BUCKET:
        return False
    from google.cloud import storage
    blob = storage.Client().bucket(config.GCS_BUCKET).blob(object_name)
    if not blob.exists():
        print(f"gs://{config.GCS_BUCKET}/{object_name} not found — skipping")
        return False
    blob.download_to_filename(local_path)
    return True
