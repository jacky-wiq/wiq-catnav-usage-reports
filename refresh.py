"""Weekly refresh for CatNav Compass + CatNav Adoption.

Pulls fresh data (BigQuery + two Google Sheets), rebuilds both HTML pages, and
optionally uploads them to a private GCS bucket. See README.md for setup.

    python3 refresh.py                      # local: uses your own bq/gws logins
    CATNAV_BACKEND=api python3 refresh.py   # service account / Cloud Run
"""
import json
import os
import shutil
import sys

import config
import sources

FACT_MAX_ROWS = 500000


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def pull_fact_user_events():
    # NOTE: gs://ga_user_tracking_catnav/fact_user_events (the old source) is a
    # periodic export that has been observed to lag the live table by weeks.
    # Query the BigQuery table directly instead so refreshes are never stale.
    sql = (
        "SELECT event_id, event_date, "
        "FORMAT_DATETIME('%Y-%m-%d %H:%M:%E6S', event_dt) AS event_dt, "
        "FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%E6S UTC', event_dt_utc) AS event_dt_utc, "
        "user_id, session_id, batch_page_id, batch_ordering_id, batch_event_index, "
        "event, module, var_title, var_value, user_portfolio, engagement_time_sec "
        f"FROM `{config.BQ_PROJECT}.prod.fact_user_events` ORDER BY event_id"
    )
    csv_text = sources.query_to_csv(sql, max_rows=FACT_MAX_ROWS)
    n_rows = csv_text.count("\n") - 1
    print(f"fact_user_events: {n_rows} rows")
    if n_rows >= FACT_MAX_ROWS:
        # The bq CLI silently truncates at --max_rows; refuse to build a partial dashboard.
        sys.exit(f"fact_user_events hit the {FACT_MAX_ROWS}-row cap — raise FACT_MAX_ROWS in refresh.py.")
    with open(config.CSV_PATH, "w") as f:
        f.write(csv_text)


def pull_pseudo_users():
    # dim_pseudo_users carries a probabilistic user_id -> email inference (built by
    # the data team from behavioral signals like "View by: <name>" filter usage), not
    # an authenticated identity binding. Scope the pull to ids actually present in the
    # current fact_user_events window to keep this small and always in sync with it.
    sql = (
        "SELECT DISTINCT d.pseudo_user_id, d.user_name, d.user_email_inferred, "
        "d.user_email_confidence, d.user_email_matches "
        f"FROM `{config.BQ_PROJECT}.prod.dim_pseudo_users` d "
        "WHERE d.pseudo_user_id IN ("
        f"SELECT DISTINCT user_id FROM `{config.BQ_PROJECT}.prod.fact_user_events`)"
    )
    write_json(config.PSEUDO_USERS_PATH, sources.query_to_rows(sql))


def pull_usage_by_user():
    sql = (
        "SELECT user_email, user_name, user_role, portfolio_name, fiscal_week_end_dt, "
        "CAST(days_active_in_week AS INT64) as days_active "
        f"FROM `{config.BQ_PROJECT}.prod.usage_by_user` "
        "WHERE portfolio_name IS NOT NULL AND CAST(days_active_in_week AS INT64) > 0"
    )
    write_json(config.USAGE_BY_USER_PATH, sources.query_to_rows(sql, max_rows=50000))


def pull_portfolio_mapping():
    sheet = config.PORTFOLIO_MAPPING_SHEET_ID
    write_json(config.AU_PORTFOLIOS_PATH, sources.sheet_values(sheet, "'AU Portfolios'!A1:E200"))
    write_json(config.AU_USERS_PATH, sources.sheet_values(sheet, "'AU Users'!A1:H2000"))


def build_compass():
    sources.run([sys.executable, os.path.join(config.PIPELINE_DIR, "aggregate_daily.py")])
    with open(config.DATA_PATH) as f:
        new_data = f.read()
    json.loads(new_data)  # sanity check: aggregation produced valid JSON

    with open(config.COMPASS_TEMPLATE_PATH) as f:
        template = f.read()
    output = template.replace("var DATA = __DASHBOARD_DATA__;", "var DATA = " + new_data + ";")
    assert "__DASHBOARD_DATA__" not in output, "Compass template placeholder was not replaced"
    with open(config.COMPASS_OUTPUT_PATH, "w") as f:
        f.write(output)
    print(f"Compass written: {config.COMPASS_OUTPUT_PATH} ({os.path.getsize(config.COMPASS_OUTPUT_PATH)} bytes)")


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    print(f"Backend: {config.BACKEND} · data dir: {config.DATA_DIR} · output dir: {config.OUTPUT_DIR}")

    pull_fact_user_events()
    pull_pseudo_users()
    pull_portfolio_mapping()
    pull_usage_by_user()

    build_compass()
    if config.BACKEND == "api":
        sources.download_from_gcs(config.LEAVE_NOTES_GCS_OBJECT, config.LEAVE_NOTES_PATH)
    # Adoption reuses the usage_by_user pull above (and re-pulls the Commercial Team roster).
    sources.run([sys.executable, os.path.join(config.PIPELINE_DIR, "adoption.py")])

    for output_path, filename in ((config.COMPASS_OUTPUT_PATH, "catnav_compass.html"),
                                  (config.ADOPTION_OUTPUT_PATH, "catnav_adoption.html")):
        copy = config.share_copy_path(filename)
        if copy:
            shutil.copyfile(output_path, copy)
            print(f"Copied to {copy}")
        sources.upload_to_gcs(output_path, filename)

    print("\nDONE.")


if __name__ == "__main__":
    main()
